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

Permisos (ecosistema-cerebro.md SS15, criterio de auditoria): el secreto vive en
claro en ese archivo mientras el registro esta pendiente, asi que tanto el directorio
como el archivo se crean con permisos restrictivos (0o700/0o600) en POSIX -- en
Windows `os.chmod` no modela el mismo bitmask, asi que se intenta best-effort y se
ignora el error (no-op aceptable, ver auditoria).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

TOKEN_PREFIX = "cbr_"

STATE_DIR = Path.home() / ".cerebro"
PENDING_TOKENS_DIR = STATE_DIR / "pending-tokens"

# Umbral para avisar de un archivo pendiente "huerfano" (un `cerebro token create`
# que fallo parcialmente y nunca se reintento) -- sin este aviso, el secreto en claro
# quedaria en disco indefinidamente sin que nadie lo note (ecosistema-cerebro.md SS15).
STALE_PENDING_SECONDS = 24 * 60 * 60


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass  # Windows (u otro FS sin el mismo modelo de permisos): no-op aceptable.


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
    _chmod_best_effort(path.parent, 0o700)
    path.write_text(json.dumps({"name": name, "value": value}), encoding="utf-8")
    _chmod_best_effort(path, 0o600)


def clear_pending_value(name: str) -> None:
    _pending_path(name).unlink(missing_ok=True)


def warn_stale_pending_tokens(*, now: float | None = None) -> None:
    """Avisa por stderr de archivos pendientes mas viejos que `STALE_PENDING_SECONDS`
    -- llamado al arrancar el CLI (`main()`) para que un registro parcial olvidado no
    quede en disco para siempre sin que el usuario lo note. Nunca imprime el secreto
    en claro, solo el nombre y la edad."""
    if not PENDING_TOKENS_DIR.is_dir():
        return
    current = now if now is not None else time.time()
    for path in sorted(PENDING_TOKENS_DIR.glob("*.json")):
        try:
            age_seconds = current - path.stat().st_mtime
        except OSError:
            continue
        if age_seconds < STALE_PENDING_SECONDS:
            continue
        age_days = age_seconds / 86400
        name = path.stem
        print(
            f"Aviso: hay un registro de token pendiente sin completar para '{name}' "
            f"desde hace ~{age_days:.1f} dia(s) ({path}). Ejecuta "
            f"`cerebro token create {name} --scopes ...` de nuevo para reintentarlo "
            "(reusa el mismo secreto), o borra el archivo si ya no aplica.",
            file=sys.stderr,
        )
