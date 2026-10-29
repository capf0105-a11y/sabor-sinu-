"""Motor conversacional de Sabor Sinú.

Máquina de estados simple y auditable:
browsing → checkout_name → checkout_address → checkout_barrio →
checkout_phone → checkout_payment → checkout_confirm → confirmed.

El motor no conoce FastAPI ni SMTP: recibe una sesión, devuelve bloques de
respuesta y crea el pedido en el almacén. El envío de correo se agenda en la capa
HTTP para no hacer esperar al cliente.
"""

from __future__ import annotations

import re

from .config import Settings
from .menu import (
    ALL_PRODUCT_IDS,
    COMMON_BARRIOS,
    PRODUCTS_BY_ID,
    ZONES,
    display_barrio,
    find_zone,
)
from .nlu import (
    detect_intent,
    explicit_quantity,
    extract_address,
    extract_name,
    extract_order_code,
    extract_payment,
    extract_phone,
    format_phone,
    has_add_verb,
    has_remove_verb,
    is_info_question,
    is_no,
    is_yes,
    parse_order,
    split_segments,
)
from .orders import PAYMENT_LABELS, OrderStore, public_order
from .sessions import (
    CartItem,
    Session,
    add_to_cart,
    cart_count,
    cart_public,
    cart_subtotal,
    cart_summary_text,
    cart_totals,
    change_qty,
    find_item,
    items_for_order,
    totals_line,
)
from .stock import StockStore
from .utils import money, normalize

def _strip_barrio_from_address(address: str, barrio: str) -> str:
    """Evita mostrar dos veces el barrio cuando el cliente lo incluye en la dirección."""
    if not address or not barrio:
        return address
    pattern = re.compile(
        r"[,;\s]*(?:barrio|br\.?|urbanizaci[oó]n)?\s*" + re.escape(barrio) + r"\s*$",
        re.IGNORECASE,
    )
    cleaned = pattern.sub("", address).strip(" ,;.-")
    return cleaned if len(cleaned) >= 6 else address


STRONG_ADD = re.compile(
    r"\b(agrega|agregame|anade|suma|sumame|dame|me das|pon|ponme|trae|traeme|llevo|me llevo)\b"
)
INGREDIENT_REMOVE = re.compile(
    r"\b(?:quita|quitar|quitale|elimina|eliminar|saca|sacar)\s+(?:el |la |los |las )?"
    r"(queso|cebolla|tomate|lechuga|salsa|tocineta|maiz|platano|chorizo|picante|hielo)\b"
)
OTRO_PEDIDO = re.compile(r"\b(otro|nuevo|otra vez|empezar de nuevo)\b.*\bpedido\b")


def text(message: str) -> dict:
    return {"type": "text", "text": message}


def products_block(product_ids: list[str], title: str | None = None) -> dict:
    block: dict = {"type": "products", "product_ids": product_ids}
    if title:
        block["title"] = title
    return block


def quick(label: str, value: str | None = None) -> dict:
    return {"label": label, "value": value or label}


class ChatEngine:
    def __init__(
        self, settings: Settings, store: OrderStore, stock: StockStore | None = None
    ) -> None:
        self.settings = settings
        self.store = store
        self.stock = stock

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def handle(self, session: Session, message: str) -> dict:
        raw = (message or "").strip()[: self.settings.max_message_chars]
        norm = normalize(raw)
        session.touch()

        if not norm:
            return self._resp(
                session,
                [text("Escríbeme tu pedido o toca **Ver el menú** para empezar 😊")],
                self._default_quick(session),
            )

        intent = detect_intent(norm)
        order_code = extract_order_code(norm)

        # Un pedido ya confirmado abre una conversación nueva, salvo que se
        # consulte el estado o se pida otro pedido.
        if session.stage == "confirmed":
            if OTRO_PEDIDO.search(norm):
                session.cart.clear()
                session.customer.clear()
                session.stage = "browsing"
                session.last_order_id = None
                return self._welcome(session)
            if intent != "status" and not order_code:
                session.stage = "browsing"

        # 1. Cancelar proceso de compra
        if intent == "cancel":
            return self._cancel(session)

        # 2. Consultar estado por código explícito
        if order_code:
            return self._status_response(session, order_code)

        # 3. Durante el checkout se mantiene el foco, pero se permiten preguntas
        if session.stage.startswith("checkout_"):
            return self._during_checkout(session, raw, norm, intent)

        # 4. Estado del último pedido
        if intent == "status":
            return self._status_response(session, session.last_order_id)

        # 5. Vaciar carrito / quitar último
        if intent == "clear_cart":
            if not session.cart:
                return self._resp(
                    session, [text("Tu carrito ya está vacío.")], self._default_quick(session)
                )
            session.cart.clear()
            return self._resp(
                session,
                [text("Listo, vacié el carrito. Cuando quieras armamos otro pedido 💪")],
                [quick("Ver el menú"), quick("Recomiéndame algo")],
            )

        if intent == "remove_last":
            if not session.cart:
                return self._resp(
                    session, [text("No hay productos en el carrito para quitar.")], self._default_quick(session)
                )
            removed = session.cart.pop()
            return self._resp(
                session,
                [
                    text(
                        f"Quité **{removed.qty} × {removed.product.name}** del carrito.\n"
                        f"{totals_line(session.cart, self._barrio(session), self.settings)}"
                    )
                ],
                self._default_quick(session),
            )

        # 6. Quitar productos o ingredientes
        if has_remove_verb(norm):
            removed = self._handle_removal(session, raw, norm)
            if removed:
                return removed

        # 7. Productos: consulta de precio, agregado real o inicio de cierre
        parsed = parse_order(raw)
        if parsed.lines:
            if is_info_question(norm) and not STRONG_ADD.search(norm):
                return self._info_response(session, parsed)
            return self._add_response(session, parsed, start_checkout=(intent == "checkout_start"))

        # 8. Inicio explícito de checkout (sin productos nuevos en el mensaje)
        if intent == "checkout_start":
            if not session.cart:
                return self._empty_cart_reply(session)
            return self._begin_checkout(session)

        # 9. Modificadores sueltos ("sin cebolla", "extra queso")
        if parsed.orphan_modifiers or parsed.orphan_notes:
            return self._orphan_adjustment(session, parsed)

        # 10. Intenciones informativas
        if intent in {
            "menu",
            "recommendation",
            "delivery",
            "payments",
            "hours",
            "location",
            "human",
            "help",
            "greeting",
            "thanks",
            "bye",
        }:
            return self._intent_response(intent, session)

        # 11. Fallback
        return self._fallback(session)

    def apply_cart_action(
        self,
        session: Session,
        action: str,
        product_id: str | None = None,
        uid: str | None = None,
        qty: int | None = None,
    ) -> dict:
        """Acciones directas desde las tarjetas o la barra lateral del carrito."""
        if session.stage == "confirmed":
            session.stage = "browsing"
        if session.stage.startswith("checkout_"):
            # Editar el carrito invalida el resumen de confirmación.
            session.stage = "browsing"
            session.customer = {}

        if action == "add":
            product = PRODUCTS_BY_ID.get(product_id or "")
            if not product:
                raise ValueError("Producto no encontrado.")
            if self.stock and not self.stock.is_available(product.id):
                raise ValueError(f"Por hoy no tenemos {product.name}. Mira el resto del menú.")
            add_to_cart(session.cart, product, qty or 1)
        elif action in {"inc", "dec"}:
            item = find_item(session.cart, uid or "")
            if not item:
                raise ValueError("El producto ya no está en el carrito.")
            change_qty(session.cart, item.uid, 1 if action == "inc" else -1)
        elif action == "remove":
            item = find_item(session.cart, uid or "")
            if not item:
                raise ValueError("El producto ya no está en el carrito.")
            session.cart.remove(item)
        elif action == "clear":
            session.cart.clear()
        else:
            raise ValueError("Acción no válida.")

        session.touch()
        return cart_public(session.cart, self._barrio(session), self.settings)

    def resume(self, session: Session) -> dict:
        if session.stage.startswith("checkout_"):
            return {
                "blocks": [
                    text("Continuamos con tu pedido donde lo dejaste."),
                    text(self._checkout_question(session)),
                ],
                "quick_replies": self._checkout_quick(session),
            }
        return {"blocks": [], "quick_replies": []}

    # ------------------------------------------------------------------
    # Respuestas base
    # ------------------------------------------------------------------

    def _barrio(self, session: Session) -> str | None:
        return session.customer.get("barrio")

    def _resp(
        self,
        session: Session,
        blocks: list[dict],
        quick_replies: list[dict] | None = None,
        order: dict | None = None,
        notify_order_id: str | None = None,
    ) -> dict:
        payload = {
            "session_id": session.id,
            "stage": session.stage,
            "blocks": blocks,
            "quick_replies": quick_replies or [],
            "cart": cart_public(session.cart, self._barrio(session), self.settings),
            "order": order,
        }
        if notify_order_id:
            payload["_notify_order_id"] = notify_order_id
        return payload

    def _default_quick(self, session: Session) -> list[dict]:
        items = [quick("Ver el menú"), quick("Recomiéndame algo")]
        if session.cart:
            items.insert(0, quick("Finalizar pedido"))
        items.append(quick("Estado de mi pedido"))
        return items

    def _welcome(self, session: Session) -> dict:
        name = self.settings.business_name
        blocks = [
            text(
                f"¡Hola! Soy **Sinu**, el asistente de {name} 🌭\n"
                "Puedo armar tu pedido, calcular el domicilio y dejarlo listo en minutos.\n\n"
                f"**Horario:** {self.settings.business_hours}\n"
                f"**Domicilios:** desde {money(self.settings.delivery_base_fee)} · "
                f"gratis desde {money(self.settings.free_delivery_from)}.\n"
                f"**Pagos:** Nequi o Llave al {self.settings.business_nequi}."
            )
        ]
        if session.cart:
            blocks.append(
                text(
                    f"Veo que tienes **{cart_count(session.cart)} artículo(s)** en el carrito "
                    f"por **{money(cart_subtotal(session.cart))}**. ¿Seguimos?"
                )
            )
        return self._resp(session, blocks, self._default_quick(session))

    def _empty_cart_reply(self, session: Session) -> dict:
        return self._resp(
            session,
            [
                text(
                    "Tu carrito está vacío, así que primero elige algo rico 😋\n"
                    "Estos son los favoritos de la casa:"
                ),
                products_block(
                    ["suizo", "hamburguesa", "salchipapa"], "Los más pedidos"
                ),
            ],
            [quick("Ver el menú"), quick("Recomiéndame algo")],
        )

    def _fallback(self, session: Session) -> dict:
        return self._resp(
            session,
            [
                text(
                    "No estoy seguro de haberte entendido 🤔. Puedo ayudarte con:\n"
                    "• Armar tu pedido: *“dos suizos y una gaseosa”*\n"
                    "• Consultar precios o ingredientes\n"
                    "• Calcular el domicilio en Montería\n"
                    "• Cerrar el pedido y enviarlo a cocina"
                )
            ],
            self._default_quick(session),
        )

    # ------------------------------------------------------------------
    # Carrito
    # ------------------------------------------------------------------

    def _add_response(self, session: Session, parsed, start_checkout: bool = False) -> dict:
        before_subtotal = cart_subtotal(session.cart)

        unavailable = []
        if self.stock:
            unavailable = [
                line.product
                for line in parsed.lines
                if not self.stock.is_available(line.product.id)
            ]
            if unavailable:
                parsed.lines = [
                    line
                    for line in parsed.lines
                    if self.stock.is_available(line.product.id)
                ]
        if unavailable and not parsed.lines:
            names = ", ".join(dict.fromkeys(product.name for product in unavailable))
            return self._resp(
                session,
                [
                    text(
                        f"Por hoy no tenemos **{names}** 😔. "
                        "¿Te ofrezco otra cosa del menú?"
                    )
                ],
                [quick("Ver el menú"), quick("Recomiéndame algo")],
            )

        added_lines = []
        for line in parsed.lines:
            item = add_to_cart(
                session.cart, line.product, line.qty, line.modifiers, line.notes
            )
            added_lines.append((line, item))

        bullets = []
        for line, _item in added_lines:
            detail = [mod["name"] for mod in line.modifiers] + line.notes
            suffix = f" · {', '.join(detail)}" if detail else ""
            bullets.append(f"• **{line.qty} × {line.product.name}**{suffix}")

        subtotal = cart_subtotal(session.cart)
        blocks = [text("¡Buen gusto! Agregué a tu pedido:\n" + "\n".join(bullets))]
        if parsed.orphan_modifiers or parsed.orphan_notes:
            blocks[0]["text"] += "\n\n(Anoté también los ajustes que pediste)."

        blocks.append(
            text(
                f"Llevas **{cart_count(session.cart)} artículo(s)** · "
                f"Subtotal **{money(subtotal)}**."
            )
        )

        if subtotal >= self.settings.free_delivery_from and before_subtotal < self.settings.free_delivery_from:
            blocks.append(text("🚚 ¡Desbloqueaste el **domicilio gratis**!"))
        elif 0 < self.settings.free_delivery_from - subtotal <= 15000:
            blocks.append(
                text(
                    f"Te faltan **{money(self.settings.free_delivery_from - subtotal)}** "
                    "para el domicilio gratis."
                )
            )

        if start_checkout:
            return self._begin_checkout(session, prefix_blocks=blocks)

        blocks.append(text("¿Quieres algo más o cerramos el pedido?"))
        return self._resp(
            session,
            blocks,
            [quick("Finalizar pedido"), quick("Ver el menú"), quick("Quitar último")],
        )

    def _info_response(self, session: Session, parsed) -> dict:
        lines = []
        ids: list[str] = []
        for line in parsed.lines:
            product = line.product
            if product.id not in ids:
                ids.append(product.id)
            lines.append(f"• **{product.name}** — **{money(product.price)}**")
        blocks = [
            text("Claro, esto es lo que buscas:\n" + "\n".join(lines)),
            text(parsed.lines[0].product.description),
            products_block(ids),
        ]
        first = parsed.lines[0].product
        return self._resp(
            session,
            blocks,
            [
                quick(f"Agregar {first.name}", f"Agregar {first.name}"),
                quick("Ver el menú"),
            ],
        )

    def _handle_removal(self, session: Session, raw: str, norm: str) -> dict | None:
        if not session.cart:
            return self._resp(
                session,
                [text("Tu carrito está vacío; no hay nada que quitar.")],
                self._default_quick(session),
            )

        parsed = parse_order(raw)
        if parsed.lines:
            explicit = explicit_quantity(norm)
            removed_labels: list[str] = []
            for line in parsed.lines:
                remaining = explicit
                for item in list(session.cart):
                    if item.product_id != line.product.id:
                        continue
                    if remaining is None:
                        session.cart.remove(item)
                        removed_labels.append(f"{item.qty} × {item.product.name}")
                        continue
                    take = min(item.qty, remaining)
                    item.qty -= take
                    remaining -= take
                    removed_labels.append(f"{take} × {item.product.name}")
                    if item.qty <= 0:
                        session.cart.remove(item)
                    if remaining <= 0:
                        break
            if removed_labels:
                return self._resp(
                    session,
                    [
                        text(
                            "Listo, quité " + ", ".join(removed_labels) + ".\n"
                            + totals_line(session.cart, self._barrio(session), self.settings)
                        )
                    ],
                    self._default_quick(session),
                )
            return self._resp(
                session,
                [text("No encontré ese producto en tu carrito.")],
                self._default_quick(session),
            )

        ingredient = INGREDIENT_REMOVE.search(norm)
        if ingredient:
            label = ingredient.group(1)
            item = session.cart[-1]
            note = f"Sin {label}"
            if note not in item.notes:
                item.notes.append(note)
            return self._resp(
                session,
                [
                    text(
                        f"Anotado: **{note}** en **{item.product.name}**. "
                        f"El valor se mantiene en {money(item.line_total)}."
                    )
                ],
                self._default_quick(session),
            )
        return None

    def _orphan_adjustment(self, session: Session, parsed) -> dict:
        if not session.cart:
            return self._resp(
                session,
                [
                    text(
                        "Para aplicar ese ajuste primero agrega un producto. "
                        "¿Te muestro el menú?"
                    )
                ],
                [quick("Ver el menú"), quick("Recomiéndame algo")],
            )
        item = session.cart[-1]
        changes = []
        for modifier in parsed.orphan_modifiers:
            if all(mod["key"] != modifier["key"] for mod in item.modifiers):
                item.modifiers.append(modifier)
                changes.append(modifier["name"])
        for note in parsed.orphan_notes:
            if note not in item.notes:
                item.notes.append(note)
                changes.append(note)
        return self._resp(
            session,
            [
                text(
                    f"Actualicé **{item.product.name}**: {', '.join(changes)}.\n"
                    f"Nuevo valor: **{money(item.line_total)}**."
                )
            ],
            self._default_quick(session),
        )

    # ------------------------------------------------------------------
    # Checkout
    # ------------------------------------------------------------------

    def _drop_sold_out(self, session: Session) -> list[str]:
        """Quita del carrito lo que el vendedor marcó agotado y devuelve sus nombres."""
        if not self.stock:
            return []
        removed = [
            item for item in session.cart if not self.stock.is_available(item.product_id)
        ]
        if not removed:
            return []
        session.cart = [
            item for item in session.cart if self.stock.is_available(item.product_id)
        ]
        return list(dict.fromkeys(item.product.name for item in removed))

    def _begin_checkout(self, session: Session, prefix_blocks: list[dict] | None = None) -> dict:
        session.stage = "checkout_name"
        session.customer = {}
        blocks = list(prefix_blocks or [])
        sold_out = self._drop_sold_out(session)
        if sold_out:
            blocks.append(
                text(
                    "Uy, se agotó **" + ", ".join(sold_out) + "** y lo quité de tu pedido. "
                    "Puedes elegir otra cosa del menú."
                )
            )
        if not session.cart:
            session.stage = "browsing"
            return self._empty_cart_reply(session)
        blocks.append(
            text(
                f"Perfecto, cerramos tu pedido 🧾\n\n"
                f"**{cart_summary_text(session.cart)}**\n"
                f"{totals_line(session.cart, None, self.settings)}\n\n"
                "Para la entrega necesito tu **nombre completo**."
            )
        )
        return self._resp(session, blocks, [])

    def _during_checkout(self, session: Session, raw: str, norm: str, intent: str | None) -> dict:
        if intent == "status" or extract_order_code(norm):
            response = self._status_response(session, extract_order_code(norm) or session.last_order_id)
            response["blocks"].append(text("Retomemos tu pedido. " + self._checkout_question(session)))
            response["quick_replies"] = self._checkout_quick(session)
            return response

        if intent in {
            "menu",
            "recommendation",
            "delivery",
            "payments",
            "hours",
            "location",
            "human",
            "help",
        } and not (intent == "payments" and session.stage == "checkout_payment"):
            response = self._intent_response(intent, session)
            response["blocks"].append(text("Retomemos tu pedido. " + self._checkout_question(session)))
            response["quick_replies"] = self._checkout_quick(session)
            return response

        if intent == "cancel":
            return self._cancel(session)

        parsed = parse_order(raw)
        if parsed.lines:
            added = self._add_response(session, parsed)
            added["blocks"].append(text(self._checkout_question(session)))
            added["quick_replies"] = self._checkout_quick(session)
            return added

        return self._checkout_step(session, raw, norm)

    def _checkout_question(self, session: Session) -> str:
        stage = session.stage
        if stage == "checkout_name":
            return "¿Cuál es tu **nombre completo**?"
        if stage == "checkout_address":
            return (
                "¿A qué **dirección exacta** te llevamos el pedido? Incluye calle o carrera, "
                "número y una referencia (ej: *Calle 12 #4-56, frente al parque*)."
            )
        if stage == "checkout_barrio":
            return "¿En qué **barrio** queda la dirección? Así calculo el domicilio exacto."
        if stage == "checkout_phone":
            return "¿Cuál es tu **número de celular**? Lo usamos solo para coordinar la entrega (10 dígitos)."
        if stage == "checkout_payment":
            return (
                "¿Cómo prefieres pagar? Solo manejamos **Nequi** o **Llave** "
                f"al **{self.settings.business_nequi}**."
            )
        if stage == "checkout_confirm":
            return "¿Confirmas tu pedido? Si todo está bien, lo envío a cocina de inmediato."
        return "¿Continuamos con tu pedido?"

    def _checkout_quick(self, session: Session) -> list[dict]:
        if session.stage == "checkout_barrio":
            return [quick(barrio) for barrio in COMMON_BARRIOS]
        if session.stage == "checkout_payment":
            return [quick("Nequi"), quick("Llave")]
        if session.stage == "checkout_confirm":
            return [quick("Confirmar pedido"), quick("Agregar algo más"), quick("Cancelar")]
        return []

    def _checkout_step(self, session: Session, raw: str, norm: str) -> dict:
        stage = session.stage

        if stage == "checkout_name":
            name = extract_name(raw)
            if not name:
                return self._resp(
                    session,
                    [
                        text(
                            "Necesito tu **nombre completo** para el pedido "
                            "(solo letras, por ejemplo: *María Fernanda Gómez*)."
                        )
                    ],
                    [],
                )
            session.customer["name"] = name
            session.stage = "checkout_address"
            return self._resp(
                session,
                [
                    text(
                        f"Gracias, **{name.split()[0]}**. "
                        + self._checkout_question(session)
                    )
                ],
                [],
            )

        if stage == "checkout_address":
            address = extract_address(raw)
            if not address:
                return self._resp(
                    session,
                    [
                        text(
                            "No alcancé a entender la dirección. Escríbela con calle/carrera y número, "
                            "por ejemplo: *Calle 12 #4-56, frente al parque*."
                        )
                    ],
                    [],
                )
            session.customer["address"] = address
            found = find_zone(address)
            if found:
                barrio, zone = found
                address = _strip_barrio_from_address(address, barrio)
                session.customer["address"] = address
                session.customer["barrio"] = barrio
                session.customer["zone"] = zone.name
                session.customer["delivery_fee"] = zone.fee
                session.stage = "checkout_phone"
                return self._resp(
                    session,
                    [
                        text(
                            f"Perfecto: **{address}** ({barrio}).\n"
                            f"Domicilio **{money(zone.fee)}**.\n\n"
                            + self._checkout_question(session)
                        )
                    ],
                    [],
                )
            session.stage = "checkout_barrio"
            return self._resp(
                session,
                [
                    text(
                        f"Anoté **{address}**. "
                        + self._checkout_question(session)
                    )
                ],
                self._checkout_quick(session),
            )

        if stage == "checkout_barrio":
            if re.search(r"\b(no se|no idea|no sabria|no tengo)\b", norm):
                return self._resp(
                    session,
                    [
                        text(
                            "Sin problema. Escríbeme el barrio o una referencia cercana "
                            "(por ejemplo: *cerca del parque Simón Bolívar*)."
                        )
                    ],
                    self._checkout_quick(session),
                )
            found = find_zone(raw)
            if found:
                barrio, zone = found
                session.customer["barrio"] = barrio
                session.customer["zone"] = zone.name
                session.customer["delivery_fee"] = zone.fee
                fee_text = f"Domicilio **{money(zone.fee)}**."
            else:
                cleaned = re.sub(r"\s+", " ", raw.strip(" .,-"))
                if len(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", cleaned)) < 4:
                    return self._resp(
                        session,
                        [text("¿Me confirmas el nombre del barrio, por favor?")],
                        self._checkout_quick(session),
                    )
                session.customer["barrio"] = display_barrio(cleaned)[:60]
                session.customer["zone"] = "Por confirmar"
                session.customer["delivery_fee"] = self.settings.delivery_base_fee
                fee_text = (
                    f"El domicilio queda en **{money(self.settings.delivery_base_fee)}** "
                    "y el despacho confirma la tarifa exacta."
                )
            session.stage = "checkout_phone"
            return self._resp(
                session,
                [
                    text(
                        f"Listo, barrio **{session.customer['barrio']}**. {fee_text}\n\n"
                        + self._checkout_question(session)
                    )
                ],
                [],
            )

        if stage == "checkout_phone":
            phone = extract_phone(raw)
            if not phone:
                return self._resp(
                    session,
                    [
                        text(
                            "Ese número no parece válido. Necesito un celular colombiano de 10 dígitos "
                            "(ej: *300 123 4567*)."
                        )
                    ],
                    [],
                )
            session.customer["phone"] = phone
            session.stage = "checkout_payment"
            return self._resp(
                session,
                [text(f"Gracias. {self._checkout_question(session)}")],
                self._checkout_quick(session),
            )

        if stage == "checkout_payment":
            payment = extract_payment(norm)
            if payment == "unsupported":
                return self._resp(
                    session,
                    [
                        text(
                            "Por ahora solo recibimos **Nequi** o **Llave** al "
                            f"**{self.settings.business_nequi}**."
                        )
                    ],
                    self._checkout_quick(session),
                )
            if not payment:
                return self._resp(
                    session,
                    [
                        text(
                            "Elige un método de pago para continuar: **Nequi** o **Llave** "
                            f"al **{self.settings.business_nequi}**."
                        )
                    ],
                    self._checkout_quick(session),
                )
            session.customer["payment"] = payment
            session.stage = "checkout_confirm"
            return self._resp(
                session,
                [
                    text(
                        "Perfecto. Revisa tu pedido 👇 Si todo está bien, confírmalo y lo "
                        "enviamos a cocina."
                    ),
                    self._summary_block(session),
                ],
                self._checkout_quick(session),
            )

        if stage == "checkout_confirm":
            if is_no(norm):
                return self._cancel(session)
            if not is_yes(norm):
                return self._resp(
                    session,
                    [
                        text(
                            "Solo necesito que me confirmes con **“Confirmar pedido”** o "
                            "me digas si quieres cambiar algo."
                        )
                    ],
                    self._checkout_quick(session),
                )
            return self._confirm_order(session)

        # Estado desconocido: se reinicia el cierre de forma segura.
        session.stage = "browsing"
        return self._fallback(session)

    def _summary_block(self, session: Session) -> dict:
        customer = session.customer
        totals = cart_totals(session.cart, customer.get("barrio"), self.settings)
        return {
            "type": "summary",
            "summary": {
                "customer": {
                    "name": customer.get("name", ""),
                    "address": customer.get("address", ""),
                    "barrio": customer.get("barrio", ""),
                    "phone": format_phone(customer.get("phone", "")),
                    "payment": customer.get("payment", ""),
                    "payment_label": PAYMENT_LABELS.get(
                        customer.get("payment", ""), customer.get("payment", "")
                    ),
                },
                "items": cart_public(
                    session.cart, customer.get("barrio"), self.settings
                )["items"],
                "subtotal": totals["subtotal"],
                "delivery_fee": totals["delivery_fee"],
                "total": totals["total"],
                "free_delivery": totals["free_delivery"],
                "eta_label": (
                    f"{self.settings.prep_minutes + self.settings.delivery_minutes} min aprox."
                ),
            },
        }

    def _confirm_order(self, session: Session) -> dict:
        sold_out = self._drop_sold_out(session)
        if sold_out:
            session.stage = "browsing"
            return self._resp(
                session,
                [
                    text(
                        "Uy, se agotó **" + ", ".join(sold_out) + "** y lo quité de tu pedido. "
                        "Revisa tu carrito y confirma de nuevo."
                    )
                ],
                [quick("Ver el menú"), quick("Finalizar pedido")],
            )
        if not session.cart:
            return self._empty_cart_reply(session)
        totals = cart_totals(session.cart, session.customer.get("barrio"), self.settings)
        customer = dict(session.customer)
        order = self.store.create(
            customer=customer,
            items=items_for_order(session.cart),
            subtotal=totals["subtotal"],
            delivery_fee=totals["delivery_fee"],
            total=totals["total"],
            prep_minutes=self.settings.prep_minutes,
            delivery_minutes=self.settings.delivery_minutes,
        )
        public = public_order(order)
        session.cart.clear()
        session.customer.clear()
        session.stage = "confirmed"
        session.last_order_id = order["id"]

        eta = self.settings.prep_minutes + self.settings.delivery_minutes
        payment = customer.get("payment", "")
        payment_label = PAYMENT_LABELS.get(payment, payment)
        payment_number = (
            self.settings.business_nequi
            if payment == "nequi"
            else self.settings.business_llave
        )
        blocks = [
            text(
                f"¡Pedido recibido! 🎉\n\n"
                f"**{public['id']}** · Total **{money(public['total'])}**\n"
                f"Pago: **{payment_label}** al **{payment_number}**\n"
                "Envía la **captura del comprobante** por WhatsApp para verificar tu pago.\n"
                f"Te contactamos al **{format_phone(customer.get('phone', ''))}** si necesitamos "
                f"confirmar algo. Llega en aproximadamente **{eta} minutos**."
            ),
            {"type": "success", "order": public},
        ]
        return self._resp(
            session,
            blocks,
            [
                quick("Ver estado del pedido", "Estado de mi pedido"),
                quick("Llamar al despacho", self.settings.phone_link),
                quick("Hacer otro pedido", "Quiero hacer otro pedido"),
            ],
            order=public,
            notify_order_id=order["id"],
        )

    def _cancel(self, session: Session) -> dict:
        if session.stage.startswith("checkout_"):
            session.stage = "browsing"
            session.customer = {}
            return self._resp(
                session,
                [
                    text(
                        "Cancelé el proceso de compra. Tu carrito sigue intacto por si quieres "
                        "retomarlo cuando quieras."
                    )
                ],
                self._default_quick(session),
            )
        if session.stage == "confirmed":
            return self._resp(
                session,
                [
                    text(
                        "Para cancelar un pedido ya confirmado, llama directamente al despacho; "
                        "lo atendemos de inmediato."
                    )
                ],
                [
                    quick("Llamar al despacho", self.settings.phone_link),
                    quick("Ver estado del pedido", "Estado de mi pedido"),
                ],
            )
        return self._resp(
            session,
            [
                text(
                    "No hay un pedido en curso. Si quieres vaciar el carrito, dime "
                    "**“vaciar carrito”**."
                )
            ],
            self._default_quick(session),
        )

    # ------------------------------------------------------------------
    # Estado del pedido
    # ------------------------------------------------------------------

    def _status_response(self, session: Session, code: str | None) -> dict:
        if not code:
            return self._resp(
                session,
                [
                    text(
                        "Aún no tengo un pedido registrado en esta conversación. "
                        "Envíame tu número de pedido (ej: **SINU-261028-4F7A**) o arma uno nuevo."
                    )
                ],
                [quick("Ver el menú"), quick("Finalizar pedido")],
            )
        order = self.store.get(code)
        if not order:
            return self._resp(
                session,
                [
                    text(
                        f"No encontré el pedido **{code}**. Revisa el código o llama al despacho "
                        "para verificarlo."
                    )
                ],
                [
                    quick("Llamar al despacho", self.settings.phone_link),
                    quick("Ver el menú"),
                ],
            )
        public = public_order(order)
        status = public["status"]
        return self._resp(
            session,
            [
                text(
                    f"Tu pedido **{public['id']}** está **{status['label']}**.\n"
                    f"{status['description']}\n"
                    f"Total: **{money(public['total'])}** · "
                    f"Llega en ~**{status['eta_remaining_minutes']} min**."
                ),
                {"type": "status", "order": public},
            ],
            [
                quick("Actualizar estado", "Estado de mi pedido"),
                quick("Llamar al despacho", self.settings.phone_link),
            ],
        )

    # ------------------------------------------------------------------
    # Intenciones informativas
    # ------------------------------------------------------------------

    def _intent_response(self, intent: str, session: Session) -> dict:
        if intent == "menu":
            return self._resp(
                session,
                [
                    text(
                        "Esta es nuestra carta 🔥 Toca **+ / −** para elegir la cantidad y luego "
                        "**Agregar** para sumarla al carrito."
                    ),
                    products_block(list(ALL_PRODUCT_IDS), "Menú completo"),
                ],
                [
                    quick("Recomiéndame algo"),
                    quick("Finalizar pedido"),
                    quick("Ver zonas de domicilio", "¿Hacen domicilios?"),
                ],
            )

        if intent == "recommendation":
            return self._resp(
                session,
                [
                    text(
                        "Si es tu primera vez, te recomiendo el **Suizo con Lomito Monteriano** "
                        "(el más vendido), la **Hamburguesa Sinuana** y la **Salchipapa Salvajada** "
                        "para compartir."
                    ),
                    products_block(["suizo", "hamburguesa", "salchipapa"], "Recomendados"),
                ],
                [quick("Ver el menú"), quick("Finalizar pedido")],
            )

        if intent == "delivery":
            zones = "\n".join(
                f"• {zone.name}: {money(zone.fee)}" for zone in ZONES
            )
            return self._resp(
                session,
                [
                    text(
                        "Hacemos domicilios en **Montería** 🛵\n"
                        f"{zones}\n\n"
                        f"El envío es **gratis desde {money(self.settings.free_delivery_from)}** "
                        f"y el tiempo estimado es de "
                        f"**{self.settings.prep_minutes + self.settings.delivery_minutes} minutos**."
                    )
                ],
                [quick("Ver el menú"), quick("Finalizar pedido")],
            )

        if intent == "payments":
            return self._resp(
                session,
                [
                    text(
                        "Puedes pagar de dos formas:\n"
                        f"• **Nequi** al {self.settings.business_nequi}\n"
                        f"• **Llave** al {self.settings.business_llave}\n\n"
                        "Envía el comprobante por WhatsApp y confirmamos tu pedido."
                    )
                ],
                [quick("Ver el menú"), quick("Finalizar pedido")],
            )

        if intent == "hours":
            return self._resp(
                session,
                [
                    text(
                        f"Atendemos **{self.settings.business_hours}**.\n"
                        "Los pedidos web se reciben dentro de ese horario y se despachan de inmediato."
                    )
                ],
                [quick("Ver el menú"), quick("Finalizar pedido")],
            )

        if intent == "location":
            return self._resp(
                session,
                [
                    text(
                        f"Estamos en **{self.settings.business_address}**.\n"
                        f"Teléfono: **{self.settings.business_phone}**\n"
                        f"Mapa: {self.settings.maps_link}"
                    )
                ],
                [
                    quick("Llamar al despacho", self.settings.phone_link),
                    quick("Ver el menú"),
                ],
            )

        if intent == "human":
            return self._resp(
                session,
                [
                    text(
                        "Claro, te conecto con el equipo del despacho 👩‍🍳\n"
                        f"Llama al **{self.settings.business_phone}** o escríbenos por WhatsApp:\n"
                        f"{self.settings.whatsapp_link}"
                    )
                ],
                [
                    quick("Llamar al despacho", self.settings.phone_link),
                    quick("WhatsApp", self.settings.whatsapp_link),
                    quick("Ver el menú"),
                ],
            )

        if intent == "help":
            return self._resp(
                session,
                [
                    text(
                        "Soy **Sinu** y puedo:\n"
                        "• Mostrarte el menú y agregar productos (también con extras).\n"
                        "• Calcular el domicilio según tu barrio en Montería.\n"
                        "• Tomar tus datos y confirmar el pedido.\n"
                        "• Enviarte el estado del pedido.\n\n"
                        "Prueba con: *“Quiero dos suizos y una gaseosa”*."
                    )
                ],
                self._default_quick(session),
            )

        if intent == "greeting":
            return self._welcome(session)

        if intent == "thanks":
            return self._resp(
                session,
                [text("¡Con gusto! Estoy aquí para lo que necesites 😊")],
                self._default_quick(session),
            )

        if intent == "bye":
            return self._resp(
                session,
                [
                    text(
                        "¡Gracias por visitarnos! Cuando quieras volvemos a armar tu pedido. "
                        "Buen provecho 🍔"
                    )
                ],
                [quick("Ver el menú")],
            )

        return self._fallback(session)
