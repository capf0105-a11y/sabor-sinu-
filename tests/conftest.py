"""Configuración de pruebas: aísla datos y activa el correo en modo demo."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="saborsinu-tests-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("ORDERS_FILE", str(_TMP / "orders.json"))
os.environ.setdefault("OUTBOX_DIR", str(_TMP / "outbox"))
os.environ.setdefault("EMAIL_DRY_RUN", "true")
os.environ.setdefault("ADMIN_TOKEN", "token-de-prueba")
os.environ.setdefault("ENVIRONMENT", "test")
