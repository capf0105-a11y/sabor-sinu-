"""Carta real de Sabor Sinú y reglas de domicilio en Montería.

Editar este archivo es suficiente para cambiar precios, descripciones, alias de
búsqueda o tarifas por barrio. Los alias se comparan ya normalizados (sin tildes
ni mayúsculas), por eso se escriben en minúsculas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .utils import normalize, title_case


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    price: int
    description: str
    ingredients: tuple[str, ...]
    category: str
    image: str
    badge: str
    aliases: tuple[str, ...]
    popular: bool = False
    prep_minutes: int = 15

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "price": self.price,
            "description": self.description,
            "ingredients": list(self.ingredients),
            "category": self.category,
            "image": self.image,
            "badge": self.badge,
            "popular": self.popular,
            "prep_minutes": self.prep_minutes,
        }


PRODUCTS: tuple[Product, ...] = (
    Product(
        id="suizo",
        name="Suizo con Lomito Monteriano",
        price=33600,
        description=(
            "Pan artesanal, salchicha suiza premium picada (150 g), lomito de res "
            "saltado (160 g), papa ripio, salsa tártara de la casa y abundante "
            "queso costeño gratinado."
        ),
        ingredients=(
            "Pan artesanal",
            "Salchicha suiza 150 g",
            "Lomito de res 160 g",
            "Papa ripio",
            "Salsa tártara de la casa",
            "Queso costeño gratinado",
        ),
        category="Principales",
        image="/static/img/suizo.svg",
        badge="Más vendido",
        aliases=(
            "suizo con lomito monteriano",
            "suizo con lomito",
            "suizo monteriano",
            "suizo",
            "suizos",
            "lomito monteriano",
            "lomito",
        ),
        popular=True,
        prep_minutes=18,
    ),
    Product(
        id="superperro",
        name="Súper Perro de la Casa",
        price=18000,
        description=(
            "Pan alargado premium, lechuga, salchicha ranchera, jamón, pollo "
            "desmechado, maíz tierno, chorizo, tajaditas de plátano maduro, papa "
            "ripio, queso mozzarella y salsa tártara especial."
        ),
        ingredients=(
            "Pan alargado premium",
            "Salchicha ranchera",
            "Jamón y pollo desmechado",
            "Maíz tierno y chorizo",
            "Plátano maduro y papa ripio",
            "Queso mozzarella y salsa tártara",
        ),
        category="Principales",
        image="/static/img/superperro.svg",
        badge="De la casa",
        aliases=(
            "super perro de la casa",
            "super perro",
            "superperro",
            "perro de la casa",
            "super dog",
            "perro",
            "perros",
        ),
        popular=True,
        prep_minutes=12,
    ),
    Product(
        id="salchipapa",
        name="Salchipapa Salvajada Costeña",
        price=28000,
        description=(
            "Cama de papas fritas crujientes, salchicha premium, carne desmechada, "
            "pollo, butifarra, chorizo, queso costeño rallado, suero costeño y "
            "salsas de la casa."
        ),
        ingredients=(
            "Papas fritas crujientes",
            "Salchicha premium",
            "Carne desmechada y pollo",
            "Butifarra y chorizo",
            "Queso costeño rallado",
            "Suero costeño y salsas de la casa",
        ),
        category="Principales",
        image="/static/img/salchipapa.svg",
        badge="Para compartir",
        aliases=(
            "salchipapa salvajada costena",
            "salchipapa salvajada",
            "salvajada costena",
            "salchipapa costena",
            "salchipapa",
            "salchipapas",
            "salchi",
        ),
        prep_minutes=15,
    ),
    Product(
        id="hamburguesa",
        name="Hamburguesa Sinuana Especial",
        price=18000,
        description=(
            "Carne artesanal al carbón (150 g), queso americano, tocineta crujiente, "
            "lechuga, tomate y papas francesas incluidas."
        ),
        ingredients=(
            "Carne artesanal al carbón 150 g",
            "Queso americano",
            "Tocineta crujiente",
            "Lechuga y tomate",
            "Papas francesas incluidas",
        ),
        category="Principales",
        image="/static/img/hamburguesa.svg",
        badge="Favorita",
        aliases=(
            "hamburguesa sinuana especial",
            "hamburguesa sinuana",
            "hamburguesa",
            "hamburguesas",
            "hamburgesa",
            "burger",
            "sinuana",
        ),
        popular=True,
        prep_minutes=14,
    ),
    Product(
        id="gaseosa_postobon",
        name="Gaseosa Postobón 400 ml",
        price=4500,
        description="Bien fría, 400 ml. Elige Postobón para acompañar tu pedido.",
        ingredients=("Botella 400 ml", "Servida bien fría"),
        category="Bebidas",
        image="/static/img/gaseosa.svg",
        badge="Bien fría",
        aliases=(
            "gaseosa postobon",
            "postobon",
            "gaseosa 400",
            "gaseosa",
            "gaseosas",
            "refresco",
            "bebida",
            "bebidas",
        ),
        prep_minutes=2,
    ),
    Product(
        id="gaseosa_coca",
        name="Gaseosa Coca-Cola 400 ml",
        price=4500,
        description="Coca-Cola original bien fría, 400 ml.",
        ingredients=("Botella 400 ml", "Servida bien fría"),
        category="Bebidas",
        image="/static/img/gaseosa.svg",
        badge="Bien fría",
        aliases=(
            "gaseosa coca cola",
            "coca cola",
            "cocacola",
            "gaseosa coca",
            "coca",
        ),
        prep_minutes=2,
    ),
    Product(
        id="jugo_agua",
        name="Jugo Natural en Agua",
        price=6000,
        description="Jugo natural del día preparado en agua. Pregunta por la fruta disponible.",
        ingredients=("Fruta natural del día", "Preparado en agua", "Sin conservantes"),
        category="Bebidas",
        image="/static/img/jugo.svg",
        badge="Natural",
        aliases=(
            "jugo natural en agua",
            "jugos naturales en agua",
            "jugo en agua",
            "jugo de agua",
            "jugo natural",
            "jugos naturales",
            "jugo",
            "jugos",
        ),
        prep_minutes=4,
    ),
    Product(
        id="jugo_leche",
        name="Jugo Natural en Leche",
        price=6000,
        description="Jugo natural del día preparado en leche. Pregunta por la fruta disponible.",
        ingredients=("Fruta natural del día", "Preparado en leche", "Sin conservantes"),
        category="Bebidas",
        image="/static/img/jugo.svg",
        badge="Natural",
        aliases=(
            "jugo natural en leche",
            "jugos naturales en leche",
            "jugo en leche",
            "jugos en leche",
            "jugo con leche",
            "jugos con leche",
            "jugo de leche",
            "jugos de leche",
            "jugo leche",
        ),
        prep_minutes=4,
    ),
)

PRODUCTS_BY_ID: dict[str, Product] = {product.id: product for product in PRODUCTS}
ALL_PRODUCT_IDS: tuple[str, ...] = tuple(product.id for product in PRODUCTS)


# ---------------------------------------------------------------------------
# Modificadores (con costo) y notas (sin costo)
# ---------------------------------------------------------------------------

MODIFIERS: dict[str, dict] = {
    "extra_queso": {"name": "Extra queso costeño", "price": 2500},
    "extra_carne": {"name": "Extra carne", "price": 6000},
    "extra_salchicha": {"name": "Extra salchicha", "price": 4000},
    "extra_papas": {"name": "Extra papas", "price": 3000},
    "extra_salsa": {"name": "Extra salsa de la casa", "price": 1000},
    "doble_proteina": {"name": "Doble proteína", "price": 8000},
}

MODIFIER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:doble\s+proteina|doble\s+porcion\s+de\s+carne)\b", "doble_proteina"),
    (r"\b(?:extra|mas|adicional|doble)\s+queso\b", "extra_queso"),
    (r"\b(?:extra|mas|adicional|doble)\s+carne\b", "extra_carne"),
    (r"\b(?:extra|mas|adicional|doble)\s+salchicha\b", "extra_salchicha"),
    (r"\b(?:extra|mas|adicional|doble)\s+papas?\b", "extra_papas"),
    (r"\b(?:extra|mas|adicional|doble)\s+salsas?\b", "extra_salsa"),
)

NOTE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bsin\s+queso\b", "Sin queso"),
    (r"\bsin\s+cebolla\b", "Sin cebolla"),
    (r"\bsin\s+tomate\b", "Sin tomate"),
    (r"\bsin\s+lechuga\b", "Sin lechuga"),
    (r"\bsin\s+salsa\s+tartara\b", "Sin salsa tártara"),
    (r"\bsin\s+salsa\b", "Sin salsa"),
    (r"\bsin\s+tocineta\b", "Sin tocineta"),
    (r"\bsin\s+maiz\b", "Sin maíz"),
    (r"\bsin\s+platano\b", "Sin plátano"),
    (r"\bsin\s+chorizo\b", "Sin chorizo"),
    (r"\bsin\s+picante\b", "Sin picante"),
    (r"\bsin\s+hielo\b", "Sin hielo"),
    (r"\bbien\s+asado\b", "Bien asado"),
    (r"\bbien\s+cocido\b", "Bien cocido"),
    (r"\btermino\s+medio\b", "Término medio"),
    (r"\bpara\s+llevar\b", "Para llevar"),
)


# ---------------------------------------------------------------------------
# Zonas de domicilio en Montería
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    fee: int
    barrios: tuple[str, ...] = field(default_factory=tuple)

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "fee": self.fee,
            "barrios": list(self.barrios),
        }


ZONES: tuple[Zone, ...] = (
    Zone(
        id="zona_1",
        name="Zona 1 · Centro y alrededores",
        fee=4000,
        barrios=(
            "Centro",
            "El Prado",
            "Sucre",
            "La Ribera",
            "San Martín",
            "El Dorado",
            "La Pradera",
        ),
    ),
    Zone(
        id="zona_2",
        name="Zona 2 · Norte y oriente",
        fee=6000,
        barrios=(
            "La Granja",
            "Monteverde",
            "El Recreo",
            "Los Laureles",
            "La Castellana",
            "Santa Fe",
            "Nueva Lucía",
            "Cantaclaro",
            "Camilo Torres",
            "Pastrana",
            "La Esperanza",
            "Los Ángeles",
            "Villa Sinú",
            "Brisas del Sinú",
            "Pumarrosa",
            "San José",
            "Los Cedros",
        ),
    ),
    Zone(
        id="zona_3",
        name="Zona 3 · Periferia y vía nacional",
        fee=8000,
        barrios=(
            "Los Garzones",
            "La Guajira",
            "El Silencio",
            "Las Colinas",
            "Santa Clara",
            "El Totumo",
            "Aeropuerto",
        ),
    ),
)

# Lista plana (nombre visible, nombre normalizado, zona)
BARRIO_INDEX: tuple[tuple[str, str, Zone], ...] = tuple(
    (barrio, normalize(barrio), zone) for zone in ZONES for barrio in zone.barrios
)

COMMON_BARRIOS: tuple[str, ...] = ("Centro", "La Granja", "Monteverde", "Los Garzones")


def find_zone(text: str) -> tuple[str, Zone] | None:
    """Busca un barrio dentro de una dirección o de una respuesta suelta.

    Primero hace coincidencia exacta por frase; si no, intenta tolerar errores de
    digitación comparando cada fragmento de 1-2 palabras con los barrios.
    """
    normalized = normalize(text)
    if not normalized:
        return None

    for barrio, key, zone in sorted(BARRIO_INDEX, key=lambda item: -len(item[1])):
        if re.search(rf"\b{re.escape(key)}\b", normalized):
            return barrio, zone

    tokens = normalized.split()
    candidates = [" ".join(tokens[i : i + 2]) for i in range(max(1, len(tokens) - 1))]
    candidates += tokens
    best: tuple[float, str, Zone] | None = None
    for candidate in candidates:
        if len(candidate) < 4:
            continue
        for barrio, key, zone in BARRIO_INDEX:
            if len(key) < 4:
                continue
            ratio = SequenceMatcher(None, candidate, key).ratio()
            if ratio >= 0.84 and (best is None or ratio > best[0]):
                best = (ratio, barrio, zone)
    if best:
        return best[1], best[2]
    return None


def zone_fee(barrio: str | None) -> int:
    if not barrio:
        return 0
    found = find_zone(barrio)
    return found[1].fee if found else 0


def display_barrio(barrio: str) -> str:
    return title_case(barrio.strip())
