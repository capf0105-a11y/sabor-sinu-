"""Pruebas de humo contra el sitio en producción.

Se ejecuta desde GitHub Actions cada pocas horas. Si algo falla, el workflow
crea un issue automáticamente para que nos enteremos sin estar mirando.

Uso local:
    python scripts/smoke_test.py
    BASE_URL=http://127.0.0.1:8000 python scripts/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.getenv("BASE_URL", "https://sabor-sinu.onrender.com").rstrip("/")
EXPECTED_COMMIT = os.getenv("EXPECTED_COMMIT", "").strip()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
PANEL_PATH = os.getenv("ADMIN_PANEL_PATH", "/panel-vendedor-sinu-7k3p9x")
TIMEOUT = float(os.getenv("SMOKE_TIMEOUT", "120"))

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"[OK]   {name}")
        return
    message = f"{name}: {detail}" if detail else name
    FAILURES.append(message)
    print(f"[FAIL] {message}")


def fetch(path: str, headers: dict[str, str] | None = None, timeout: float | None = None):
    url = BASE_URL + path
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SaborSinu-SmokeTest/1.0",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            **(headers or {}),
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout or TIMEOUT) as response:
            body = response.read()
            return (
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                body,
                time.monotonic() - started,
            )
    except urllib.error.HTTPError as error:
        body = error.read()
        return (
            error.code,
            {key.lower(): value for key, value in error.headers.items()},
            body,
            time.monotonic() - started,
        )
    except Exception as error:  # noqa: BLE001 - el smoke test debe reportar cualquier fallo
        raise RuntimeError(f"No se pudo conectar a {url}: {error}") from error


def json_body(body: bytes):
    return json.loads(body.decode("utf-8"))


def main() -> int:
    print(f"Smoke test contra {BASE_URL}")
    print("-" * 60)

    # 1. Salud y versión desplegada
    status, headers, body, elapsed = fetch("/api/health")
    check("GET /api/health responde 200", status == 200, f"status={status}")
    health = {}
    if status == 200:
        try:
            health = json_body(body)
        except Exception as error:  # noqa: BLE001
            check("GET /api/health devuelve JSON válido", False, str(error))
    check("health.status es ok", health.get("status") == "ok", str(health.get("status")))
    check("health.environment es production", health.get("environment") == "production", str(health.get("environment")))
    check(
        "health incluye commit del despliegue",
        bool(health.get("commit")) and health.get("commit") != "dev",
        f"commit={health.get('commit')!r}",
    )
    if EXPECTED_COMMIT:
        check(
            "el sitio tiene el último commit",
            health.get("commit") == EXPECTED_COMMIT,
            f"esperado={EXPECTED_COMMIT[:8]} recibido={str(health.get('commit'))[:8]}",
        )
    print(f"       tiempo de /api/health: {elapsed:.2f}s")

    # Segunda llamada: si la primera despertó Render, esta debe ser rápida.
    _, _, _, warm_elapsed = fetch("/api/health")
    print(f"       tiempo en caliente: {warm_elapsed:.2f}s")
    if warm_elapsed > 10:
        print(f"[WARN] /api/health sigue lento en caliente: {warm_elapsed:.2f}s")

    # 2. Configuración comercial
    status, headers, body, _ = fetch("/api/config")
    check("GET /api/config responde 200", status == 200, f"status={status}")
    config = {}
    if status == 200:
        try:
            config = json_body(body)
        except Exception as error:  # noqa: BLE001
            check("GET /api/config devuelve JSON válido", False, str(error))

    delivery = config.get("delivery", {})
    contact = config.get("contact", {})
    payments = config.get("payments", [])
    check("domicilio base es $2.000", delivery.get("base_fee") == 2000, str(delivery.get("base_fee")))
    check("domicilio gratis desde $80.000", delivery.get("free_from") == 80000, str(delivery.get("free_from")))
    check("las 3 zonas cobran $2.000", all(zone.get("fee") == 2000 for zone in delivery.get("zones", [])), str(delivery.get("zones")))
    check(
        "solo se aceptan Nequi y Llave",
        [payment.get("id") for payment in payments] == ["nequi", "llave"],
        str(payments),
    )
    check("Nequi y Llave apuntan al 304 466 7353", contact.get("nequi") == "304 466 7353" and contact.get("llave") == "304 466 7353", str(contact))
    check("el teléfono del vendedor está publicado", "304 466 7353" in contact.get("phone", ""), str(contact.get("phone")))
    check(
        "/api/config tiene caché de 5 minutos",
        "max-age=300" in headers.get("cache-control", ""),
        headers.get("cache-control", ""),
    )

    # 3. Menú y precios exactos
    status, _, body, _ = fetch("/api/menu")
    check("GET /api/menu responde 200", status == 200, f"status={status}")
    menu = {}
    if status == 200:
        try:
            menu = json_body(body)
        except Exception as error:  # noqa: BLE001
            check("GET /api/menu devuelve JSON válido", False, str(error))

    expected_prices = {
        "suizo": 33600,
        "superperro": 18000,
        "salchipapa": 28000,
        "hamburguesa": 18000,
        "gaseosa_postobon": 4500,
        "gaseosa_coca": 4500,
        "jugo_agua": 6000,
        "jugo_leche": 6000,
    }
    items = menu.get("items", [])
    by_id = {item.get("id"): item for item in items}
    check("el menú tiene 8 productos", len(items) == 8, str(len(items)))
    for product_id, price in expected_prices.items():
        item = by_id.get(product_id)
        check(f"precio de {product_id} es {price}", item is not None and item.get("price") == price, str(item.get("price") if item else "no existe"))
    check(
        "todas las fotos son JPG reales",
        all(str(item.get("image", "")).endswith(".jpg") for item in items),
        str([item.get("image") for item in items]),
    )
    check(
        "todos los productos traen disponibilidad",
        all(isinstance(item.get("available"), bool) for item in items),
        "faltan campos available",
    )

    # 4. Página, panel y ruta privada
    status, headers, body, _ = fetch("/")
    check("GET / responde 200", status == 200, f"status={status}")
    html = body.decode("utf-8", errors="replace")
    check("la página carga el chatbot", "app.js" in html and "Sabor Sin" in html)
    check("la página no se cachea", "no-store" in headers.get("cache-control", ""), headers.get("cache-control", ""))

    status, headers, body, _ = fetch(PANEL_PATH)
    check(f"GET {PANEL_PATH} responde 200", status == 200, f"status={status}")
    check("el panel no se indexa", "noindex" in headers.get("x-robots-tag", ""), headers.get("x-robots-tag", ""))
    check("el panel muestra el login", "Panel de stock" in body.decode("utf-8", errors="replace"))

    status, _, _, _ = fetch("/admin")
    check("GET /admin ya no existe", status == 404, f"status={status}")

    # 5. Seguridad del stock
    status, _, _, _ = fetch("/api/admin/stock")
    check("el stock exige contraseña", status == 401, f"status={status}")

    if ADMIN_TOKEN:
        status, _, body, _ = fetch("/api/admin/stock", headers={"X-Admin-Token": ADMIN_TOKEN})
        check("la contraseña del panel funciona", status == 200, f"status={status}")
        stock = {}
        if status == 200:
            try:
                stock = json_body(body)
            except Exception as error:  # noqa: BLE001
                check("el stock devuelve JSON válido", False, str(error))
        check("el stock está persistente en Upstash", stock.get("persistent") is True, str(stock.get("persistent")))
        check("el stock tiene 8 productos", len(stock.get("items", [])) == 8, str(len(stock.get("items", []))))
    else:
        print("[SKIP] No hay ADMIN_TOKEN; no se probó el panel autenticado.")

    # 6. Imágenes y compresión
    status, headers, body, _ = fetch("/static/img/suizo.jpg")
    check("la foto del suizo responde 200", status == 200, f"status={status}")
    check("la foto es JPEG", headers.get("content-type", "").startswith("image/jpeg"), headers.get("content-type", ""))
    check("las imágenes tienen caché de 1 día", "max-age=86400" in headers.get("cache-control", ""), headers.get("cache-control", ""))

    status, headers, _, _ = fetch("/api/config", headers={"Accept-Encoding": "gzip"})
    check("las respuestas van comprimidas con gzip", status == 200 and "gzip" in headers.get("content-encoding", ""), headers.get("content-encoding", ""))

    status, _, _, _ = fetch("/credits")
    check("GET /credits responde 200", status == 200, f"status={status}")

    print("-" * 60)
    if FAILURES:
        print(f"FALLARON {len(FAILURES)} de {CHECKS} comprobaciones:")
        for failure in FAILURES:
            print(f" - {failure}")
        return 1
    print(f"TODO BIEN: {CHECKS} comprobaciones pasaron.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(f"[FAIL] Error inesperado: {error}")
        raise SystemExit(1)
