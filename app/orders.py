"""Persistencia de pedidos, estado de seguimiento y datos públicos."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import Settings
from .utils import BOGOTA, money, order_code

logger = logging.getLogger("saborsinu.orders")

PAYMENT_LABELS = {
    "nequi": "Nequi",
    "llave": "Llave",
}

MONTHS_ES = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)


def format_datetime(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, BOGOTA)
    hour = dt.hour % 12 or 12
    suffix = "a.m." if dt.hour < 12 else "p.m."
    return f"{dt.day} {MONTHS_ES[dt.month - 1]} {dt.year}, {hour}:{dt.minute:02d} {suffix}"


def format_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, BOGOTA)
    hour = dt.hour % 12 or 12
    suffix = "a.m." if dt.hour < 12 else "p.m."
    return f"{hour}:{dt.minute:02d} {suffix}"


def build_status(order: dict) -> dict:
    """Estado derivado del tiempo transcurrido (seguimiento tipo delivery)."""
    prep = int(order.get("prep_minutes", 25))
    delivery = int(order.get("delivery_minutes", 20))
    total = prep + delivery
    elapsed = max(0.0, (time.time() - float(order["created_ts"])) / 60)

    steps_def = (
        ("recibido", "Pedido recibido", "Recibimos tu pedido y lo enviamos a cocina.", 0),
        ("preparacion", "En preparación", "Estamos preparando todo fresco para ti.", 3),
        ("camino", "En camino", "Tu pedido salió con nuestro domiciliario.", prep),
        ("entregado", "Entregado", "¡Buen provecho! Gracias por pedir en Sabor Sinú.", total),
    )

    current_index = 0
    steps = []
    for index, (key, label, description, offset) in enumerate(steps_def):
        done = elapsed >= offset
        if done:
            current_index = index
        steps.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "at": order["created_ts"] + offset * 60,
                "at_label": format_time(order["created_ts"] + offset * 60),
                "done": done,
                "current": False,
            }
        )
    steps[current_index]["current"] = True
    progress = 0 if total <= 0 else min(100, round(elapsed / total * 100))

    return {
        "key": steps_def[current_index][0],
        "label": steps_def[current_index][1],
        "description": steps_def[current_index][2],
        "progress": progress,
        "elapsed_minutes": round(elapsed),
        "eta_remaining_minutes": max(0, round(total - elapsed)),
        "eta_total_minutes": total,
        "steps": steps,
        "updated_at": time.time(),
    }


def public_order(order: dict) -> dict:
    status = build_status(order)
    customer = order.get("customer", {})
    return {
        "id": order["id"],
        "created_at": order["created_at"],
        "created_at_display": format_datetime(order["created_ts"]),
        "customer": {
            "name": customer.get("name", ""),
            "address": customer.get("address", ""),
            "barrio": customer.get("barrio", ""),
            "phone": customer.get("phone", ""),
            "payment": customer.get("payment", ""),
            "payment_label": PAYMENT_LABELS.get(
                customer.get("payment", ""), customer.get("payment", "")
            ),
        },
        "items": order.get("items", []),
        "subtotal": order.get("subtotal", 0),
        "delivery_fee": order.get("delivery_fee", 0),
        "total": order.get("total", 0),
        "currency": "COP",
        "notes": order.get("notes", ""),
        "prep_minutes": order.get("prep_minutes", 25),
        "delivery_minutes": order.get("delivery_minutes", 20),
        "eta_label": f"{order.get('prep_minutes', 25) + order.get('delivery_minutes', 20)} min aprox.",
        "status": status,
    }


class OrderStore:
    """Almacén JSON con escritura atómica.

    Un archivo es suficiente para una operación pequeña y hace que el pedido
    sobreviva reinicios del servicio gratuito. Para mayor volumen se puede
    reemplazar por PostgreSQL sin cambiar la interfaz pública.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._orders: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            orders = data.get("orders", []) if isinstance(data, dict) else data
            self._orders = {order["id"]: order for order in orders if "id" in order}
        except Exception:  # archivo corrupto: se conserva una copia y se continúa
            logger.exception("No se pudo leer %s; se creará un archivo nuevo", self.path)
            backup = self.path.with_suffix(f".corrupt-{int(time.time())}.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            self._orders = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"orders": list(self._orders.values())}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    def create(
        self,
        *,
        customer: dict,
        items: list[dict],
        subtotal: int,
        delivery_fee: int,
        total: int,
        prep_minutes: int,
        delivery_minutes: int,
        notes: str = "",
    ) -> dict:
        with self._lock:
            order_id = order_code()
            while order_id in self._orders:
                order_id = order_code()
            created_ts = time.time()
            order = {
                "id": order_id,
                "created_ts": created_ts,
                "created_at": datetime.fromtimestamp(created_ts, BOGOTA).isoformat(
                    timespec="seconds"
                ),
                "customer": dict(customer),
                "items": copy.deepcopy(items),
                "subtotal": subtotal,
                "delivery_fee": delivery_fee,
                "total": total,
                "currency": "COP",
                "notes": notes,
                "prep_minutes": prep_minutes,
                "delivery_minutes": delivery_minutes,
                "email": {"status": "pending", "to": [], "sent_at": None, "error": None},
                "source": "web-chat",
            }
            self._orders[order_id] = order
            self._save()
            logger.info(
                "Pedido creado %s · %s · %s", order_id, customer.get("name"), money(total)
            )
            return copy.deepcopy(order)

    def get(self, order_id: str) -> dict | None:
        with self._lock:
            order = self._orders.get((order_id or "").upper())
            return copy.deepcopy(order) if order else None

    def update_email(self, order_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            order = self._orders.get((order_id or "").upper())
            if not order:
                return
            order["email"] = {
                "status": status,
                "to": order.get("email", {}).get("to", []),
                "sent_at": time.time() if status == "sent" else None,
                "error": error,
            }
            self._save()

    def set_email_recipients(self, order_id: str, recipients: list[str]) -> None:
        with self._lock:
            order = self._orders.get((order_id or "").upper())
            if not order:
                return
            order.setdefault("email", {})["to"] = recipients
            self._save()

    def list_recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            orders = sorted(
                self._orders.values(), key=lambda item: item.get("created_ts", 0), reverse=True
            )
            return copy.deepcopy(orders[: max(1, min(limit, 200))])

    def count(self) -> int:
        with self._lock:
            return len(self._orders)


def admin_order(order: dict) -> dict:
    public = public_order(order)
    public["email"] = order.get("email", {})
    return public
