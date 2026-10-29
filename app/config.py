"""Configuración central de Sabor Sinú.

Toda la configuración sensible se toma de variables de entorno (archivo .env).
Los valores por defecto permiten ejecutar la aplicación en local sin configurar
nada: el correo se escribe en data/outbox en lugar de enviarse por SMTP.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "")
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "si", "sí", "on"}


def _env_list(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


_smtp_host = _env("SMTP_HOST")


@dataclass(frozen=True)
class Settings:
    """Valores inmutables de configuración del servicio."""

    # Rutas
    base_dir: Path = BASE_DIR
    static_dir: Path = BASE_DIR / "static"
    data_dir: Path = Path(_env("DATA_DIR", str(BASE_DIR / "data")))
    orders_file: Path = Path(_env("ORDERS_FILE", str(BASE_DIR / "data" / "orders.json")))
    outbox_dir: Path = Path(_env("OUTBOX_DIR", str(BASE_DIR / "data" / "outbox")))

    # Marca
    business_name: str = _env("BUSINESS_NAME", "Sabor Sinú")
    business_tagline: str = _env("BUSINESS_TAGLINE", "Cocina rápida costeña")
    business_phone: str = _env("BUSINESS_PHONE", "+57 304 466 7353")
    business_whatsapp: str = _env("BUSINESS_WHATSAPP", "573044667353")
    business_nequi: str = _env("BUSINESS_NEQUI", "304 466 7353")
    business_llave: str = _env("BUSINESS_LLAVE", "304 466 7353")
    business_address: str = _env(
        "BUSINESS_ADDRESS", "Calle 34 # 6-12, Centro, Montería, Córdoba"
    )
    business_hours: str = _env(
        "BUSINESS_HOURS", "Lunes a domingo · 11:00 a.m. – 10:30 p.m."
    )
    business_instagram: str = _env("BUSINESS_INSTAGRAM", "@saborsinu")

    # Domicilios y tiempos
    delivery_base_fee: int = _env_int("DELIVERY_BASE_FEE", 2000)
    free_delivery_from: int = _env_int("FREE_DELIVERY_FROM", 80000)
    prep_minutes: int = _env_int("PREP_MINUTES", 25)
    delivery_minutes: int = _env_int("DELIVERY_MINUTES", 20)
    timezone: str = _env("TIMEZONE", "America/Bogota")

    # Correo
    smtp_host: str = _smtp_host
    smtp_port: int = _env_int("SMTP_PORT", 465)
    smtp_user: str = _env("SMTP_USER")
    smtp_password: str = _env("SMTP_PASSWORD")
    smtp_from: str = _env("SMTP_FROM")
    smtp_from_name: str = _env("SMTP_FROM_NAME", "Sabor Sinú · Pedidos Web")
    smtp_use_ssl: bool = _env_bool("SMTP_USE_SSL", True)
    smtp_use_tls: bool = _env_bool("SMTP_USE_TLS", False)
    smtp_timeout: int = _env_int("SMTP_TIMEOUT", 15)
    admin_emails: tuple[str, ...] = _env_list("ADMIN_EMAILS")
    email_dry_run: bool = _env_bool("EMAIL_DRY_RUN", not bool(_smtp_host))

    # WhatsApp del vendedor (CallMeBot)
    whatsapp_alert_phone: str = _env("WHATSAPP_ALERT_PHONE")
    callmebot_api_key: str = _env("CALLMEBOT_API_KEY")

    # Persistencia opcional del stock (Upstash Redis free)
    upstash_redis_rest_url: str = _env("UPSTASH_REDIS_REST_URL")
    upstash_redis_rest_token: str = _env("UPSTASH_REDIS_REST_TOKEN")

    # Operación
    admin_token: str = _env("ADMIN_TOKEN")
    # Ruta privada del panel del vendedor. Si se cambia, /admin deja de existir.
    admin_panel_path: str = _env("ADMIN_PANEL_PATH", "/admin")
    allowed_origins: tuple[str, ...] = _env_list("ALLOWED_ORIGINS")
    session_ttl_minutes: int = _env_int("SESSION_TTL_MINUTES", 180)
    rate_limit_per_minute: int = _env_int("RATE_LIMIT_PER_MINUTE", 60)
    max_message_chars: int = _env_int("MAX_MESSAGE_CHARS", 500)
    environment: str = _env("ENVIRONMENT", "development")

    @property
    def phone_link(self) -> str:
        digits = "".join(ch for ch in self.business_phone if ch.isdigit())
        return f"tel:+{digits}"

    @property
    def whatsapp_link(self) -> str:
        digits = "".join(ch for ch in self.business_whatsapp if ch.isdigit())
        return f"https://wa.me/{digits}"

    @property
    def maps_link(self) -> str:
        query = self.business_address.replace(" ", "+").replace("#", "%23")
        return f"https://www.google.com/maps/search/?api=1&query={query}"

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.admin_emails and not self.email_dry_run)

    @property
    def whatsapp_alerts_enabled(self) -> bool:
        return bool(self.whatsapp_alert_phone and self.callmebot_api_key)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
