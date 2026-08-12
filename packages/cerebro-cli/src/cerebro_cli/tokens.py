"""Generacion del secreto TRANSVERSAL (ecosistema-cerebro.md SS13) y estado local de
reintentos pendientes.

Un solo secreto (prefijo `cbr_`, distinto de `kos_`/`cbrd_` -- los prefijos propios de
cada API cuando generan su token sin `value`) se registra por separado en
cerebro-memory y cerebro-docs via `POST /tokens` con `value=<secreto>` (ver SS13 y los
cambios en `cerebro_memory.auth`/`cerebro_docs.auth`).

Estado pendiente: para que "reintentar el mismo comando" sea realmente seguro tras un
fallo parcial, el reintento debe usar el MISMO secreto que el intento anterior -- si
generara uno nuevo cada vez, el servicio que YA quedo registrado veria un nombre
duplicado con un hash distinto (409, no idempotente). Por eso `cerebro token create`
persiste el secreto generado en un archivo local (fuera del repo, en el home del
usuario) hasta que ambos servicios confirman exito, momento en el que se borra.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

TOKEN_PREFIX = "cbr_"

STATE_DIR = Path.home() / ".cerebro"
PENDING_TOKENS_DIR = STATE_DIR / "pending-tokens"


def generate_transversal_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _pending_path(name: str) -> Path:
    # Los nombres de token son identificadores de agente (ej. "claude-desktop"), no
    # rutas - basta con un guard minimo contra separadores de path antes de usarlos
    # como nombre de archivo.
    safe = name.replace("/", "_").replace("\\", "_")
    return PENDING_TOKENS_DIR / f"{safe}.json"


def load_pending_value(name: str) -> str | None:
    path = _pending_path(name)
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("value")


def save_pending_value(name: str, value: str) -> None:
    path = _pending_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "value": value}), encoding="utf-8")


def clear_pending_value(name: str) -> None:
    _pending_path(name).unlink(missing_ok=True)
