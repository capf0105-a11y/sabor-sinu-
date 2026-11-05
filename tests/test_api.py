"""Pruebas de integración de la API con el cliente de FastAPI."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, store

client = TestClient(app)


@pytest.fixture(autouse=True)
def negocio_abierto(monkeypatch):
    """El chat rechaza pedidos fuera de horario; las pruebas no deben depender de la hora."""
    monkeypatch.setattr(
        "app.engine.schedule_status",
        lambda *args, **kwargs: {"is_open": True, "status_label": "Abierto ahora"},
    )


def test_home_y_salud():
    home = client.get("/")
    assert home.status_code == 200
    assert "Sabor Sinú" in home.text
    assert "app.js" in home.text
    assert "boot.js" in home.text
    assert "default-src 'self'" in home.headers["content-security-policy"]
    assert home.headers["x-frame-options"] == "DENY"
    assert "camera=()" in home.headers["permissions-policy"]

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["email_mode"] == "dry_run"
    assert health.json()["commit"]
    assert client.get("/health").json()["status"] == "ok"


def test_config_y_menu():
    config_response = client.get("/api/config")
    assert "max-age=300" in config_response.headers["Cache-Control"]
    config = config_response.json()
    assert config["brand"]["name"] == settings.business_name
    assert config["delivery"]["free_from"] == settings.free_delivery_from
    assert len(config["delivery"]["zones"]) == 3
    assert config["delivery"]["eta_minutes"] == (
        settings.prep_minutes + settings.delivery_minutes
    )
    assert isinstance(config["contact"]["is_open"], bool)
    assert config["contact"]["status_label"] in {"Abierto ahora", "Cerrado"}

    menu = client.get("/api/menu").json()
    assert len(menu["items"]) == 8
    prices = {item["id"]: item["price"] for item in menu["items"]}
    assert prices["suizo"] == 33600
    assert prices["hamburguesa"] == 18000
    assert prices["gaseosa_coca"] == 4500
    prep = {item["id"]: item["prep_minutes"] for item in menu["items"]}
    assert prep["suizo"] == 18
    assert prep["gaseosa_coca"] == 2


def test_chat_y_carrito():
    response = client.post("/api/chat", json={"message": "Quiero dos suizos y una gaseosa"})
    assert response.status_code == 200
    data = response.json()
    assert data["cart"]["count"] == 3
    assert data["cart"]["subtotal"] == 71700
    session_id = data["session_id"]

    added = client.post(
        "/api/cart",
        json={"session_id": session_id, "action": "add", "product_id": "gaseosa_coca", "qty": 2},
    )
    assert added.status_code == 200
    assert added.json()["cart"]["count"] == 5

    removed = client.post(
        "/api/cart", json={"session_id": session_id, "action": "clear"}
    )
    assert removed.json()["cart"]["count"] == 0

    session = client.get(f"/api/session/{session_id}")
    assert session.status_code == 200
    assert session.json()["stage"] == "browsing"


def test_flujo_http_hasta_pedido_y_correo():
    chat = client.post("/api/chat", json={"message": "un suizo y una gaseosa"})
    session_id = chat.json()["session_id"]

    pasos = [
        "Finalizar pedido",
        "Carlos Andrés Ruiz",
        "Calle 30 #8-12, barrio Centro",
        "3015557788",
        "Nequi",
    ]
    for message in pasos:
        response = client.post(
            "/api/chat", json={"session_id": session_id, "message": message}
        )
        assert response.status_code == 200

    final = client.post(
        "/api/chat", json={"session_id": session_id, "message": "Confirmar pedido"}
    )
    data = final.json()
    assert data["stage"] == "confirmed"
    order = data["order"]
    assert order["total"] == 40100  # 33.600 + 4.500 + 2.000 de domicilio
    assert order["customer"]["barrio"] == "Centro"
    assert order["customer"]["address"] == "Calle 30 #8-12"

    public = client.get(f"/api/orders/{order['id']}")
    assert public.status_code == 200
    assert public.json()["order"]["id"] == order["id"]
    assert "email" not in public.json()["order"]

    stored = store.get(order["id"])
    assert stored["email"]["status"] == "dry_run"
    outbox = settings.outbox_dir / f"{order['id']}.html"
    assert outbox.exists()
    assert "Sabor Sinú" in outbox.read_text(encoding="utf-8")


def test_admin_protegido():
    assert client.get("/api/admin/orders").status_code == 401
    assert (
        client.get("/api/admin/orders", headers={"X-Admin-Token": "incorrecto"}).status_code
        == 401
    )
    response = client.get(
        "/api/admin/orders", headers={"X-Admin-Token": settings.admin_token}
    )
    assert response.status_code == 200
    assert isinstance(response.json()["count"], int)


def test_admin_login_con_cookie_y_cierre():
    c = TestClient(app)
    assert c.get("/api/admin/stock").status_code == 401

    bad_origin = c.post(
        "/api/admin/login",
        json={"password": settings.admin_token},
        headers={"Origin": "https://sitio-malicioso.example"},
    )
    assert bad_origin.status_code == 403

    bad = c.post("/api/admin/login", json={"password": "incorrecta"})
    assert bad.status_code == 401

    good = c.post("/api/admin/login", json={"password": settings.admin_token})
    assert good.status_code == 200
    assert good.headers["cache-control"] == "no-store"
    assert c.cookies.get("sinu_admin_session")
    assert c.get("/api/admin/stock").status_code == 200

    assert c.post("/api/admin/logout").status_code == 200
    assert c.get("/api/admin/stock").status_code == 401


def test_login_bloquea_fuerza_bruta():
    c = TestClient(app)
    ip = {"CF-Connecting-IP": "203.0.113.50"}
    for _ in range(5):
        response = c.post(
            "/api/admin/login", json={"password": "incorrecta"}, headers=ip
        )
        assert response.status_code == 401
    blocked = c.post(
        "/api/admin/login",
        json={"password": settings.admin_token},
        headers=ip,
    )
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "900"


def test_admin_put_rechaza_origen_externo():
    c = TestClient(app)
    assert c.post("/api/admin/login", json={"password": settings.admin_token}).status_code == 200
    response = c.put(
        "/api/admin/stock/suizo",
        json={"available": True},
        headers={"Origin": "https://sitio-malicioso.example"},
    )
    assert response.status_code == 403


def test_token_admin_tambien_se_bloquea():
    c = TestClient(app)
    ip = {"CF-Connecting-IP": "203.0.113.51", "X-Admin-Token": "incorrecto"}
    for _ in range(5):
        assert c.get("/api/admin/orders", headers=ip).status_code == 401
    blocked = c.get(
        "/api/admin/orders",
        headers={"CF-Connecting-IP": "203.0.113.51", "X-Admin-Token": settings.admin_token},
    )
    assert blocked.status_code == 429


def test_rate_limit_no_afecta_health():
    for _ in range(5):
        assert client.get("/api/health").status_code == 200


def test_producto_invalido():
    response = client.post(
        "/api/cart", json={"action": "add", "product_id": "no-existe", "qty": 1}
    )
    assert response.status_code == 400

def test_panel_admin_y_stock_agotado():
    panel_path = settings.admin_panel_path or "/admin"
    panel = client.get(panel_path)
    assert panel.status_code == 200
    assert "default-src 'none'" in panel.headers["content-security-policy"]
    assert "script-src 'self'" in panel.headers["content-security-policy"]
    assert "sessionStorage" not in panel.text
    assert "X-Admin-Token" not in panel.text
    if panel_path != "/admin":
        assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/stock").status_code == 401

    headers = {"X-Admin-Token": settings.admin_token}
    response = client.put(
        "/api/admin/stock/suizo", json={"available": False}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["available"] is False

    menu = client.get("/api/menu").json()
    suizo = next(item for item in menu["items"] if item["id"] == "suizo")
    assert suizo["available"] is False

    chat = client.post("/api/chat", json={"message": "Quiero un suizo"}).json()
    assert "no tenemos" in chat["blocks"][0]["text"].lower()

    response = client.put(
        "/api/admin/stock/suizo", json={"available": True}, headers=headers
    )
    assert response.json()["available"] is True
    menu = client.get("/api/menu").json()
    suizo = next(item for item in menu["items"] if item["id"] == "suizo")
    assert suizo["available"] is True


def test_cors_permite_github_pages():
    origin = "https://capf0105-a11y.github.io"
    response = client.get("/api/config", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin

    preflight = client.options(
        "/api/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "POST" in preflight.headers["access-control-allow-methods"]

    blocked = client.get(
        "/api/config", headers={"Origin": "https://sitio-no-autorizado.example"}
    )
    assert blocked.status_code == 200
    assert "access-control-allow-origin" not in blocked.headers


def test_pages_publico_actualizado():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    index = (root / "docs" / "index.html").read_text(encoding="utf-8")
    assert 'name="sinu-api-base" content="https://sabor-sinu.onrender.com"' in index
    assert 'href="static/css/styles.css' in index
    assert 'src="static/js/app.js' in index
    assert 'href="credits.html"' in index
    assert 'href="/static/' not in index
    assert (root / "docs" / ".nojekyll").exists()

    app_js = (root / "docs" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "sinu-api-base" in app_js
    assert "assetUrl(product.image)" in app_js


def test_carrito_bloqueado_fuera_de_horario(monkeypatch):
    monkeypatch.setattr(
        "app.main.schedule_status",
        lambda *args, **kwargs: {"is_open": False, "status_label": "Cerrado"},
    )
    response = client.post(
        "/api/cart",
        json={"action": "add", "product_id": "gaseosa_coca", "qty": 1},
    )
    assert response.status_code == 403
    assert "cerrados" in response.json()["detail"]


def test_carrito_siempre_abierto_ignora_horario(monkeypatch):
    monkeypatch.setattr(
        "app.main.schedule_status",
        lambda *args, **kwargs: {"is_open": False, "status_label": "Cerrado"},
    )
    response = client.post(
        "/api/cart",
        json={
            "action": "add",
            "product_id": "gaseosa_coca",
            "qty": 1,
            "always_open": True,
        },
    )
    assert response.status_code == 200
    config = client.get("/api/config?always_open=true").json()
    assert config["contact"]["is_open"] is True
