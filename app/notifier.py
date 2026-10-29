"""Notificaciones por WhatsApp para el vendedor.

Usa CallMeBot (https://www.callmebot.com/blog/free-api-whatsapp-messages/),
un servicio gratuito para enviar mensajes a un número propio. El vendedor
autoriza su número una vez y recibe una API key.

Si no hay configuración, la función no hace nada y el pedido sigue normal.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request

from .config import Settings
from .orders import PAYMENT_LABELS
from .utils import money

logger = logging.getLogger("saborsinu.whatsapp")

MAX_TEXT = 900


def _item_line(item: dict) -> str:
    qty = int(item.get("qty", 1))
    name = item.get("name", "Producto")
    extras = [mod.get("name", "") for mod in item.get("modifiers", [])]
    if item.get("notes"):
        extras.append(str(item["notes"]))
    suffix = f" ({', '.join(e for e in extras if e)})" if extras else ""
    return f"- {qty}x {name}{suffix}: {money(item.get('line_total', 0))}"


def build_whatsapp_text(order: dict) -> str:
    customer = order.get("customer", {})
    payment = PAYMENT_LABELS.get(
        customer.get("payment", ""), customer.get("payment", "")
    )
    lines = [
        f"*NUEVO PEDIDO {order.get('id', '')}*",
        f"Cliente: {customer.get('name', '')}",
        f"Celular: {customer.get('phone', '')}",
        f"Direccion: {customer.get('address', '')}",
        f"Barrio: {customer.get('barrio', '')}",
        "",
        "*Productos*",
    ]
    lines.extend(_item_line(item) for item in order.get("items", []))
    lines += [
        "",
        f"Subtotal: {money(order.get('subtotal', 0))}",
        f"Domicilio: {money(order.get('delivery_fee', 0))}",
        f"*TOTAL: {money(order.get('total', 0))}*",
        f"Pago: {payment}",
    ]
    if order.get("notes"):
        lines.append(f"Notas: {order['notes']}")
    text = "\n".join(lines)
    return text[:MAX_TEXT]


def send_whatsapp_alert(order: dict, settings: Settings) -> dict:
    """Envía el pedido al WhatsApp del vendedor vía CallMeBot."""
    if not settings.whatsapp_alerts_enabled:
        return {"status": "disabled"}

    phone = re.sub(r"\D", "", settings.whatsapp_alert_phone)
    if not phone:
        return {"status": "disabled"}

    params = urllib.parse.urlencode(
        {
            "phone": f"+{phone}",
            "text": build_whatsapp_text(order),
            "apikey": settings.callmebot_api_key,
        }
    )
    url = f"https://api.callmebot.com/whatsapp.php?{params}"

    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            status = response.status
            body = response.read(300).decode("utf-8", "ignore")
        if status == 200:
            logger.info("WhatsApp enviado para el pedido %s", order.get("id"))
            return {"status": "sent"}
        logger.warning(
            "WhatsApp fallo para %s: HTTP %s %s", order.get("id"), status, body[:120]
        )
        return {"status": "error", "detail": f"HTTP {status}"}
    except Exception as exc:  # noqa: BLE001 - nunca debe romper el pedido
        logger.warning("WhatsApp fallo para %s: %s", order.get("id"), exc)
        return {"status": "error", "detail": str(exc)[:200]}
