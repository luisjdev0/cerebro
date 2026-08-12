"""Token auth with scopes (plan_v2.md SS9 "Autorizacion v1.0").

Two kinds of credential are accepted on `Authorization: Bearer <token>`:

- The **root token** (`Settings.api_token`, from `.env`/`API_TOKEN`): compared with
  `secrets.compare_digest`, exactly like Fase 1. Kept for backwards compatibility --
  it is documented as the "root token" and gets every scope (`read`, `write`,
  `admin`) over every context, unconditionally. There is no database row for it.
- **Named tokens**, minted via `POST /tokens` (or `cerebro-memory token create`) and
  stored in `api_tokens` (`db/migrations/004_api_tokens.sql`) as a SHA-256 hash --
  the plaintext value is shown exactly once, at creation time, and never stored or
  logged. Each has a stable `name` (the real, non-self-declared identity of the
  calling agent -- it overrides whatever `X-Agent-Name` the caller sends, see
  `agent_name()` in `api.py`), explicit `scopes`, and an optional
  `allowed_contexts` allow-list (`None`/NULL = every context).

Enforcement (plan_v2.md SS9):
    - `read`  -> every GET (except /health, which needs no auth at all).
    - `write` -> POST/PATCH/DELETE of memories/contexts/edges/disambiguation-resolve.
    - `admin` -> token management (`/tokens/*`) and `GET /disambiguations/export`;
      also required for `DELETE /contexts/{slug}` (destructive, see api.py).

`allowed_contexts` (when not None) is enforced three ways, per endpoint:
    - search: an explicit `context`/`scope=<slug>` outside the list is 403; with no
      explicit context, results (scope=all) and Context Engine candidates (scope=auto)
      are silently narrowed to the allowed set -- never surfaced as "ambiguous"
      candidates, never returned as hits.
    - writes: creating/updating/deleting something whose context is outside the list
      is 403.
    - stats/timeline: rows for disallowed contexts are filtered out (not surfaced,
      not errored) unless an explicit out-of-scope `context` was requested (then 403).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

TOKEN_PREFIX = "kos_"
VALID_SCOPES = ("read", "write", "admin")

ROOT_TOKEN_NAME = "root"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """A fresh random bearer token. Never derived from anything guessable, and never
    persisted anywhere except as its SHA-256 hash (see `create_api_token`)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of the current request: who they are (for the audit
    log / `memory.source`), what they can do (`scopes`), and where (`allowed_contexts`,
    None meaning "everywhere")."""

    name: str
    scopes: frozenset[str]
    allowed_contexts: frozenset[str] | None
    is_root: bool = False

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def context_allowed(self, slug: str | None) -> bool:
        """True if this principal may touch `slug` (or `slug is None` -- callers use
        that for "no context specified yet" cases that get resolved later)."""
        if self.allowed_contexts is None or slug is None:
            return True
        return slug in self.allowed_contexts

    def filter_slugs(self, slugs: list[str]) -> list[str]:
        """Narrow a list of context slugs down to what this principal may see."""
        if self.allowed_contexts is None:
            return slugs
        return [s for s in slugs if s in self.allowed_contexts]


class DuplicateTokenNameError(ValueError):
    """Raised when creating a token whose name already has an active token."""


class TokenNotFoundError(LookupError):
    """Raised when revoking a name with no active token."""


class InvalidScopesError(ValueError):
    """Raised when creating a token with an empty or unknown scope list."""


# --------------------------------------------------------------------------- auth dependency


async def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """FastAPI dependency: resolve `Authorization: Bearer <token>` to a `Principal`.

    401 if missing, malformed, or matching neither the root token nor an active row
    in `api_tokens`. Does not check scopes -- see `require_scope`.
    """
    settings = request.app.state.settings
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid Authorization: Bearer <token>",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized

    root_expected = f"Bearer {settings.api_token}"
    if secrets.compare_digest(authorization, root_expected):
        return Principal(
            name=ROOT_TOKEN_NAME,
            scopes=frozenset(VALID_SCOPES),
            allowed_contexts=None,
            is_root=True,
        )

    token = authorization[len("Bearer ") :]
    pool: asyncpg.Pool = request.app.state.pool
    row = await pool.fetchrow(
        "SELECT name, scopes, allowed_contexts FROM api_tokens WHERE token_hash = $1 AND revoked_at IS NULL",
        hash_token(token),
    )
    if row is None:
        raise unauthorized

    allowed = frozenset(row["allowed_contexts"]) if row["allowed_contexts"] is not None else None
    return Principal(
        name=row["name"],
        scopes=frozenset(row["scopes"]),
        allowed_contexts=allowed,
        is_root=False,
    )


def require_scope(scope: str):
    """Dependency factory: 401 (via get_principal) if unauthenticated, 403 if
    authenticated but missing `scope`. Returns the `Principal` so handlers that need
    to check `allowed_contexts` can just add `Annotated[Principal, Depends(require_scope(...))]`
    to their signature instead of a second dependency."""

    async def _dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token '{principal.name}' is missing required scope '{scope}' (has: {sorted(principal.scopes)})",
            )
        return principal

    return _dependency


# --------------------------------------------------------------------------- token CRUD


async def create_api_token(
    pool: asyncpg.Pool,
    *,
    name: str,
    scopes: list[str],
    allowed_contexts: list[str] | None,
) -> dict[str, Any]:
    invalid = sorted(set(scopes) - set(VALID_SCOPES))
    if invalid or not scopes:
        raise InvalidScopesError(
            f"invalid scopes {invalid}: must be a non-empty subset of {VALID_SCOPES}"
            if invalid
            else "at least one scope is required"
        )

    plaintext = generate_token()
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO api_tokens (token_hash, name, scopes, allowed_contexts)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, scopes, allowed_contexts, created_at, revoked_at
            """,
            hash_token(plaintext),
            name,
            scopes,
            allowed_contexts,
        )
    except asyncpg.UniqueViolationError as exc:
        raise DuplicateTokenNameError(name) from exc

    return {**dict(row), "token": plaintext}


async def list_api_tokens(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, name, scopes, allowed_contexts, created_at, revoked_at FROM api_tokens ORDER BY created_at"
    )


async def revoke_api_token(pool: asyncpg.Pool, name: str) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        UPDATE api_tokens SET revoked_at = now()
        WHERE name = $1 AND revoked_at IS NULL
        RETURNING id, name, scopes, allowed_contexts, created_at, revoked_at
        """,
        name,
    )
    if row is None:
        raise TokenNotFoundError(name)
    return row
