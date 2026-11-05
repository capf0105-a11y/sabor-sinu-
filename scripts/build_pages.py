"""Genera la tienda pública para GitHub Pages.

La API y el panel privado siguen viviendo en Render. Este script copia solo
los archivos públicos a ``docs/``, cambia las rutas absolutas por relativas y
agrega el origen de la API para que el navegador sepa a dónde pedir el menú.

Uso:
    python scripts/build_pages.py

Después de cambiar productos, fotos o estilos, vuelve a ejecutarlo y sube
``docs/`` a la rama principal.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
DOCS = ROOT / "docs"
API_BASE = "https://sabor-sinu.onrender.com"

# Archivos públicos que no deben viajar a GitHub Pages.
EXCLUDED = {"admin.html", "admin.css", "admin.js"}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _copy_public_tree() -> None:
    # En carpetas sincronizadas (OneDrive) rmtree puede fallar por bloqueos.
    # Los archivos se sobrescriben igual, así que un borrado parcial no estorba.
    if DOCS.exists():
        shutil.rmtree(DOCS, ignore_errors=True)

    (DOCS / "static" / "css").mkdir(parents=True, exist_ok=True)
    (DOCS / "static" / "js").mkdir(parents=True, exist_ok=True)
    (DOCS / "static" / "img").mkdir(parents=True, exist_ok=True)

    shutil.copy2(
        STATIC / "css" / "styles.css", DOCS / "static" / "css" / "styles.css"
    )
    for name in ("app.js", "boot.js"):
        shutil.copy2(STATIC / "js" / name, DOCS / "static" / "js" / name)

    for image in (STATIC / "img").iterdir():
        if image.is_file() and image.name not in EXCLUDED:
            shutil.copy2(image, DOCS / "static" / "img" / image.name)


def _build_index() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    index = index.replace(
        '<meta name="theme-color" content="#f7f1e7" id="metaTheme">',
        (
            '<meta name="theme-color" content="#f7f1e7" id="metaTheme">\n'
            f'  <meta name="sinu-api-base" content="{API_BASE}">'
        ),
    )
    index = index.replace('href="/static/', 'href="static/')
    index = index.replace('src="/static/', 'src="static/')
    index = index.replace('href="/"', 'href="./"')
    index = index.replace('href="/credits"', 'href="credits.html"')
    _write(DOCS / "index.html", index)


def _build_credits() -> None:
    credits = (STATIC / "credits.html").read_text(encoding="utf-8")
    credits = credits.replace('href="/"', 'href="index.html"')
    _write(DOCS / "credits.html", credits)


def main() -> None:
    _copy_public_tree()
    _build_index()
    _build_credits()
    # Evita que Jekyll procese los archivos y garantiza que se sirvan tal cual.
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print(f"GitHub Pages listo en {DOCS}")


if __name__ == "__main__":
    main()
