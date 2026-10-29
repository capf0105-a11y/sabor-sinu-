"""Punto de entrada HTTP: sirve la web y expone la API del chatbot."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .emailer import send_order_email
from .notifier import send_whatsapp_alert
from .engine import ChatEngine
from .menu import PRODUCTS, PRODUCTS_BY_ID, ZONES
from .orders import PAYMENT_LABELS, OrderStore, admin_order, public_order
from .schemas import CartIn, ChatIn, StockIn
from .sessions import SessionStore, cart_public
from .stock import StockStore
from .utils import RateLimiter

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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "%s iniciado · %d productos · correo: %s",
        settings.business_name,
        len(PRODUCTS),
        "SMTP" if settings.email_configured else "modo demo (data/outbox)",
    )
    yield


app = FastAPI(
    title=f"{settings.business_name} · API del asistente",
    description="Chatbot de ventas, carrito y notificaciones por correo.",
    version="1.0.0",
    lifespan=lifespan,
)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Admin-Token"],
    )


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/health":
        ip = request.client.host if request.client else "desconocido"
        if not limiter.allow(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Demasiadas solicitudes. Espera un momento e intenta de nuevo."},
                headers={"Retry-After": "30"},
            )
    response = await call_next(request)
    if path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    return response


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        raise HTTPException(
            status_code=403,
            detail="Panel administrativo deshabilitado. Define ADMIN_TOKEN en el entorno.",
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Token administrativo inválido.")


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


@app.get("/admin", include_in_schema=False)
async def admin_page() -> FileResponse:
    return FileResponse(
        settings.static_dir / "admin.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
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
