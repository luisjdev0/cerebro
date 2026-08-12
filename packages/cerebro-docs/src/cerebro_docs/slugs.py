"""Generacion y normalizacion de slugs para documentos (ecosistema-cerebro.md SS6).

Puro (sin I/O) a proposito, para que sea testeable sin base de datos - la deteccion
de COLISION real vive en la restriccion `UNIQUE (category_id, slug)` de la base de
datos (ver api.py, que traduce el `UniqueViolationError` de Postgres a un 409
explicito): dos documentos con el mismo titulo en la misma categoria generan aqui,
deliberadamente, el mismo slug - es la base de datos, no este modulo, quien decide
si eso es un conflicto real.
"""

from __future__ import annotations

import re
import unicodedata

FALLBACK_SLUG = "documento"

_WHITESPACE_RE = re.compile(r"\s+")
_INVALID_CHARS_RE = re.compile(r"[^a-z0-9-]+")
_REPEATED_DASH_RE = re.compile(r"-{2,}")


def slugify(title: str) -> str:
    """lowercase, sin acentos, espacios -> guiones, solo [a-z0-9-].

    Guiones repetidos se colapsan y los de inicio/fin se recortan. Un titulo que no
    deja ningun caracter valido (p.ej. solo emoji o puntuacion) cae a
    `FALLBACK_SLUG` en vez de producir un slug vacio.
    """
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.strip().lower()
    dashed = _WHITESPACE_RE.sub("-", lowered)
    cleaned = _INVALID_CHARS_RE.sub("-", dashed)
    collapsed = _REPEATED_DASH_RE.sub("-", cleaned).strip("-")
    return collapsed or FALLBACK_SLUG
