"""Local CLI state under `~/.cerebro/` (not to be confused with server-side token
storage, which now lives entirely in cerebro-auth).

Historically this module also generated and persisted a CROSS-CUTTING secret
(prefix `cbr_`) plus pending-retry state for `cerebro token create`, because that
command had to register the same secret in two independent services
(cerebro-memory and cerebro-docs) and survive a partial failure between them. Now
that token management lives in exactly ONE service (cerebro-auth), there's nothing
left to retry across services, so that whole mechanism (`generate_transversal_token`,
`save_pending_value`/`load_pending_value`/`clear_pending_value`,
`warn_stale_pending_tokens`, `PENDING_TOKENS_DIR`) has been removed -- a single
`AuthClient().create_token(...)` call either succeeds or fails, no local state
needed in between.

What's left: `cerebro login` persists the resolved token/gateway URL here after a
successful `AuthClient.login()` call, so other `cerebro-cli` invocations can pick up
credentials without `CEREBRO_TOKEN`/`CEREBRO_*_URL` set in the environment.

`cerebro_clients.config`'s `*_token()`/`*_base_url()` functions read this file back
as a fallback, below any env var -- see that module's docstring for the exact
precedence order.

Permissions (ecosistema-cerebro.md SS15, audit criterion): the file holds a
plaintext token, so both the directory and file are created with restrictive
permissions (0o700/0o600) on POSIX -- on Windows `os.chmod` doesn't model the same
bitmask, so it's attempted best-effort and the error is ignored (acceptable no-op,
see audit).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIR = Path.home() / ".cerebro"
CONFIG_PATH = STATE_DIR / "config.json"


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass  # Windows (or another FS without the same permissions model): acceptable no-op.


def save_login_config(
    token: str,
    url: str | None,
    *,
    kind: str = "token",
    user: str | None = None,
    expires_at: str | None = None,
) -> None:
    """Persists the credentials resolved by `cerebro login` to
    `~/.cerebro/config.json`. Overwrites any previous login.

    `kind` is "token" (a token the user pasted: never expires from the CLI's point
    of view, and `logout` only forgets it) or "session" (minted by a user/password
    login: carries `expires_at` and is revoked server-side on `logout`)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(STATE_DIR, 0o700)
    data: dict[str, Any] = {"token": token, "kind": kind}
    if url:
        data["url"] = url
    if user:
        data["user"] = user
    if expires_at:
        data["expires_at"] = expires_at
    CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")
    _chmod_best_effort(CONFIG_PATH, 0o600)


def clear_login_config() -> bool:
    """Forget the saved credentials. Returns True if there was something to remove."""
    if not CONFIG_PATH.exists():
        return False
    try:
        CONFIG_PATH.unlink()
    except OSError:
        return False
    return True


def session_state() -> str:
    """"none" (no credentials at all), "expired" (a saved session whose expiry has
    passed) or "valid". Credentials in the environment (`CEREBRO_TOKEN`) count as a
    session: the user configured them explicitly and the server is the judge."""
    if os.environ.get("CEREBRO_TOKEN"):
        return "valid"
    cfg = load_login_config()
    if not cfg or not cfg.get("token"):
        return "none"
    expires = cfg.get("expires_at")
    if expires:
        try:
            when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        except ValueError:
            return "valid"
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when <= datetime.now(timezone.utc):
            return "expired"
    return "valid"


def load_login_config() -> dict[str, Any] | None:
    """Reads back what `cerebro login` saved, or `None` if there's no session yet
    or the file is unreadable/corrupt. Not currently consumed by anything in this
    package -- see the KNOWN GAP note above; kept here for whichever side ends up
    reading it (this package or `cerebro_clients.config`)."""
    if not CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None
