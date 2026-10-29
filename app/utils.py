"""Utilidades compartidas: dinero, fechas, normalización de texto y límites."""

from __future__ import annotations

import re
import secrets
import threading
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")


def now_bogota() -> datetime:
    """Hora actual en Colombia (independiente de la zona del servidor)."""
    return datetime.now(BOGOTA)


def iso_now() -> str:
    return now_bogota().isoformat(timespec="seconds")


def money(value: int | float) -> str:
    """Formatea pesos colombianos: 33600 -> $33.600."""
    return "$" + f"{int(round(value)):,}".replace(",", ".")


def normalize(text: str) -> str:
    """Minúsculas, sin tildes y sin signos: base para el motor de NLU."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_case(text: str) -> str:
    """Capitaliza respetando palabras pequeñas (de, la, del...)."""
    small = {"de", "del", "la", "las", "los", "el", "y", "en", "a"}
    words = []
    for index, word in enumerate(text.strip().split()):
        low = word.lower()
        if index > 0 and low in small:
            words.append(low)
        else:
            words.append(low[:1].upper() + low[1:])
    return " ".join(words)


def order_code() -> str:
    """Código legible y difícil de adivinar: SINU-261028-9F3A."""
    date = now_bogota().strftime("%y%m%d")
    suffix = secrets.token_hex(2).upper()
    return f"SINU-{date}-{suffix}"


def new_session_id() -> str:
    return "sess_" + secrets.token_urlsafe(18)


class RateLimiter:
    """Límite simple en memoria por IP (ventana deslizante de 60 s).

    Para despliegues con varias instancias se recomienda Redis; para una sola
    instancia gratuita (Render, Railway, Fly) este límite es suficiente.
    """

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = max(1, limit_per_minute)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


def clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
