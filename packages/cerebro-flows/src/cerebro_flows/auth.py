"""Token auth with scopes - reads the shared `cerebro_auth` schema (owned by the
`cerebro-auth` package) instead of a local `api_tokens` table.

This module used to be an exact mirror of `cerebro_docs.auth` with its own local
`api_tokens` table. Token issuance/management now live exclusively in the
`cerebro-auth` service; this module only implements the READ path: resolving a
bearer token to a `Principal` by querying `cerebro_auth.api_tokens` (same Postgres
instance, same connection pool as this service's own tables - a cross-schema query,
not a network call). `cerebro-auth` implements the identical resolution logic for
its own use; this is meant to be its exact mirror.

Resolution algorithm (see `get_principal`):
    1. Look up the token in `cerebro_auth.api_tokens` by hash; missing/revoked -> 401.
    2. Module gate: the effective `allowed_modules` must contain 'flows', else this
       token isn't valid for this service at all.
    3. `access_level` comes from `cerebro_auth.users` (when the token has a
       `user_id`) or from the token's own `access_level` column (service/root-
       adjacent tokens with no `user_id`).
    4. `admin` -> full access to its own `allowed_modules` (or all modules if NULL).
       `module_scopes` is never consulted for admin.
    5. `owner` -> union of `allowed_modules`/`module_scopes` across every group the
       user belongs to (via `cerebro_auth.group_scopes`/`user_groups`), narrowed
       (never widened) by the token's own `allowed_modules`/`module_scopes` if set.
    6. `user` (base case) -> the token's own `allowed_modules`/`module_scopes`, as-is.
    7. The flows-specific fine scope is `effective_module_scopes['flows']['categories']`
       - the direct replacement for the old `allowed_categories` DB column.

The root-token bootstrap (`secrets.compare_digest` against `settings.api_token`,
granting admin with no DB row at all) is unchanged.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

TOKEN_PREFIX = "cbrf_"
VALID_SCOPES = ("read", "write", "admin")

ROOT_TOKEN_NAME = "root"

# All modules an admin with no explicit `allowed_modules` inherits (step 4).
ALL_MODULES = ("memory", "docs", "flows")

THIS_MODULE = "flows"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
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
        if self.allowed_categories is None or slug is None:
            return True
        return slug in self.allowed_categories

    def filter_slugs(self, slugs: list[str]) -> list[str]:
        if self.allowed_categories is None:
            return slugs
        return [s for s in slugs if s in self.allowed_categories]


# --------------------------------------------------------------------------- resolution helpers


def _module_categories(module_scopes: dict[str, Any] | None) -> frozenset[str] | None:
    """Extracts the flows-specific `categories` restriction out of a `module_scopes`
    JSONB blob (step 7). None means "no restriction within flows" - either because
    `module_scopes` has no 'flows' key at all, or because `categories` is null."""
    if not module_scopes:
        return None
    flows_scope = module_scopes.get(THIS_MODULE)
    if not flows_scope:
        return None
    categories = flows_scope.get("categories")
    return frozenset(categories) if categories is not None else None


async def _resolve_owner_base(
    pool: asyncpg.Pool, user_id: uuid.UUID
) -> tuple[frozenset[str], frozenset[str] | None]:
    """Step 5: the UNION of `allowed_modules`/flows-`categories` across every group
    this user belongs to. Returns (base_allowed_modules, base_flows_categories),
    where None for the categories half means "unrestricted" (a group grants flows
    with no categories restriction, which -- being a union -- wins over any other
    group's narrower restriction)."""
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
    flows_category_sets: list[frozenset[str] | None] = []
    for g in group_rows:
        g_modules = set(g["allowed_modules"] or [])
        base_modules |= g_modules
        if THIS_MODULE in g_modules:
            flows_category_sets.append(_module_categories(g["module_scopes"]))

    if any(c is None for c in flows_category_sets):
        base_categories: frozenset[str] | None = None
    elif flows_category_sets:
        base_categories = frozenset().union(*flows_category_sets)
    else:
        # No group grants 'flows' at all: empty restriction, irrelevant in practice
        # since the module gate below will reject this token anyway (unless the
        # token itself widens allowed_modules, which it cannot - narrowing only).
        base_categories = frozenset()

    return frozenset(base_modules), base_categories


async def _resolve_group_mates(pool: asyncpg.Pool, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Every user id that shares at least one group with `user_id`, including
    `user_id` itself - a self-join over `cerebro_auth.user_groups`."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT ug2.user_id
        FROM cerebro_auth.user_groups ug1
        JOIN cerebro_auth.user_groups ug2 ON ug2.group_id = ug1.group_id
        WHERE ug1.user_id = $1
        """,
        user_id,
    )
    return [r["user_id"] for r in rows]


# --------------------------------------------------------------------------- auth dependency


async def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
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

    # Step 1
    row = await pool.fetchrow(
        """
        SELECT name, user_id, scopes, access_level, allowed_modules, module_scopes
        FROM cerebro_auth.api_tokens
        WHERE token_hash = $1 AND revoked_at IS NULL
        """,
        hash_token(token),
    )
    if row is None:
        raise unauthorized

    user_id: uuid.UUID | None = row["user_id"]
    token_allowed_modules: list[str] | None = row["allowed_modules"]
    token_module_scopes: dict[str, Any] | None = row["module_scopes"]

    # Step 3
    if user_id is not None:
        user_row = await pool.fetchrow(
            "SELECT access_level FROM cerebro_auth.users WHERE id = $1 AND revoked_at IS NULL",
            user_id,
        )
        if user_row is None:
            raise unauthorized
        access_level: str = user_row["access_level"]
    else:
        access_level = row["access_level"]

    # Steps 4-6: resolve effective allowed_modules / flows-categories
    if access_level == "admin":
        effective_allowed_modules = (
            frozenset(token_allowed_modules) if token_allowed_modules is not None else frozenset(ALL_MODULES)
        )
        # module_scopes is never consulted for admin - full access within the gate.
        effective_categories: frozenset[str] | None = None

    elif access_level == "owner":
        if user_id is None:
            # Defensive: 'owner' is a per-user concept (group membership). A
            # service/root-adjacent token (user_id IS NULL) reporting access_level
            # 'owner' is a misconfiguration this algorithm doesn't otherwise define
            # - treated as zero access rather than guessed at.
            effective_allowed_modules = frozenset()
            effective_categories = frozenset()
        else:
            base_modules, base_categories = await _resolve_owner_base(pool, user_id)
            if token_allowed_modules is not None:
                effective_allowed_modules = base_modules & frozenset(token_allowed_modules)
            else:
                effective_allowed_modules = base_modules

            token_categories = _module_categories(token_module_scopes)
            if token_categories is not None:
                effective_categories = (
                    token_categories if base_categories is None else base_categories & token_categories
                )
            else:
                effective_categories = base_categories

    else:  # 'user' base case (and any unrecognized value, defensively treated the same)
        effective_allowed_modules = frozenset(token_allowed_modules) if token_allowed_modules is not None else frozenset()
        effective_categories = _module_categories(token_module_scopes)

    # Step 2: module gate, enforced before consulting the fine (categories) scope.
    if THIS_MODULE not in effective_allowed_modules:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"token '{row['name']}' is not authorized for the '{THIS_MODULE}' module",
        )

    # owner_filter: the ownership gate, independent of the module/category gate above.
    if access_level == "admin" or user_id is None:
        owner_filter: dict[str, Any] | None = None
    elif access_level == "owner":
        mates = await _resolve_group_mates(pool, user_id)
        owner_filter = {"user_id_in": mates}
    elif access_level == "user":
        owner_filter = {"user_id": user_id}
    else:
        owner_filter = None

    return Principal(
        name=row["name"],
        scopes=frozenset(row["scopes"]),
        access_level=access_level,
        user_id=user_id,
        allowed_categories=effective_categories,
        owner_filter=owner_filter,
        is_root=False,
    )


def require_scope(scope: str):
    async def _dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token '{principal.name}' is missing required scope '{scope}' (has: {sorted(principal.scopes)})",
            )
        return principal

    return _dependency
