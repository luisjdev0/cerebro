"""Token auth backed by the shared `cerebro_auth` schema (unified authentication
across cerebro-memory/cerebro-docs/cerebro-flows).

Two kinds of credential are accepted on `Authorization: Bearer <token>`:

- The **root token** (`Settings.api_token`, from `.env`/`API_TOKEN`): compared with
  `secrets.compare_digest`, unchanged from before this migration. Kept for backwards
  compatibility -- it gets every scope (`read`, `write`, `admin`) over every context,
  unconditionally. There is no database row for it.
- **Shared tokens**, minted and managed exclusively by the new `cerebro-auth` service
  (this service no longer has its own `/tokens` routes) and stored in
  `cerebro_auth.api_tokens` as a SHA-256 hash. `get_principal` below resolves one of
  these tokens by reading `cerebro_auth.api_tokens`/`users`/`user_groups`/
  `group_scopes` -- a cross-schema query within the same Postgres instance/connection
  pool, not a network call to `cerebro-auth`.

Resolution algorithm (mirrors the identical logic in `cerebro_auth`'s own service --
this module only implements the READ path over the same tables):

1. Look up the token by `token_hash` in `cerebro_auth.api_tokens`; missing or
   `revoked_at IS NOT NULL` -> 401.
2. Module gate (specific to this service): the effective `allowed_modules` must
   contain `"memory"`, else this token is not valid for cerebro-memory at all.
3. `user_id IS NOT NULL` -> `access_level` comes from `cerebro_auth.users.access_level`
   (the token's own `access_level` column is NULL in this case). `user_id IS NULL` ->
   `access_level` comes from the token's own `access_level` column (a service/
   root-adjacent token).
4. `access_level == "admin"`: effective `allowed_modules` = the token's own
   `allowed_modules` if not NULL, else every module (`memory`, `docs`, `flows`).
   `module_scopes` is never consulted for admin.
5. `access_level == "owner"`: effective base = the UNION of `allowed_modules`/
   `module_scopes` across every group (`cerebro_auth.group_scopes`) this user
   belongs to (`cerebro_auth.user_groups`). The token's own `allowed_modules`/
   `module_scopes`, if set, narrow that base further (intersect, never widen). No
   groups and no token-level narrowing -> empty effective access (a misconfiguration,
   not a permissive default).
6. `access_level == "user"` (base case): effective `allowed_modules`/`module_scopes`
   are the token's own values, as-is -- nothing to inherit at this level.
7. The memory-specific fine scope is
   `effective_module_scopes.get("memory", {}).get("contexts")` -- the direct
   replacement for the old `allowed_contexts` column (a list of context slugs, or
   `None`/absent meaning "no restriction within memory").

`owner_user_id`-based filtering (the separate "who owns this" gate, Change 3) is
computed here too as `Principal.owner_filter`:

- `access_level == "user"` and `user_id` is set -> `{"user_id": user_id}`.
- `access_level == "owner"` -> `{"user_id_in": [...]}`, the ids of every user that
  shares at least one group with this user (including the user themselves), via a
  self-join over `cerebro_auth.user_groups`.
- `access_level == "admin"`, or any token with no `user_id` (root/service token) ->
  `None` (no ownership restriction).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

TOKEN_PREFIX = "kos_"
VALID_SCOPES = ("read", "write", "admin")

ROOT_TOKEN_NAME = "root"

# This service's own name in cerebro_auth.api_tokens.allowed_modules /
# cerebro_auth.group_scopes.allowed_modules / .module_scopes.
MODULE = "memory"

# Every module known to the ecosystem -- what an admin-level token inherits when its
# own `allowed_modules` is NULL (step 4 above).
ALL_MODULES = ("memory", "docs", "flows")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """A fresh random bearer token. Never derived from anything guessable. Token
    *creation* now lives exclusively in `cerebro-auth`; this is kept here because it
    (and `hash_token`) are still useful to fabricate rows directly in tests."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of the current request: who they are (for the audit
    log / `memory.source`), what they can do (`scopes`, `access_level`), where
    (`allowed_contexts`, None meaning "everywhere within memory"), and whose content
    they may touch (`owner_filter`, None meaning "no ownership restriction")."""

    name: str
    scopes: frozenset[str]
    allowed_contexts: frozenset[str] | None
    access_level: str
    user_id: uuid.UUID | None
    owner_filter: dict[str, Any] | None
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


# --------------------------------------------------------------------------- auth dependency


def _decode_jsonb(value: Any) -> Any:
    """asyncpg returns JSONB columns as raw text unless a codec is registered on the
    connection (none is, here) -- decode explicitly, same pattern as the
    `disambiguation_log.candidates` handling in api.py."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _merge_module_scopes(base: dict[str, Any], other: dict[str, Any] | None) -> None:
    """In-place UNION of `other` (one group's `module_scopes`) into `base` (the
    running union across every group a user belongs to, step 5). Within a module, a
    `contexts` list is unioned (deduplicated); if either side has no `contexts` key at
    all (meaning "no restriction within that module"), the union is unrestricted too."""
    for module, scope in (other or {}).items():
        scope = scope or {}
        if module not in base:
            base[module] = dict(scope)
            continue
        existing = base[module]
        existing_contexts = existing.get("contexts")
        other_contexts = scope.get("contexts")
        if "contexts" not in existing or "contexts" not in scope:
            existing.pop("contexts", None)  # unrestricted side wins
        elif existing_contexts is None or other_contexts is None:
            existing["contexts"] = None
        else:
            existing["contexts"] = list(dict.fromkeys([*existing_contexts, *other_contexts]))


def _narrow_module_scopes(base: dict[str, Any], token_scopes: dict[str, Any] | None) -> dict[str, Any]:
    """Narrow `base` (the group union) with a token's own `module_scopes`, per module
    and, within `contexts`, by intersection -- never widening what the base grants
    (step 5, "narrow the base with those (never widen)"). A module the token doesn't
    mention at all is left as-is (nothing to narrow it with)."""
    token_scopes = token_scopes or {}
    result: dict[str, Any] = {}
    for module, base_scope in base.items():
        if module not in token_scopes:
            result[module] = base_scope
            continue
        token_scope = token_scopes.get(module) or {}
        base_contexts = base_scope.get("contexts")
        token_contexts = token_scope.get("contexts")
        merged = dict(base_scope)
        if base_contexts is None and token_contexts is None:
            merged.pop("contexts", None)
        elif base_contexts is None:
            merged["contexts"] = token_contexts
        elif token_contexts is None:
            merged["contexts"] = base_contexts
        else:
            merged["contexts"] = [c for c in base_contexts if c in token_contexts]
        result[module] = merged
    return result


async def _resolve_owner_group_scopes(pool: asyncpg.Pool, user_id: uuid.UUID) -> dict[str, Any]:
    """UNION of `allowed_modules`/`module_scopes` across every group `user_id`
    belongs to (step 5's "effective base")."""
    rows = await pool.fetch(
        """
        SELECT gs.allowed_modules, gs.module_scopes
        FROM cerebro_auth.group_scopes gs
        JOIN cerebro_auth.user_groups ug ON ug.group_id = gs.group_id
        WHERE ug.user_id = $1
        """,
        user_id,
    )
    base_modules: set[str] = set()
    base_module_scopes: dict[str, Any] = {}
    for row in rows:
        if row["allowed_modules"]:
            base_modules.update(row["allowed_modules"])
        _merge_module_scopes(base_module_scopes, _decode_jsonb(row["module_scopes"]))
    return {"modules": base_modules, "module_scopes": base_module_scopes}


async def _resolve_owner_mates(pool: asyncpg.Pool, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Every user id that shares at least one group with `user_id`, including
    `user_id` itself -- a self-join over `cerebro_auth.user_groups`."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT ug2.user_id
        FROM cerebro_auth.user_groups ug1
        JOIN cerebro_auth.user_groups ug2 ON ug2.group_id = ug1.group_id
        WHERE ug1.user_id = $1
        """,
        user_id,
    )
    mates = {row["user_id"] for row in rows}
    mates.add(user_id)  # a lone user with no groups still owns their own content
    return list(mates)


async def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """FastAPI dependency: resolve `Authorization: Bearer <token>` to a `Principal`.

    401 if missing, malformed, matching neither the root token nor an active row in
    `cerebro_auth.api_tokens`, or if the token resolves but is not scoped to the
    `"memory"` module at all (see module gate, step 2 above). Does not check
    read/write scopes -- see `require_scope`.
    """
    settings = request.app.state.settings
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid Authorization: Bearer <token>",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not authorization or not authorization.startswith("Bearer "):
        raise unauthorized

    # Root-token bootstrap -- unchanged, no DB row, full access.
    root_expected = f"Bearer {settings.api_token}"
    if secrets.compare_digest(authorization, root_expected):
        return Principal(
            name=ROOT_TOKEN_NAME,
            scopes=frozenset(VALID_SCOPES),
            allowed_contexts=None,
            access_level="admin",
            user_id=None,
            owner_filter=None,
            is_root=True,
        )

    token = authorization[len("Bearer ") :]
    pool: asyncpg.Pool = request.app.state.pool
    row = await pool.fetchrow(
        """
        SELECT
            t.name,
            t.user_id,
            t.scopes,
            t.access_level AS token_access_level,
            t.allowed_modules AS token_allowed_modules,
            t.module_scopes AS token_module_scopes,
            u.access_level AS user_access_level
        FROM cerebro_auth.api_tokens t
        LEFT JOIN cerebro_auth.users u ON u.id = t.user_id
        WHERE t.token_hash = $1 AND t.revoked_at IS NULL
        """,
        hash_token(token),
    )
    if row is None:
        raise unauthorized

    user_id: uuid.UUID | None = row["user_id"]
    access_level: str = row["user_access_level"] if user_id is not None else row["token_access_level"]
    token_allowed_modules: list[str] | None = row["token_allowed_modules"]
    token_module_scopes = _decode_jsonb(row["token_module_scopes"])

    effective_module_scopes: dict[str, Any] = {}

    if access_level == "admin":
        # step 4: module_scopes never consulted for admin.
        effective_modules: set[str] | None = (
            set(token_allowed_modules) if token_allowed_modules is not None else set(ALL_MODULES)
        )
    elif access_level == "owner":
        base = await _resolve_owner_group_scopes(pool, user_id)  # user_id is not None here
        base_modules, base_module_scopes = base["modules"], base["module_scopes"]
        if token_allowed_modules is not None:
            effective_modules = base_modules & set(token_allowed_modules)
        else:
            effective_modules = base_modules
        effective_module_scopes = _narrow_module_scopes(base_module_scopes, token_module_scopes)
    else:  # access_level == "user" (base case, step 6): the token's own values as-is.
        # NOTE (ambiguity, see final report): the spec doesn't pin down what NULL
        # `allowed_modules` means for a plain "user" token specifically (only "as-is,
        # nothing to inherit"). We treat NULL here the same way None has always meant
        # "unrestricted" elsewhere in this module (allowed_contexts=None, etc.) --
        # this may need reconciling once cerebro-auth's own read path is final.
        effective_modules = set(token_allowed_modules) if token_allowed_modules is not None else None
        effective_module_scopes = token_module_scopes or {}

    # step 2: module gate, specific to this service.
    if effective_modules is not None and MODULE not in effective_modules:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"token '{row['name']}' is not scoped to the '{MODULE}' module",
        )

    if access_level == "admin":
        allowed_contexts = None
    else:
        memory_scope = effective_module_scopes.get(MODULE) or {}
        raw_contexts = memory_scope.get("contexts")
        allowed_contexts = frozenset(raw_contexts) if raw_contexts is not None else None

    if user_id is None:
        owner_filter: dict[str, Any] | None = None
    elif access_level == "admin":
        owner_filter = None
    elif access_level == "owner":
        owner_filter = {"user_id_in": await _resolve_owner_mates(pool, user_id)}
    elif access_level == "user":
        owner_filter = {"user_id": user_id}
    else:
        owner_filter = None

    # `api_tokens.scopes` only ever holds 'read'/'write' under the unified schema
    # ('admin' is now an access_level, not a scope) -- synthesize it back into the
    # service-scope set for admin-level principals so routes still gated by
    # `require_scope("admin")` (e.g. DELETE /contexts/{slug}) keep working for any
    # admin, not just the root token. Same synthesis as cerebro-docs.
    scopes = set(row["scopes"] or ())
    if access_level == "admin":
        scopes.add("admin")

    return Principal(
        name=row["name"],
        scopes=frozenset(scopes),
        allowed_contexts=allowed_contexts,
        access_level=access_level,
        user_id=user_id,
        owner_filter=owner_filter,
        is_root=False,
    )


def require_scope(scope: str):
    """Dependency factory: 401 (via get_principal) if unauthenticated, 403 if
    authenticated but missing `scope`. Returns the `Principal` so handlers that need
    to check `allowed_contexts`/`owner_filter` can just add
    `Annotated[Principal, Depends(require_scope(...))]` to their signature instead of
    a second dependency."""

    async def _dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token '{principal.name}' is missing required scope '{scope}' (has: {sorted(principal.scopes)})",
            )
        return principal

    return _dependency
