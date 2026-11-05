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


def _clock_minutes(value: str) -> int:
    """Convierte "HH:MM" a minutos desde medianoche (0 si viene mal)."""
    try:
        hours, minutes = str(value).strip().split(":")[:2]
        return int(hours) * 60 + int(minutes)
    except (AttributeError, ValueError):
        return 0


def schedule_status(
    open_time: str, close_time: str, now: datetime | None = None
) -> dict:
    """Estado de apertura según la hora de Colombia, no la del servidor.

    Soporta horarios que cruzan la medianoche (p. ej. 18:00 → 02:00).
    """
    current = now or now_bogota()
    if current.tzinfo is None:
        current = current.replace(tzinfo=BOGOTA)
    else:
        current = current.astimezone(BOGOTA)

    minutes = current.hour * 60 + current.minute
    start = _clock_minutes(open_time)
    end = _clock_minutes(close_time)

    if start == end:
        is_open = True
    elif start < end:
        is_open = start <= minutes < end
    else:
        is_open = minutes >= start or minutes < end

    return {
        "is_open": is_open,
        "status_label": "Abierto ahora" if is_open else "Cerrado",
    }


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


class LoginGuard:
    """Bloquea intentos de contraseña fallidos por IP.

    No cuenta los accesos correctos: el vendedor puede entrar varias veces sin
    quedar bloqueado. Tras ``max_attempts`` fallos en ``window_seconds``, la IP
    queda bloqueada durante ``block_seconds``.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 900,
        block_seconds: int = 900,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.window_seconds = max(60, window_seconds)
        self.block_seconds = max(60, block_seconds)
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_blocked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            until = self._blocked_until.get(key)
            if not until:
                return False
            if until > now:
                return True
            self._blocked_until.pop(key, None)
            return False

    def register_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            bucket = self._failures[key]
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            bucket.append(now)
            if len(bucket) >= self.max_attempts:
                self._blocked_until[key] = now + self.block_seconds
                bucket.clear()

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)


def clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
