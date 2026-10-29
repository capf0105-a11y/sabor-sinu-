"""Pruebas del motor conversacional y del cálculo de pedidos."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import settings
from app.engine import ChatEngine
from app.nlu import extract_phone, parse_order
from app.orders import OrderStore, public_order
from app.sessions import Session


@pytest.fixture()
def setup(tmp_path):
    cfg = replace(
        settings,
        data_dir=tmp_path,
        orders_file=tmp_path / "orders.json",
        outbox_dir=tmp_path / "outbox",
        email_dry_run=True,
    )
    store = OrderStore(cfg.orders_file)
    return ChatEngine(cfg, store), store, Session(id="sess_test")


def say(engine: ChatEngine, session: Session, message: str) -> dict:
    return engine.handle(session, message)


def test_parsea_pedido_compuesto():
    parsed = parse_order("Quiero dos suizos y una gaseosa")
    assert [(line.product.id, line.qty) for line in parsed.lines] == [
        ("suizo", 2),
        ("gaseosa_postobon", 1),
    ]


def test_parsea_variantes_de_bebida():
    assert parse_order("dos jugos en leche").lines[0].product.id == "jugo_leche"
    assert parse_order("una coca cola").lines[0].product.id == "gaseosa_coca"
    assert parse_order("tres jugos").lines[0].product.id == "jugo_agua"


def test_parsea_extras_y_notas():
    parsed = parse_order("2 salchipapas con extra queso y una hamburguesa sin cebolla")
    salchipapa = parsed.lines[0]
    hamburguesa = parsed.lines[1]
    assert salchipapa.qty == 2
    assert salchipapa.modifiers[0]["key"] == "extra_queso"
    assert "Sin cebolla" in hamburguesa.notes


def test_agrega_y_calcula_totales(setup):
    engine, _store, session = setup
    response = say(engine, session, "Quiero dos suizos y una gaseosa")
    assert response["cart"]["count"] == 3
    assert response["cart"]["subtotal"] == 71700
    assert response["cart"]["total"] == 77700
    assert response["stage"] == "browsing"


def test_consulta_precio_no_agrega(setup):
    engine, _store, session = setup
    response = say(engine, session, "¿Cuánto cuesta el suizo?")
    assert response["cart"]["count"] == 0
    assert "33.600" in response["blocks"][0]["text"]


def test_quitar_producto(setup):
    engine, _store, session = setup
    say(engine, session, "dos suizos")
    response = say(engine, session, "quita un suizo")
    assert response["cart"]["count"] == 1


def test_envio_gratis(setup):
    engine, _store, session = setup
    response = say(engine, session, "tres suizos")
    assert response["cart"]["subtotal"] == 100800
    assert response["cart"]["delivery_fee"] == 0
    assert response["cart"]["free_delivery"] is True


def test_flujo_completo_de_compra(setup):
    engine, store, session = setup
    say(engine, session, "Quiero dos suizos y una gaseosa")
    response = say(engine, session, "Finalizar pedido")
    assert response["stage"] == "checkout_name"

    response = say(engine, session, "María Fernanda Gómez")
    assert response["stage"] == "checkout_address"

    response = say(engine, session, "Calle 12 #4-56, barrio Centro")
    assert response["stage"] == "checkout_phone"
    assert session.customer["barrio"] == "Centro"
    assert session.customer["address"] == "Calle 12 #4-56"

    response = say(engine, session, "300 123 4567")
    assert response["stage"] == "checkout_payment"

    response = say(engine, session, "Nequi")
    assert response["stage"] == "checkout_confirm"
    summary = [b for b in response["blocks"] if b["type"] == "summary"][0]
    assert summary["summary"]["delivery_fee"] == 4000
    assert summary["summary"]["total"] == 75700

    response = say(engine, session, "Confirmar pedido")
    assert response["stage"] == "confirmed"
    assert response["order"]["total"] == 75700
    assert response["order"]["status"]["key"] == "recibido"
    assert response["_notify_order_id"] == response["order"]["id"]

    order = store.get(response["order"]["id"])
    assert order is not None
    assert len(order["items"]) == 2
    assert order["customer"]["payment"] == "nequi"
    assert session.cart == []


def test_pago_solo_nequi_o_llave(setup):
    engine, _store, session = setup
    say(engine, session, "un suizo")
    say(engine, session, "finalizar")
    say(engine, session, "Juan Pérez")
    say(engine, session, "Calle 12 #4-56, Centro")
    say(engine, session, "3001234567")
    response = say(engine, session, "Efectivo")
    assert response["stage"] == "checkout_payment"
    assert "Nequi" in response["blocks"][0]["text"]
    response = say(engine, session, "llave")
    assert response["stage"] == "checkout_confirm"
    assert session.customer["payment"] == "llave"


def test_telefono_invalido_se_rechaza(setup):
    engine, _store, session = setup
    say(engine, session, "un suizo")
    say(engine, session, "finalizar")
    say(engine, session, "Juan Pérez")
    say(engine, session, "Carrera 6 #12-34")
    say(engine, session, "La Granja")
    response = say(engine, session, "12345")
    assert response["stage"] == "checkout_phone"
    assert "10 dígitos" in response["blocks"][0]["text"]


def test_barrio_con_error_de_digitacion(setup):
    engine, _store, session = setup
    say(engine, session, "un suizo")
    say(engine, session, "finalizar")
    say(engine, session, "Juan Pérez")
    response = say(engine, session, "Calle 12 #4-56, barrio Monte berde")
    assert response["stage"] == "checkout_phone"
    assert session.customer["barrio"] == "Monteverde"


def test_pedido_con_finalizar_en_el_mismo_mensaje(setup):
    engine, _store, session = setup
    response = say(engine, session, "Quiero dos suizos y finalizar")
    assert response["stage"] == "checkout_name"
    assert len(session.cart) == 1
    assert session.cart[0].qty == 2


def test_estado_del_pedido(setup):
    engine, _store, session = setup
    say(engine, session, "un suizo")
    say(engine, session, "finalizar")
    say(engine, session, "Juan Pérez")
    say(engine, session, "Calle 12 #4-56, Centro")
    say(engine, session, "3001234567")
    say(engine, session, "Llave")
    response = say(engine, session, "confirmar")
    order_id = response["order"]["id"]

    response = say(engine, session, "estado de mi pedido")
    status = [b for b in response["blocks"] if b["type"] == "status"][0]
    assert status["order"]["id"] == order_id
    assert status["order"]["status"]["steps"][0]["done"] is True

    # También por código explícito
    response = say(engine, session, f"mi pedido {order_id}")
    assert response["blocks"][1]["order"]["id"] == order_id


def test_extra_queso_suelto(setup):
    engine, _store, session = setup
    say(engine, session, "una hamburguesa")
    response = say(engine, session, "con extra queso")
    assert response["cart"]["items"][0]["unit_price"] == 20500


def test_telefono_y_codigo():
    assert extract_phone("+57 300 123 4567") == "3001234567"
    assert extract_phone("123") is None
    from app.nlu import extract_order_code

    assert extract_order_code("mi pedido SINU-261028-4F7A") == "SINU-261028-4F7A"


def test_public_order_no_expone_correo(setup):
    engine, store, session = setup
    say(engine, session, "un suizo")
    say(engine, session, "finalizar")
    say(engine, session, "Ana Torres")
    say(engine, session, "Calle 1 #2-3, Centro")
    say(engine, session, "3001112233")
    say(engine, session, "Nequi")
    response = say(engine, session, "confirmar")
    public = public_order(store.get(response["order"]["id"]))
    assert "email" not in public
    assert public["customer"]["payment_label"] == "Nequi"
