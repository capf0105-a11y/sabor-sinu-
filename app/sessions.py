"""Carrito, sesiones de conversación y cálculos de totales."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

from .config import Settings
from .menu import PRODUCTS_BY_ID, Product, find_zone
from .utils import clamp_int, money, new_session_id


@dataclass
class CartItem:
    uid: str
    product_id: str
    qty: int
    modifiers: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def product(self) -> Product:
        return PRODUCTS_BY_ID[self.product_id]

    @property
    def unit_price(self) -> int:
        return self.product.price + sum(int(mod.get("price", 0)) for mod in self.modifiers)

    @property
    def line_total(self) -> int:
        return self.unit_price * self.qty

    def signature(self) -> tuple:
        mods = tuple(sorted(mod["key"] for mod in self.modifiers))
        return (self.product_id, mods, tuple(sorted(self.notes)))


def _new_uid() -> str:
    return f"itm_{secrets.token_hex(5)}"


def add_to_cart(
    cart: list[CartItem],
    product: Product,
    qty: int,
    modifiers: Iterable[dict] = (),
    notes: Iterable[str] = (),
) -> CartItem:
    """Suma un producto, fusionando líneas idénticas (mismo producto y extras)."""
    qty = clamp_int(int(qty), 1, 20)
    modifier_list: list[dict] = []
    seen: set[str] = set()
    for modifier in modifiers:
        key = modifier.get("key") or modifier.get("name")
        if key in seen:
            continue
        seen.add(key)
        modifier_list.append(
            {
                "key": key,
                "name": modifier.get("name", str(key)),
                "price": int(modifier.get("price", 0)),
            }
        )
    note_list = list(dict.fromkeys(note for note in notes if note))

    candidate = CartItem(
        uid=_new_uid(),
        product_id=product.id,
        qty=qty,
        modifiers=modifier_list,
        notes=note_list,
    )
    signature = candidate.signature()
    for item in cart:
        if item.signature() == signature:
            item.qty = clamp_int(item.qty + qty, 1, 30)
            return item

    if len(cart) >= 30:
        raise ValueError("El carrito alcanzó el máximo de 30 líneas.")
    cart.append(candidate)
    return candidate


def find_item(cart: list[CartItem], uid: str) -> CartItem | None:
    return next((item for item in cart if item.uid == uid), None)


def change_qty(cart: list[CartItem], uid: str, delta: int) -> CartItem | None:
    item = find_item(cart, uid)
    if not item:
        return None
    item.qty += delta
    if item.qty <= 0:
        cart.remove(item)
        return None
    item.qty = clamp_int(item.qty, 1, 30)
    return item


def cart_count(cart: list[CartItem]) -> int:
    return sum(item.qty for item in cart)


def cart_subtotal(cart: list[CartItem]) -> int:
    return sum(item.line_total for item in cart)


def cart_totals(cart: list[CartItem], barrio: str | None, settings: Settings) -> dict:
    subtotal = cart_subtotal(cart)
    if subtotal <= 0:
        fee = 0
        free = False
    elif subtotal >= settings.free_delivery_from:
        fee = 0
        free = True
    else:
        zone = find_zone(barrio) if barrio else None
        fee = zone[1].fee if zone else settings.delivery_base_fee
        free = False

    return {
        "subtotal": subtotal,
        "delivery_fee": fee,
        "total": subtotal + fee,
        "free_delivery": free,
        "free_delivery_from": settings.free_delivery_from,
        "remaining_for_free": max(0, settings.free_delivery_from - subtotal),
        "delivery_estimated": not bool(barrio) or find_zone(barrio) is None,
    }


def cart_public(cart: list[CartItem], barrio: str | None, settings: Settings) -> dict:
    totals = cart_totals(cart, barrio, settings)
    return {
        "items": [
            {
                "uid": item.uid,
                "product_id": item.product_id,
                "name": item.product.name,
                "image": item.product.image,
                "qty": item.qty,
                "unit_price": item.unit_price,
                "line_total": item.line_total,
                "modifiers": item.modifiers,
                "notes": item.notes,
                "detail": ", ".join(
                    [mod["name"] for mod in item.modifiers] + item.notes
                ),
            }
            for item in cart
        ],
        "count": cart_count(cart),
        "currency": "COP",
        "barrio": barrio,
        **totals,
    }


def items_for_order(cart: list[CartItem]) -> list[dict]:
    """Copia inmutable de las líneas para guardar en el pedido."""
    return [
        {
            "product_id": item.product_id,
            "name": item.product.name,
            "image": item.product.image,
            "qty": item.qty,
            "unit_price": item.unit_price,
            "line_total": item.line_total,
            "modifiers": [dict(mod) for mod in item.modifiers],
            "notes": list(item.notes),
        }
        for item in cart
    ]


@dataclass
class Session:
    id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    stage: str = "browsing"
    cart: list[CartItem] = field(default_factory=list)
    customer: dict = field(default_factory=dict)
    last_order_id: str | None = None
    greeted: bool = False

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionStore:
    """Sesiones en memoria con expiración.

    Para escalar horizontalmente se puede sustituir por Redis manteniendo la
    misma interfaz (get_or_create / reset / cleanup).
    """

    def __init__(self, ttl_minutes: int = 180) -> None:
        self.ttl_seconds = max(60, ttl_minutes * 60)
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._last_cleanup = 0.0

    def new(self) -> Session:
        session = Session(id=new_session_id())
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            if time.time() - session.updated_at > self.ttl_seconds:
                self._sessions.pop(session_id, None)
                return None
            return session

    def get_or_create(self, session_id: str | None) -> Session:
        session = self.get(session_id)
        if session:
            session.touch()
            return session
        return self.new()

    def reset(self, session: Session) -> Session:
        with self._lock:
            session.cart.clear()
            session.customer.clear()
            session.stage = "browsing"
            session.last_order_id = None
            session.touch()
        return session

    def cleanup(self) -> int:
        now = time.time()
        if now - self._last_cleanup < 60:
            return 0
        self._last_cleanup = now
        with self._lock:
            expired = [
                key
                for key, session in self._sessions.items()
                if now - session.updated_at > self.ttl_seconds
            ]
            for key in expired:
                self._sessions.pop(key, None)
        return len(expired)


def cart_summary_text(cart: list[CartItem]) -> str:
    if not cart:
        return "carrito vacío"
    parts = [f"{item.qty} × {item.product.name}" for item in cart[:4]]
    if len(cart) > 4:
        parts.append(f"+{len(cart) - 4} más")
    return ", ".join(parts)


def totals_line(cart: list[CartItem], barrio: str | None, settings: Settings) -> str:
    totals = cart_totals(cart, barrio, settings)
    return (
        f"Subtotal {money(totals['subtotal'])} · Domicilio "
        f"{'gratis' if totals['delivery_fee'] == 0 and totals['subtotal'] else money(totals['delivery_fee'])} "
        f"· **Total {money(totals['total'])}**"
    )
