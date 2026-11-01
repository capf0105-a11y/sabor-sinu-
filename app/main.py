"""Punto de entrada HTTP: sirve la web y expone la API del chatbot."""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .emailer import send_order_email
from .notifier import send_whatsapp_alert
from .engine import ChatEngine
from .menu import PRODUCTS, PRODUCTS_BY_ID, ZONES
from .orders import PAYMENT_LABELS, OrderStore, admin_order, public_order
from .schemas import AdminLoginIn, CartIn, ChatIn, StockIn
from .sessions import SessionStore, cart_public
from .stock import StockStore
from .utils import LoginGuard, RateLimiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("saborsinu")

settings.ensure_dirs()
store = OrderStore(settings.orders_file)
stock = StockStore(
    settings.data_dir / "stock.json",
    settings.upstash_redis_rest_url,
    settings.upstash_redis_rest_token,
)
sessions = SessionStore(settings.session_ttl_minutes)
engine = ChatEngine(settings, store, stock)
limiter = RateLimiter(settings.rate_limit_per_minute)
login_guard = LoginGuard(max_attempts=5, window_seconds=900, block_seconds=900)

ADMIN_COOKIE_NAME = "sinu_admin_session"
ADMIN_SESSION_TTL = max(1, settings.admin_session_hours) * 3600
_admin_sessions: dict[str, float] = {}
_admin_sessions_lock = threading.Lock()

PUBLIC_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
ADMIN_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "%s iniciado · %d productos · correo: %s",
        settings.business_name,
        len(PRODUCTS),
        "SMTP" if settings.email_configured else "modo demo (data/outbox)",
    )
    yield


_PRODUCTION = settings.environment == "production"
app = FastAPI(
    title=f"{settings.business_name} · API del asistente",
    description="Chatbot de ventas, carrito y notificaciones por correo.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if _PRODUCTION else "/docs",
    redoc_url=None if _PRODUCTION else "/redoc",
    openapi_url=None if _PRODUCTION else "/openapi.json",
)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Admin-Token"],
    )

# Comprime respuestas grandes (HTML, CSS, JS y JSON) para gastar menos ancho
# de banda y responder más rápido en conexiones móviles.
app.add_middleware(GZipMiddleware, minimum_size=500)


def _client_ip(request: Request) -> str:
    """IP real detrás de Cloudflare/Render, con respaldo local."""
    cloudflare_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cloudflare_ip:
        return cloudflare_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else "desconocido"


def _admin_path() -> str:
    path = (settings.admin_panel_path or "/admin").strip()
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/admin"


def _same_origin(request: Request) -> bool:
    """True si una petición de navegador viene del mismo host.

    Los clientes de automatización (smoke test) no envían Origin y siguen
    funcionando con el token de cabecera.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = request.headers.get("host", "")
    try:
        return urlparse(origin).netloc == host
    except ValueError:
        return False


def _apply_security_headers(response, path: str) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if _PRODUCTION:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    admin_path = _admin_path()
    if path == admin_path or path.startswith("/api/admin/"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        response.headers.setdefault("Content-Security-Policy", ADMIN_CSP)
    else:
        response.headers.setdefault("Content-Security-Policy", PUBLIC_CSP)


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    path = request.url.path
    response = None

    if path.startswith("/api/") and path != "/api/health":
        ip = _client_ip(request)
        if not limiter.allow(ip):
            response = JSONResponse(
                status_code=429,
                content={"detail": "Demasiadas solicitudes. Espera un momento e intenta de nuevo."},
                headers={"Retry-After": "30"},
            )

    if (
        response is None
        and path.startswith("/api/")
        and request.method in {"POST", "PUT", "PATCH"}
    ):
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                length = int(raw_length)
            except ValueError:
                response = JSONResponse(
                    status_code=400, content={"detail": "Solicitud inválida."}
                )
            else:
                if length > 64_000:
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "La solicitud es demasiado grande."},
                    )

    if response is None:
        response = await call_next(request)

    if path.startswith("/static/"):
        if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".svg", ".ico")):
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
    elif path == "/api/config":
        # La configuración cambia poco; 5 minutos de caché alivia visitas repetidas.
        response.headers.setdefault("Cache-Control", "public, max-age=300")

    _apply_security_headers(response, path)
    return response


def _admin_ip_allowed(request: Request) -> bool:
    allowed = settings.admin_allowed_ips
    return not allowed or _client_ip(request) in allowed


def _cleanup_admin_sessions(now: float | None = None) -> None:
    current = now if now is not None else time.time()
    with _admin_sessions_lock:
        expired = [token for token, until in _admin_sessions.items() if until <= current]
        for token in expired:
            _admin_sessions.pop(token, None)


def _create_admin_session() -> str:
    token = secrets.token_urlsafe(32)
    with _admin_sessions_lock:
        _admin_sessions[token] = time.time() + ADMIN_SESSION_TTL
    return token


def _valid_admin_session(token: str | None) -> bool:
    if not token:
        return False
    current = time.time()
    with _admin_sessions_lock:
        until = _admin_sessions.get(token)
        if not until:
            return False
        if until <= current:
            _admin_sessions.pop(token, None)
            return False
        return True


def require_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> None:
    if not settings.admin_token:
        raise HTTPException(
            status_code=403,
            detail="Panel administrativo deshabilitado. Define ADMIN_TOKEN en el entorno.",
        )
    if not _admin_ip_allowed(request):
        raise HTTPException(status_code=403, detail="Acceso no permitido desde esta IP.")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _same_origin(request):
        raise HTTPException(status_code=403, detail="Origen no permitido.")
    ip = _client_ip(request)
    if x_admin_token and login_guard.is_blocked(ip):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos fallidos. Espera 15 minutos e intenta de nuevo.",
            headers={"Retry-After": "900"},
        )
    header_ok = bool(x_admin_token) and secrets.compare_digest(
        x_admin_token.encode("utf-8"), settings.admin_token.encode("utf-8")
    )
    cookie_ok = _valid_admin_session(request.cookies.get(ADMIN_COOKIE_NAME))
    if not header_ok and not cookie_ok:
        if x_admin_token:
            login_guard.register_failure(ip)
            logger.warning("Token admin inválido desde %s", ip)
        raise HTTPException(status_code=401, detail="Token administrativo inválido.")


@app.post("/api/admin/login")
async def admin_login(
    payload: AdminLoginIn,
    request: Request,
    response: Response,
) -> dict:
    if not settings.admin_token:
        raise HTTPException(
            status_code=403,
            detail="Panel administrativo deshabilitado. Define ADMIN_TOKEN en el entorno.",
        )
    if not _admin_ip_allowed(request):
        raise HTTPException(status_code=403, detail="Acceso no permitido desde esta IP.")
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Origen no permitido.")
    ip = _client_ip(request)
    if login_guard.is_blocked(ip):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos fallidos. Espera 15 minutos e intenta de nuevo.",
            headers={"Retry-After": "900"},
        )
    if not secrets.compare_digest(
        payload.password.encode("utf-8"), settings.admin_token.encode("utf-8")
    ):
        login_guard.register_failure(ip)
        logger.warning("Intento de acceso admin fallido desde %s", ip)
        raise HTTPException(status_code=401, detail="Contraseña incorrecta.")
    login_guard.reset(ip)
    _cleanup_admin_sessions()
    token = _create_admin_session()
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        max_age=ADMIN_SESSION_TTL,
        httponly=True,
        secure=_PRODUCTION,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    logger.info("Acceso admin correcto desde %s", ip)
    return {"ok": True, "expires_in": ADMIN_SESSION_TTL}


@app.post("/api/admin/logout")
async def admin_logout(request: Request, response: Response) -> dict:
    if not _same_origin(request):
        raise HTTPException(status_code=403, detail="Origen no permitido.")
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if token:
        with _admin_sessions_lock:
            _admin_sessions.pop(token, None)
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


def _notify_order(order_id: str) -> None:
    """Tarea en segundo plano: envía el correo y registra el resultado."""
    order = store.get(order_id)
    if not order:
        logger.error("No se encontró el pedido %s para notificar", order_id)
        return
    result = send_order_email(order, settings)
    store.set_email_recipients(order_id, list(result.get("recipients", [])))
    store.update_email(order_id, result["status"], result.get("error"))
    logger.info(
        "Notificación pedido %s: %s%s",
        order_id,
        result["status"],
        f" · {result.get('error')}" if result.get("error") else "",
    )
    whatsapp = send_whatsapp_alert(order, settings)
    logger.info("WhatsApp pedido %s: %s", order_id, whatsapp["status"])


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        settings.static_dir / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(settings.static_dir / "img" / "favicon.svg", media_type="image/svg+xml")


ADMIN_PANEL_PATH = _admin_path()


@app.get(ADMIN_PANEL_PATH, include_in_schema=False)
async def admin_page() -> FileResponse:
    return FileResponse(
        settings.static_dir / "admin.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@app.get("/credits", include_in_schema=False)
async def credits_page() -> FileResponse:
    return FileResponse(settings.static_dir / "credits.html", media_type="text/html")


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "business": settings.business_name,
        "orders": store.count(),
        "email_mode": "smtp" if settings.email_configured else "dry_run",
        "environment": settings.environment,
        "commit": os.getenv("RENDER_GIT_COMMIT", "dev"),
    }


@app.get("/health", include_in_schema=False)
async def health_alias() -> dict:
    return await health()


@app.get("/api/config")
async def get_config() -> dict:
    return {
        "brand": {
            "name": settings.business_name,
            "tagline": settings.business_tagline,
            "instagram": settings.business_instagram,
        },
        "contact": {
            "phone": settings.business_phone,
            "phone_link": settings.phone_link,
            "whatsapp": settings.whatsapp_link,
            "nequi": settings.business_nequi,
            "llave": settings.business_llave,
            "address": settings.business_address,
            "hours": settings.business_hours,
            "maps": settings.maps_link,
        },
        "delivery": {
            "base_fee": settings.delivery_base_fee,
            "free_from": settings.free_delivery_from,
            "prep_minutes": settings.prep_minutes,
            "delivery_minutes": settings.delivery_minutes,
            "eta_minutes": settings.prep_minutes + settings.delivery_minutes,
            "zones": [zone.public() for zone in ZONES],
        },
        "payments": [{"id": key, "label": label} for key, label in PAYMENT_LABELS.items()],
        "features": {
            "email_configured": settings.email_configured,
            "whatsapp_configured": settings.whatsapp_alerts_enabled,
            "max_message_chars": settings.max_message_chars,
        },
    }


@app.get("/api/menu")
async def get_menu() -> dict:
    return {
        "items": [
            {**product.public(), "available": stock.is_available(product.id)}
            for product in PRODUCTS
        ],
        "categories": ["Principales", "Bebidas"],
        "currency": "COP",
    }


@app.post("/api/chat")
async def chat(payload: ChatIn, background: BackgroundTasks) -> dict:
    sessions.cleanup()
    session = sessions.get_or_create(payload.session_id)
    result = engine.handle(session, payload.message)
    notify_id = result.pop("_notify_order_id", None)
    if notify_id:
        background.add_task(_notify_order, notify_id)
    return result


@app.post("/api/cart")
async def cart(payload: CartIn) -> dict:
    sessions.cleanup()
    session = sessions.get_or_create(payload.session_id)
    try:
        snapshot = engine.apply_cart_action(
            session, payload.action, payload.product_id, payload.uid, payload.qty
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_id": session.id, "cart": snapshot, "stage": session.stage}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada o expirada.")
    return {
        "session_id": session.id,
        "stage": session.stage,
        "cart": cart_public(session.cart, session.customer.get("barrio"), settings),
        "last_order_id": session.last_order_id,
        "resume": engine.resume(session),
    }


@app.post("/api/session/{session_id}/reset")
async def reset_session(session_id: str) -> dict:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada o expirada.")
    sessions.reset(session)
    return {
        "session_id": session.id,
        "stage": session.stage,
        "cart": cart_public(session.cart, None, settings),
    }


@app.get("/api/orders/{order_id}")
async def get_order(order_id: str) -> dict:
    order = store.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado. Verifica el código.")
    return {"order": public_order(order)}


# ---------------------------------------------------------------------------
# Administración (protegida por token)
# ---------------------------------------------------------------------------


@app.get("/api/admin/orders", dependencies=[Depends(require_admin)])
async def admin_orders(limit: int = Query(default=30, ge=1, le=200)) -> dict:
    return {
        "orders": [admin_order(order) for order in store.list_recent(limit)],
        "count": store.count(),
    }


@app.post("/api/admin/orders/{order_id}/resend-email", dependencies=[Depends(require_admin)])
async def resend_email(order_id: str, background: BackgroundTasks) -> dict:
    order = store.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    background.add_task(_notify_order, order["id"])
    return {"status": "queued", "order_id": order["id"]}


@app.get("/api/admin/stock", dependencies=[Depends(require_admin)])
async def admin_stock() -> dict:
    return {
        "persistent": stock.persistent,
        "items": [
            {
                "id": product.id,
                "name": product.name,
                "price": product.price,
                "category": product.category,
                "image": product.image,
                "available": stock.is_available(product.id),
            }
            for product in PRODUCTS
        ]
    }


@app.put("/api/admin/stock/{product_id}", dependencies=[Depends(require_admin)])
async def set_stock(product_id: str, payload: StockIn) -> dict:
    product = PRODUCTS_BY_ID.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    stock.set_available(product.id, payload.available)
    return {
        "id": product.id,
        "name": product.name,
        "available": stock.is_available(product.id),
    }


# ---------------------------------------------------------------------------
# Errores y estáticos
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    logger.exception("Error no controlado en %s", request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={"detail": "Ocurrió un error interno. Intenta de nuevo en unos segundos."},
        )
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})


if settings.static_dir.exists():
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
