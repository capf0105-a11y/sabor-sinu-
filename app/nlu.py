"""Motor de comprensión de lenguaje natural (NLU) basado en reglas.

No depende de servicios externos: funciona sin API keys y es determinista, lo
que permite probar cada flujo. Entiende cantidades en números o palabras,
múltiples productos por mensaje, modificadores ("extra queso", "sin cebolla"),
intenciones de conversación y datos de entrega colombianos.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .menu import (
    MODIFIER_PATTERNS,
    MODIFIERS,
    NOTE_PATTERNS,
    PRODUCTS,
    Product,
)
from .utils import clamp_int, normalize, title_case

# ---------------------------------------------------------------------------
# Normalización y segmentación
# ---------------------------------------------------------------------------


def soft_normalize(text: str) -> str:
    """Normaliza pero conserva separadores (, ; +) para dividir pedidos."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^a-z0-9\s,;+]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_SEGMENT_SPLIT = re.compile(
    r"\s*(?:[,;+]|\s+y\s+|\s+e\s+|\s+tambien\s+|\s+ademas\s+)\s*"
)


def split_segments(text: str) -> list[str]:
    parts = [part.strip() for part in _SEGMENT_SPLIT.split(soft_normalize(text))]
    return [part for part in parts if part]


# ---------------------------------------------------------------------------
# Cantidades
# ---------------------------------------------------------------------------

NUMBER_WORDS: dict[str, int] = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
}

_QTY_RE = re.compile(
    r"\b(\d{1,2}|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\b"
)


def quantity_in(text: str) -> int | None:
    """Devuelve la última cantidad explícita del texto, o None."""
    if "media docena" in text:
        return 6
    if "docena" in text:
        return 12
    if re.search(r"\bun par\b", text):
        return 2
    matches = list(_QTY_RE.finditer(text))
    if not matches:
        return None
    token = matches[-1].group(1)
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def _quantity_near(segment: str, start: int, end: int) -> int:
    suffix = segment[end : end + 10]
    after = re.match(r"\s*x\s*(\d{1,2})\b", suffix)
    if after:
        return clamp_int(int(after.group(1)), 1, 30)
    qty = quantity_in(segment[max(0, start - 32) : start])
    return clamp_int(qty or 1, 1, 30)


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------

_ALIAS_INDEX: tuple[tuple[str, Product], ...] = tuple(
    (normalize(alias), product)
    for product in PRODUCTS
    for alias in product.aliases
)


@dataclass
class ProductMatch:
    start: int
    end: int
    product: Product


def find_products(segment: str) -> list[ProductMatch]:
    """Encuentra productos evitando solapamientos (gana el alias más largo)."""
    candidates: list[ProductMatch] = []
    for alias, product in _ALIAS_INDEX:
        if not alias:
            continue
        for match in re.finditer(rf"\b{re.escape(alias)}\b", segment):
            candidates.append(ProductMatch(match.start(), match.end(), product))

    candidates.sort(key=lambda item: (-(item.end - item.start), item.start))
    chosen: list[ProductMatch] = []
    for candidate in candidates:
        overlaps = any(
            candidate.start < other.end and candidate.end > other.start
            for other in chosen
        )
        if not overlaps:
            chosen.append(candidate)
    return sorted(chosen, key=lambda item: item.start)


def _distance(position: int, match: ProductMatch) -> int:
    if match.start <= position <= match.end:
        return 0
    return min(abs(position - match.start), abs(position - match.end))


@dataclass
class ParsedLine:
    product: Product
    qty: int
    modifiers: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ParsedOrder:
    lines: list[ParsedLine] = field(default_factory=list)
    orphan_modifiers: list[dict] = field(default_factory=list)
    orphan_notes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.lines and not self.orphan_modifiers and not self.orphan_notes


def _extract_modifier_positions(segment: str) -> list[tuple[int, dict]]:
    found: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for pattern, key in MODIFIER_PATTERNS:
        for match in re.finditer(pattern, segment):
            if key in seen:
                continue
            seen.add(key)
            found.append((match.start(), {"key": key, **MODIFIERS[key]}))
    return found


def _extract_notes(segment: str) -> list[str]:
    notes: list[str] = []
    for pattern, label in NOTE_PATTERNS:
        if re.search(pattern, segment) and label not in notes:
            notes.append(label)
    return notes


def parse_order(text: str) -> ParsedOrder:
    """Convierte texto libre en líneas de pedido con cantidades y extras."""
    result = ParsedOrder()
    for segment in split_segments(text):
        matches = find_products(segment)
        modifiers = _extract_modifier_positions(segment)
        notes = _extract_notes(segment)

        if not matches:
            for _, modifier in modifiers:
                if modifier not in result.orphan_modifiers:
                    result.orphan_modifiers.append(modifier)
            for note in notes:
                if note not in result.orphan_notes:
                    result.orphan_notes.append(note)
            continue

        for match in matches:
            line = ParsedLine(
                product=match.product,
                qty=_quantity_near(segment, match.start, match.end),
            )
            for position, modifier in modifiers:
                if _distance(position, match) <= 45:
                    line.modifiers.append(modifier)
            for pattern, label in NOTE_PATTERNS:
                note_match = re.search(pattern, segment)
                if note_match and _distance(note_match.start(), match) <= 45:
                    line.notes.append(label)
            line.notes = list(dict.fromkeys(line.notes))
            result.lines.append(line)
    return result


# ---------------------------------------------------------------------------
# Intenciones
# ---------------------------------------------------------------------------

INTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("cancel", r"\b(cancelar|cancela|cancelalo|olvidalo|dejalo asi|abortar|no sigamos)\b"),
    ("status", r"\b(estado|seguimiento|rastrear|tracking)\b|\b(donde|como) va (mi|el) pedido\b|\bmi pedido (esta|va|llega)\b"),
    ("human", r"\b(asesor|humano|persona|agente|hablar con alguien|atencion al cliente|queja|reclamo)\b"),
    ("clear_cart", r"\b(vaciar|vacia|limpia|limpiar|borra todo|borrar todo|quita todo|elimina todo|carrito vacio)\b"),
    ("remove_last", r"\b(quita|quitar|elimina|eliminar|borra|borrar|saca|sacar)\s+(el |la |lo )?(ultimo|ultima)\b"),
    ("payments", r"\b(nequi|bancolombia|efectivo|metodos? de pago|formas? de pago|como (puedo )?pagar|con que pago|pagar con|pago con|transferencia)\b"),
    ("delivery", r"\b(domicilio|domicilios|envio|envios|reparto|zonas?|cobertura|hacen entregas|reparten)\b"),
    ("hours", r"\b(horario|horarios|abren|cierran|a que hora|estan abiertos|estan cerrados)\b"),
    ("location", r"\b(donde estan|donde queda|ubicacion|direccion del local|local|sede)\b"),
    ("checkout_start", r"\b(finalizar|finaliza|terminar|termina|cerrar pedido|cerrar la orden|hacer (el |mi |la )?pedido|ordenar|pagar|pago|confirmar pedido|ya esta|eso es todo|nada mas|listo para (pagar|ordenar))\b"),
    ("menu", r"\b(menu|carta|que tienen|que venden|que hay|productos|precios|ver opciones|opciones)\b"),
    ("recommendation", r"\b(recomiendame|recomiendas|recomendacion|que me recomiendas|mas vendido|mas vendidos|favorito|favoritos|popular|que me aconsejas)\b"),
    ("help", r"\b(ayuda|ayudame|que puedes hacer|como funciona|instrucciones)\b"),
    ("greeting", r"\b(hola|buenas|buenos dias|buenas tardes|buenas noches|hey|saludos|que mas|quiubo|epale)\b"),
    ("thanks", r"\b(gracias|muchas gracias|te agradezco|mil gracias)\b"),
    ("bye", r"\b(chao|adios|hasta luego|nos vemos|bye)\b"),
)


def detect_intent(text: str) -> str | None:
    normalized = normalize(text)
    for intent, pattern in INTENT_PATTERNS:
        if re.search(pattern, normalized):
            return intent
    return None


_ADD_VERBS = re.compile(
    r"\b(quiero|queria|agrega|agregame|anade|suma|sumame|dame|me das|pon|ponme|trae|traeme|llevo|me llevo|ordeno|pido|pedir)\b"
)
_INFO_QUESTION = re.compile(
    r"\b(cuanto|cuanta|precio|precios|vale|valen|cuesta|cuestan|tienen|tienes|hay|venden|que lleva|que trae|ingredientes|descripcion|informacion|es bueno|recomiendas)\b"
)
_REMOVE_VERB = re.compile(
    r"\b(quita|quitar|quitale|elimina|eliminar|borra|borrar|saca|sacar|remueve|remover|no quiero|ya no quiero)\b"
)


def has_add_verb(text: str) -> bool:
    return bool(_ADD_VERBS.search(normalize(text)))


def is_info_question(text: str) -> bool:
    return bool(_INFO_QUESTION.search(normalize(text)))


def has_remove_verb(text: str) -> bool:
    return bool(_REMOVE_VERB.search(normalize(text)))


def is_yes(text: str) -> bool:
    normalized = normalize(text)
    if re.search(r"\b(no|mejor no|todavia no|aun no)\b", normalized):
        return False
    return bool(
        re.search(
            r"\b(si|confirmar|confirmo|confirmado|dale|listo|ok|okay|correcto|claro|de acuerdo|por supuesto)\b",
            normalized,
        )
    )


def is_no(text: str) -> bool:
    return bool(
        re.search(r"\b(no|cancelar|cancela|mejor no|todavia no|aun no|negativo)\b", normalize(text))
    )


# ---------------------------------------------------------------------------
# Datos del cliente
# ---------------------------------------------------------------------------

_NAME_PREFIX = re.compile(
    r"^(?:(?:hola|buenas|buenos dias|buenas tardes|buenas noches)[,\s]+)?"
    r"(?:mi nombre es|me llamo|soy|a nombre de)\s+",
    re.IGNORECASE,
)


def extract_name(raw: str) -> str | None:
    text = (raw or "").strip()
    text = _NAME_PREFIX.sub("", text)
    text = text.split(",")[0].strip(" .-")
    if not (3 <= len(text) <= 70):
        return None
    if re.search(r"\d|@|https?:", text):
        return None
    if len(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", text)) < 3:
        return None
    if normalize(text) in {"si", "no", "hola", "buenas", "menu", "nada", "gracias"}:
        return None
    return title_case(re.sub(r"\s+", " ", text))


_ADDRESS_PREFIX = re.compile(
    r"^(?:mi direccion es|la direccion es|vivo en|mi casa queda en|queda en|es en)\s+",
    re.IGNORECASE,
)
_ADDRESS_HINT = re.compile(
    r"(\d|calle|cl\.?|carrera|cra\.?|kr\.?|diagonal|dg\.?|transversal|tv\.?|"
    r"manzana|mz\.?|km\.?|vereda|urbanizacion|conjunto|edificio|torre|apto|"
    r"frente|barrio|parque|avenida|av\.?)",
    re.IGNORECASE,
)


def extract_address(raw: str) -> str | None:
    text = (raw or "").strip()
    text = _ADDRESS_PREFIX.sub("", text).strip(" .-")
    if not (8 <= len(text) <= 180):
        return None
    if len(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", text)) < 3:
        return None
    if not _ADDRESS_HINT.search(text):
        return None
    return re.sub(r"\s+", " ", text)


def extract_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("57") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("3"):
        return digits
    return None


def format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    return phone


def extract_payment(text: str) -> str | None:
    normalized = normalize(text)
    if re.search(r"\b(nequi)\b", normalized):
        return "nequi"
    if re.search(r"\b(llave|bre[\s-]?b|bre b)\b", normalized):
        return "llave"
    if re.search(
        r"\b(efectivo|bancolombia|transferencia|consignacion|tarjeta|datafono|"
        r"credito|debito|contra entrega|en la casa|billete)\b",
        normalized,
    ):
        return "unsupported"
    return None


def extract_order_code(text: str) -> str | None:
    match = re.search(r"\bsinu[\s-]*(\d{6})[\s-]*([a-z0-9]{4})\b", normalize(text))
    if not match:
        return None
    return f"SINU-{match.group(1)}-{match.group(2).upper()}"


def explicit_quantity(text: str) -> int | None:
    return quantity_in(soft_normalize(text))
