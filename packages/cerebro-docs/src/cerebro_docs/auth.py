"""Token auth with scopes/access-levels, reading from the shared `cerebro_auth`
schema instead of a local `api_tokens` table (ecosistema-cerebro.md SS13, auth
unification).

Two kinds of credential accepted in `Authorization: Bearer <token>`:

- The **root token** (`Settings.api_token`, from `.env`/`API_TOKEN`): compared with
  `secrets.compare_digest`. Full admin access, no DB row - unchanged from before.
- **Shared tokens**, issued and managed exclusively by the `cerebro-auth` service and
  stored in `cerebro_auth.api_tokens` (same Postgres instance, same connection pool -
  a cross-schema query, not a network call). Token management (`POST/GET /tokens`,
  `DELETE /tokens/{name}`) no longer lives in this service at all; see the
  `cerebro-auth` package.

Resolution algorithm (this is the authoritative version - `cerebro-auth` implements
the identical logic for its own use; this module replicates the READ path only):

    1. Look up the token in `cerebro_auth.api_tokens` by `token_hash`; missing or
       `revoked_at IS NOT NULL` -> 401, same as before.
    2. Module gate (specific to this service): the effective `allowed_modules`
       (resolved per steps 3-6 below) must contain 'docs', else 401/403 - this
       token isn't valid for cerebro-docs at all.
    3. `access_level` comes from `cerebro_auth.users.access_level` when the token
       has a `user_id` (the token's own `access_level` column is NULL in that
       case); otherwise from the token's own `access_level` column directly
       (service/root-adjacent token).
    4. `admin`: effective `allowed_modules` = the token's own `allowed_modules` if
       not NULL, else every module (inherits all). `module_scopes` is never
       consulted for admin - full access within whatever module gate passed.
    5. `owner`: effective base = the UNION of `allowed_modules`/`module_scopes`
       from `cerebro_auth.group_scopes` across every group in
       `cerebro_auth.user_groups` for this user. If the token has its own
       `allowed_modules`/`module_scopes`, intersect/narrow the base with those
       (never widen). No groups + no token-level scope -> empty effective access
       (a misconfiguration, not a permissive default).
    6. `user` (base case): effective `allowed_modules`/`module_scopes` = the
       token's own values as-is (nothing to inherit at this level).
    7. The docs-specific fine scope is
       `effective_module_scopes.get('docs', {}).get('categories')` - this is the
       direct replacement for the old `allowed_categories` DB column (a list of
       category slugs, or `None`/absent meaning "no restriction within docs").

Enforcement of the service-level `scopes` (`read`/`write`/`admin`) is unchanged:
    - `read`  -> every GET (except /health, which needs no auth).
    - `write` -> POST/PATCH of categories/documents, DELETE /documents/{id}.
    - `admin` -> `DELETE /categories/{slug}` (destructive/cascading). NOTE:
      `cerebro_auth.api_tokens.scopes` only ever holds 'read'/'write' - 'admin' is
      an access_level in the shared schema, not a token scope - so an `admin`
      access_level synthesizes the 'admin' service-scope here to keep this
      enforcement working.

`allowed_categories` (when not None) is applied the same way as before: 403 on
explicit writes/reads outside the list; listings with no explicit category are
silently narrowed to the allowed set, never rejected.

`owner_filter` is a SEPARATE, additional gate - "who owns this document" - kept
apart from the module/category gate above. See `Principal.owner_filter` and its
computation at the bottom of `get_principal`.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

TOKEN_PREFIX = "cbrd_"
VALID_SCOPES = ("read", "write", "admin")
ALL_MODULES = ("memory", "docs", "flows")
THIS_MODULE = "docs"
VALID_ACCESS_LEVELS = ("user", "owner", "admin")

ROOT_TOKEN_NAME = "root"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """A fresh random bearer token. Never derived from anything guessable. Kept
    here as a small utility (e.g. for tests that need a plausible-looking token
    string) even though issuance itself now lives entirely in `cerebro-auth`."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of the current request: who they are (for
    `created_by`), what they can do (`scopes`, `access_level`), where
    (`allowed_categories`, None = all within the 'docs' module), and which
    documents they own (`owner_filter`, None = no ownership restriction)."""

    name: str
    scopes: frozenset[str]
    access_level: str
    user_id: uuid.UUID | None
    allowed_categories: frozenset[str] | None
    owner_filter: dict[str, Any] | None
    is_root: bool = False

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def category_allowed(self, slug: str | None) -> bool:
        """True if this principal can touch `slug` (`slug is None` -- used by
        callers for "category not decided yet" cases)."""
        if self.allowed_categories is None or slug is None:
            return True
        return slug in self.allowed_categories

    def filter_slugs(self, slugs: list[str]) -> list[str]:
        """Narrows a list of category slugs down to what this principal can see."""
        if self.allowed_categories is None:
            return slugs
        return [s for s in slugs if s in self.allowed_categories]


# --------------------------------------------------------------------------- module_scopes merge/narrow helpers


def _union_lists(values: list[list[Any]]) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        for item in value:
            if item not in seen:
                seen.append(item)
    return seen


def _merge_scope_value(a: Any, b: Any) -> Any:
    """Merges two module_scopes leaf values (one per group the user belongs to):
    lists union, dicts merge key-by-key. Anything else keeps `a` - in practice
    every leaf here is a list (e.g. `categories`) or a nested dict."""
    if isinstance(a, list) and isinstance(b, list):
        return _union_lists([a, b])
    if isinstance(a, dict) and isinstance(b, dict):
        return _merge_module_scopes(a, b)
    return a


def _merge_module_scopes(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Union of two `module_scopes` dicts - more permissive per key, never a
    subtraction. Used to combine scopes across every group an 'owner' belongs to."""
    merged = dict(a)
    for key, value in b.items():
        merged[key] = _merge_scope_value(merged[key], value) if key in merged else value
    return merged


def _narrow_scope_value(base: Any, narrow: Any) -> Any:
    if isinstance(base, list) and isinstance(narrow, list):
        return [item for item in base if item in narrow]
    if isinstance(base, dict) and isinstance(narrow, dict):
        return _narrow_module_scopes(base, narrow)
    return narrow


def _narrow_module_scopes(base: dict[str, Any], narrow: dict[str, Any] | None) -> dict[str, Any]:
    """Intersects `base` (e.g. the union of a user's group scopes) with a
    token-level override. A key ABSENT from `base` means "no restriction yet" (the
    widest possible state), so `narrow` introducing it is still a narrowing, never
    a widening; a key present in both is intersected."""
    if narrow is None:
        return base
    result = dict(base)
    for key, narrow_value in narrow.items():
        result[key] = _narrow_scope_value(result[key], narrow_value) if key in result else narrow_value
    return result


def _narrow_allowed_modules(base: set[str], narrow: list[str] | None) -> set[str]:
    """Unlike module_scopes, an allowed_modules key ABSENT from `base` means "not
    granted" (the narrowest state) - so `narrow` can only ever remove modules from
    `base`, never introduce ones `base` didn't already have."""
    if narrow is None:
        return base
    return base & set(narrow)


# --------------------------------------------------------------------------- auth dependency


async def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """FastAPI dependency: resolve `Authorization: Bearer <token>` to a `Principal`.

    401 if missing, malformed, or matching neither the root token nor an active row
    in `cerebro_auth.api_tokens`; 403 if the token resolves but doesn't carry
    the 'docs' module. Does not check `scopes` -- see `require_scope`.
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
            access_level="admin",
            user_id=None,
            allowed_categories=None,
            owner_filter=None,
            is_root=True,
        )

    token = authorization[len("Bearer ") :]
    pool: asyncpg.Pool = request.app.state.pool
    row = await pool.fetchrow(
        """
        SELECT t.name AS token_name, t.user_id, t.scopes,
               t.access_level AS token_access_level,
               t.allowed_modules AS token_allowed_modules,
               t.module_scopes AS token_module_scopes,
               u.name AS user_name, u.access_level AS user_access_level
        FROM cerebro_auth.api_tokens t
        LEFT JOIN cerebro_auth.users u ON u.id = t.user_id
        WHERE t.token_hash = $1 AND t.revoked_at IS NULL
        """,
        hash_token(token),
    )
    if row is None:
        raise unauthorized

    user_id: uuid.UUID | None = row["user_id"]
    access_level: str | None = row["user_access_level"] if user_id is not None else row["token_access_level"]
    if access_level not in VALID_ACCESS_LEVELS:
        # Explicit error, never guessing (same conservative criterion as the rest
        # of this service): a token/user row with no valid access_level is a
        # misconfiguration in `cerebro_auth`, not a case to fall back silently.
        raise unauthorized

    name: str = row["user_name"] if user_id is not None else row["token_name"]
    token_allowed_modules: list[str] | None = row["token_allowed_modules"]
    token_module_scopes: dict[str, Any] | None = row["token_module_scopes"]

    module_scopes: dict[str, Any]
    if access_level == "admin":
        allowed_modules = set(token_allowed_modules) if token_allowed_modules is not None else set(ALL_MODULES)
        module_scopes = {}  # never consulted for admin
    elif access_level == "owner":
        group_rows = await pool.fetch(
            """
            SELECT gs.allowed_modules, gs.module_scopes
            FROM cerebro_auth.user_groups ug
            JOIN cerebro_auth.group_scopes gs ON gs.group_id = ug.group_id
            WHERE ug.user_id = $1
            """,
            user_id,
        )
        base_modules: set[str] = set()
        base_scopes: dict[str, Any] = {}
        for group_row in group_rows:
            if group_row["allowed_modules"]:
                base_modules |= set(group_row["allowed_modules"])
            if group_row["module_scopes"]:
                base_scopes = _merge_module_scopes(base_scopes, group_row["module_scopes"])
        allowed_modules = _narrow_allowed_modules(base_modules, token_allowed_modules)
        module_scopes = _narrow_module_scopes(base_scopes, token_module_scopes)
    else:  # access_level == "user" -- base case, nothing to inherit
        allowed_modules = set(token_allowed_modules) if token_allowed_modules is not None else set()
        module_scopes = token_module_scopes or {}

    if THIS_MODULE not in allowed_modules:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"token '{name}' is not authorized for the 'docs' module",
        )

    docs_scope = module_scopes.get(THIS_MODULE) if isinstance(module_scopes, dict) else None
    categories = docs_scope.get("categories") if isinstance(docs_scope, dict) else None
    allowed_categories = frozenset(categories) if categories is not None else None

    scopes = set(row["scopes"] or [])
    if access_level == "admin":
        scopes.add("admin")

    owner_filter: dict[str, Any] | None
    if access_level == "user" and user_id is not None:
        owner_filter = {"user_id": user_id}
    elif access_level == "owner":
        mate_rows = await pool.fetch(
            """
            SELECT DISTINCT ug2.user_id
            FROM cerebro_auth.user_groups ug1
            JOIN cerebro_auth.user_groups ug2 ON ug2.group_id = ug1.group_id
            WHERE ug1.user_id = $1
            """,
            user_id,
        )
        owner_filter = {"user_id_in": [r["user_id"] for r in mate_rows]}
    else:  # admin, or any token with no user_id at all (root-adjacent/service)
        owner_filter = None

    return Principal(
        name=name,
        scopes=frozenset(scopes),
        access_level=access_level,
        user_id=user_id,
        allowed_categories=allowed_categories,
        owner_filter=owner_filter,
        is_root=False,
    )


def require_scope(scope: str):
    """Dependency factory: 401 (via get_principal) if unauthenticated, 403 if
    authenticated but missing `scope`."""

    async def _dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token '{principal.name}' is missing required scope '{scope}' (has: {sorted(principal.scopes)})",
            )
        return principal

    return _dependency
