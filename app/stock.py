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
import urllib.request
from pathlib import Path

logger = logging.getLogger("saborsinu.stock")


class StockStore:
    def __init__(
        self,
        path: Path,
        remote_url: str = "",
        remote_token: str = "",
    ) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._sold_out: set[str] = set()
        self._remote_url = (remote_url or "").rstrip("/")
        self._remote_token = remote_token or ""
        self._remote_key = "saborsinu:stock"
        self._remote_ok = False
        self._load()

    @property
    def remote_enabled(self) -> bool:
        return bool(self._remote_url and self._remote_token)

    @property
    def persistent(self) -> bool:
        """True solo si Upstash está configurado y respondió bien."""
        return self.remote_enabled and self._remote_ok

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self.remote_enabled:
            try:
                raw = self._remote_command("GET", self._remote_key)
                self._remote_ok = True
                if raw:
                    data = json.loads(raw)
                    sold = data.get("sold_out", [])
                    if isinstance(sold, list):
                        self._sold_out = {str(item) for item in sold}
                    self._save_local()
                    return
                # Primer uso: sube lo que haya en el archivo local.
                self._remote_command(
                    "SET",
                    self._remote_key,
                    json.dumps({"sold_out": sorted(self._sold_out)}),
                )
                return
            except Exception as exc:  # pragma: no cover - depende de red
                logger.warning("No se pudo leer Upstash (%s); uso archivo local", exc)
        self._load_local()

    def _load_local(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            sold = data.get("sold_out", [])
            if isinstance(sold, list):
                self._sold_out = {str(item) for item in sold}
        except (OSError, ValueError):
            logger.warning("No se pudo leer el stock en %s", self._path)

    def _save_local(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"sold_out": sorted(self._sold_out)}, ensure_ascii=False, indent=2
        )
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)

    def _remote_command(self, *args: str) -> object:
        payload = json.dumps(list(args)).encode("utf-8")
        request = urllib.request.Request(
            self._remote_url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._remote_token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data.get("result")

    def _save_remote(self) -> None:
        if not self.remote_enabled:
            return
        try:
            self._remote_command(
                "SET",
                self._remote_key,
                json.dumps({"sold_out": sorted(self._sold_out)}),
            )
            self._remote_ok = True
        except Exception as exc:  # pragma: no cover - depende de red
            self._remote_ok = False
            logger.warning("No se pudo guardar el stock en Upstash: %s", exc)

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
            self._save_local()
            self._save_remote()
            return available

    def sold_out(self) -> set[str]:
        with self._lock:
            return set(self._sold_out)

