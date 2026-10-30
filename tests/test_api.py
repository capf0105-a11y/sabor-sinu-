"""Pruebas de integración de la API con el cliente de FastAPI."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, store

client = TestClient(app)


def test_home_y_salud():
    home = client.get("/")
    assert home.status_code == 200
    assert "Sabor Sinú" in home.text
    assert "app.js" in home.text

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

    menu = client.get("/api/menu").json()
    assert len(menu["items"]) == 8
    prices = {item["id"]: item["price"] for item in menu["items"]}
    assert prices["suizo"] == 33600
    assert prices["hamburguesa"] == 18000
    assert prices["gaseosa_coca"] == 4500


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
    assert response.json()["count"] >= 1


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
    assert client.get(panel_path).status_code == 200
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
