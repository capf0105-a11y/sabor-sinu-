"""Disponibilidad de productos (agotados) persistida en disco.

El vendedor marca productos como agotados desde el panel `/admin` y el chatbot
lo refleja de inmediato. Solo se guardan los agotados, así que un producto
nuevo nace disponible.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("saborsinu.stock")


class StockStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._sold_out: set[str] = set()
        self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            sold = data.get("sold_out", [])
            if isinstance(sold, list):
                self._sold_out = {str(item) for item in sold}
        except (OSError, ValueError):
            logger.warning("No se pudo leer el stock en %s", self._path)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"sold_out": sorted(self._sold_out)}, ensure_ascii=False, indent=2
        )
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def is_available(self, product_id: str) -> bool:
        with self._lock:
            return product_id not in self._sold_out

    def set_available(self, product_id: str, available: bool) -> bool:
        with self._lock:
            if available:
                self._sold_out.discard(product_id)
            else:
                self._sold_out.add(product_id)
            self._save()
            return available

    def sold_out(self) -> set[str]:
        with self._lock:
            return set(self._sold_out)
