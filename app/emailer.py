"""Notificaciones por correo del pedido (SMTP + plantilla HTML corporativa).

En local, si no hay SMTP configurado, el correo se guarda en data/outbox para
poder revisarlo en el navegador. En producción basta con definir SMTP_HOST,
SMTP_USER, SMTP_PASSWORD y ADMIN_EMAILS.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from .config import Settings
from .orders import PAYMENT_LABELS, format_datetime
from .utils import money

logger = logging.getLogger("saborsinu.email")


def _item_rows(order: dict) -> str:
    rows = []
    for item in order.get("items", []):
        extras = [mod["name"] for mod in item.get("modifiers", [])]
        extras += item.get("notes", [])
        detail = (
            f'<div style="font-size:12px;color:#8a7f72;margin-top:3px;">'
            f'{html.escape(" · ".join(extras))}</div>'
            if extras
            else ""
        )
        rows.append(
            f"""
            <tr>
              <td style="padding:12px 10px;border-bottom:1px solid #efe6d8;font-size:14px;color:#241f1a;">
                <strong>{item.get('qty', 1)}×</strong> {html.escape(item.get('name', ''))}
                {detail}
              </td>
              <td align="right" style="padding:12px 10px;border-bottom:1px solid #efe6d8;font-size:14px;color:#241f1a;white-space:nowrap;">
                {money(item.get('line_total', 0))}
              </td>
            </tr>
            """
        )
    return "".join(rows)


def build_email(order: dict, settings: Settings, dry_run: bool = False) -> tuple[str, str, str]:
    customer = order.get("customer", {})
    order_id = order.get("id", "")
    payment_key = customer.get("payment", "")
    payment = PAYMENT_LABELS.get(payment_key, payment_key)
    payment_number = {
        "nequi": settings.business_nequi,
        "llave": settings.business_llave,
    }.get(payment_key)
    if payment_number:
        payment = f"{payment} al {payment_number}"
    phone_digits = "".join(ch for ch in customer.get("phone", "") if ch.isdigit())
    total = money(order.get("total", 0))
    delivery_fee = (
        "Gratis"
        if order.get("delivery_fee", 0) == 0
        else money(order.get("delivery_fee", 0))
    )
    notes = order.get("notes") or "Sin observaciones"

    subject = f"Nuevo pedido {order_id} · {total} · {customer.get('name', 'Cliente')}"

    demo_banner = (
        '<tr><td style="background:#fff4d6;color:#7a5200;padding:10px 24px;'
        'font-size:12px;text-align:center;">MODO DEMO · Este correo no se envió por SMTP; '
        "se guardó en data/outbox.</td></tr>"
        if dry_run
        else ""
    )

    html_body = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f6efe4;font-family:Arial,Helvetica,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">
    Pedido {html.escape(order_id)} por {html.escape(total)} en {html.escape(settings.business_name)}.
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f6efe4;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="620" cellspacing="0" cellpadding="0" style="max-width:620px;width:100%;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #e7dccb;">
          <tr>
            <td style="background:#17130f;padding:22px 26px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                <tr>
                  <td style="font-size:20px;font-weight:bold;color:#fdf7ed;letter-spacing:.2px;">
                    Sabor Sinú
                    <div style="font-size:11px;font-weight:normal;color:#c9b8a2;letter-spacing:1.6px;text-transform:uppercase;margin-top:3px;">
                      Cocina rápida costeña · Montería
                    </div>
                  </td>
                  <td align="right" style="font-size:11px;color:#f2a93b;font-weight:bold;letter-spacing:1.4px;text-transform:uppercase;">
                    Nuevo pedido
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          {demo_banner}
          <tr>
            <td style="padding:24px 26px 8px;">
              <div style="font-size:12px;color:#8a7f72;text-transform:uppercase;letter-spacing:1.4px;">Pedido</div>
              <div style="font-size:26px;font-weight:bold;color:#17130f;margin:4px 0 2px;">{html.escape(order_id)}</div>
              <div style="font-size:13px;color:#6f655a;">{html.escape(format_datetime(order.get('created_ts', 0)))}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 26px;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#fff8ee;border:1px solid #f0dfc4;border-radius:14px;">
                <tr>
                  <td style="padding:16px 18px;">
                    <div style="font-size:11px;color:#8a7f72;text-transform:uppercase;letter-spacing:1.3px;">Total del pedido</div>
                    <div style="font-size:30px;font-weight:bold;color:#c7431f;margin-top:2px;">{html.escape(total)}</div>
                    <div style="font-size:12px;color:#6f655a;margin-top:4px;">Pago: <strong>{html.escape(payment)}</strong></div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:4px 26px 0;">
              <div style="font-size:12px;color:#8a7f72;text-transform:uppercase;letter-spacing:1.3px;margin-bottom:8px;">Datos de entrega</div>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="font-size:14px;color:#241f1a;line-height:1.6;">
                <tr><td width="110" style="color:#8a7f72;padding:3px 0;">Cliente</td><td style="padding:3px 0;"><strong>{html.escape(customer.get('name', ''))}</strong></td></tr>
                <tr><td style="color:#8a7f72;padding:3px 0;">Celular</td><td style="padding:3px 0;"><a href="tel:+57{html.escape(phone_digits)}" style="color:#c7431f;text-decoration:none;">{html.escape(customer.get('phone', ''))}</a></td></tr>
                <tr><td style="color:#8a7f72;padding:3px 0;vertical-align:top;">Dirección</td><td style="padding:3px 0;">{html.escape(customer.get('address', ''))}</td></tr>
                <tr><td style="color:#8a7f72;padding:3px 0;">Barrio</td><td style="padding:3px 0;">{html.escape(customer.get('barrio', ''))}</td></tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 26px 0;">
              <div style="font-size:12px;color:#8a7f72;text-transform:uppercase;letter-spacing:1.3px;margin-bottom:8px;">Productos</div>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                {_item_rows(order)}
              </table>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:12px;font-size:14px;color:#241f1a;">
                <tr><td style="padding:5px 10px;color:#6f655a;">Subtotal</td><td align="right" style="padding:5px 10px;">{money(order.get('subtotal', 0))}</td></tr>
                <tr><td style="padding:5px 10px;color:#6f655a;">Domicilio</td><td align="right" style="padding:5px 10px;">{delivery_fee}</td></tr>
                <tr>
                  <td style="padding:12px 10px;border-top:2px solid #17130f;font-weight:bold;font-size:16px;">Total</td>
                  <td align="right" style="padding:12px 10px;border-top:2px solid #17130f;font-weight:bold;font-size:18px;color:#c7431f;">{total}</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 26px 26px;">
              <div style="background:#f7f3ec;border-radius:12px;padding:14px 16px;font-size:13px;color:#4b4239;line-height:1.55;">
                <strong style="color:#17130f;">Observaciones:</strong> {html.escape(notes)}
              </div>
              <div style="margin-top:16px;font-size:12px;color:#8a7f72;line-height:1.6;">
                Tiempo estimado de entrega: {order.get('prep_minutes', 25) + order.get('delivery_minutes', 20)} minutos.<br>
                Teléfono del cliente: <a href="tel:+57{html.escape(phone_digits)}" style="color:#c7431f;">{html.escape(customer.get('phone', ''))}</a>
              </div>
            </td>
          </tr>
          <tr>
            <td style="background:#17130f;padding:16px 26px;color:#c9b8a2;font-size:11px;line-height:1.6;">
              Correo automático generado por la Web App de {html.escape(settings.business_name)}.<br>
              {html.escape(settings.business_address)} · {html.escape(settings.business_phone)}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_lines = [
        f"NUEVO PEDIDO {order_id}",
        f"Fecha: {format_datetime(order.get('created_ts', 0))}",
        "",
        f"Cliente: {customer.get('name', '')}",
        f"Celular: {customer.get('phone', '')}",
        f"Dirección: {customer.get('address', '')}",
        f"Barrio: {customer.get('barrio', '')}",
        f"Pago: {payment}",
        "",
        "PRODUCTOS",
    ]
    for item in order.get("items", []):
        extras = [mod["name"] for mod in item.get("modifiers", [])] + item.get("notes", [])
        detail = f" ({', '.join(extras)})" if extras else ""
        text_lines.append(
            f"- {item.get('qty', 1)} x {item.get('name', '')}{detail}: {money(item.get('line_total', 0))}"
        )
    text_lines += [
        "",
        f"Subtotal: {money(order.get('subtotal', 0))}",
        f"Domicilio: {delivery_fee}",
        f"TOTAL: {total}",
        "",
        f"Observaciones: {notes}",
        f"Teléfono del negocio: {settings.business_phone}",
    ]
    return subject, "\n".join(text_lines), html_body


def send_order_email(order: dict, settings: Settings) -> dict:
    """Envía (o simula) el correo. Nunca lanza excepciones."""
    recipients = list(settings.admin_emails)
    dry_run = settings.email_dry_run or not settings.smtp_host or not recipients
    subject, text_body, html_body = build_email(order, settings, dry_run=dry_run)

    if dry_run:
        settings.ensure_dirs()
        base = settings.outbox_dir / order["id"]
        try:
            base.with_suffix(".html").write_text(html_body, encoding="utf-8")
            base.with_suffix(".txt").write_text(text_body, encoding="utf-8")
            logger.info("Correo en modo demo guardado en %s.html", base)
            return {
                "status": "dry_run",
                "recipients": recipients,
                "error": None,
                "path": str(base.with_suffix(".html")),
            }
        except OSError as exc:
            logger.exception("No se pudo guardar el correo de demo")
            return {"status": "failed", "recipients": recipients, "error": str(exc)}

    from_email = settings.smtp_from or settings.smtp_user
    if not from_email:
        logger.error("Falta SMTP_FROM o SMTP_USER; no se puede enviar el correo")
        return {
            "status": "failed",
            "recipients": recipients,
            "error": "Configuración SMTP incompleta (SMTP_FROM/SMTP_USER)",
        }

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.smtp_from_name, from_email))
    message["To"] = ", ".join(recipients)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        if settings.smtp_use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout,
                context=context,
            ) as server:
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
            ) as server:
                server.ehlo()
                if settings.smtp_use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
    except Exception as exc:  # noqa: BLE001 - se reporta al admin sin romper el pedido
        logger.exception("Error enviando correo del pedido %s", order["id"])
        return {"status": "failed", "recipients": recipients, "error": str(exc)[:300]}

    logger.info("Correo del pedido %s enviado a %s", order["id"], recipients)
    return {"status": "sent", "recipients": recipients, "error": None}
