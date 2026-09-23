"""Generation of the CROSS-CUTTING secret (ecosistema-cerebro.md SS13) and local
state for pending retries.

A single secret (prefix `cbr_`, distinct from `kos_`/`cbrd_` -- the prefixes each
API uses on its own when it generates its token without `value`) is registered
separately in cerebro-memory and cerebro-docs via `POST /tokens` with `value=<secret>`
(see SS13 and the changes in `cerebro_memory.auth`/`cerebro_docs.auth`).

Pending state: for "retry the same command" to actually be safe after a
partial failure, the retry must use the SAME secret as the previous attempt -- if
it generated a new one each time, the service that WAS ALREADY registered would see a
duplicate name with a different hash (409, not idempotent). That's why `cerebro token create`
persists the generated secret to a local file (outside the repo, in the user's
home) until both services confirm success, at which point it's deleted.

Permissions (ecosistema-cerebro.md SS15, audit criterion): the secret lives in
plaintext in that file while the registration is pending, so both the directory
and the file are created with restrictive permissions (0o700/0o600) on POSIX -- on
Windows `os.chmod` doesn't model the same bitmask, so it's attempted best-effort and
the error is ignored (acceptable no-op, see audit).
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

# Threshold for warning about an "orphaned" pending file (a `cerebro token create`
# that failed partially and was never retried) -- without this warning, the plaintext secret
# would sit on disk indefinitely without anyone noticing (ecosistema-cerebro.md SS15).
STALE_PENDING_SECONDS = 24 * 60 * 60


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass  # Windows (or another FS without the same permissions model): acceptable no-op.


def generate_transversal_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _pending_path(name: str) -> Path:
    # Token names are agent identifiers (e.g. "claude-desktop"), not
    # paths - a minimal guard against path separators is enough before using them
    # as a file name.
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
    """Warns via stderr about pending files older than `STALE_PENDING_SECONDS`
    -- called when the CLI starts up (`main()`) so a forgotten partial registration doesn't
    sit on disk forever without the user noticing. Never prints the plaintext
    secret, only the name and the age."""
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
