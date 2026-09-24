"""Token auth for cerebro-auth's OWN routes (all admin-gated except `/login` and
`/health`) plus the permission-resolution algorithm that makes cerebro-auth the
ecosystem's source of truth for identity.

Two things live here, and they are related but distinct:

1. `get_principal` / `require_admin`: the dependency chain that gates
   cerebro-auth's own admin routes (POST /users, /groups, /tokens, ...). Since
   cerebro-auth IS the source of truth, this validates Bearer tokens against ITS
   OWN `cerebro_auth.api_tokens` / `cerebro_auth.users` tables directly -- this is
   the one service in the ecosystem allowed to query its own schema this way for
   its own admin operations (the other three services replicate a *read-only*
   version of the same query pattern against these same tables, over the network
   or however they're wired, instead of managing their own token tables).

2. `resolve_effective_scope`: the reusable, documented implementation of the
   permission-resolution algorithm (token -> access_level -> allowed_modules +
   module_scopes). `get_principal` calls it for its own gating, and the `/login`
   route calls it to hand back a resolved identity to callers. This function is
   written to be easy to port: the other three services will each implement a
   read-only variant of the same steps against this same schema, so the SQL and
   the narrowing logic below are kept as literal and copy-adaptable as possible.

Root token bootstrap: same `secrets.compare_digest` pattern against
`settings.api_token` as the other three services, resolving to `access_level =
'admin'` with no DB row involved at all -- this has to work even before a single
user or token exists in the database (the bootstrap problem: how do you create
the first admin user/token without already having an admin token?).

Hashing/generation mechanism is identical to the other three services'
`auth.py` (`hashlib.sha256(...).hexdigest()` / `secrets.token_urlsafe(32)`);
only the prefix differs (`cbra_`, "cerebro-auth", vs. `cbrf_` for flows etc.).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

from cerebro_auth.config import Settings

TOKEN_PREFIX = "cbra_"
VALID_SCOPES = ("read", "write")
VALID_ACCESS_LEVELS = ("user", "owner", "admin")
ALL_MODULES = frozenset({"memory", "docs", "flows"})

ROOT_TOKEN_NAME = "root"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


# --------------------------------------------------------------------------- errors


class DuplicateTokenNameError(ValueError):
    """Raised when creating a token whose name already has an active token."""


class TokenNotFoundError(LookupError):
    """Raised when revoking a name with no active token."""


class InvalidScopesError(ValueError):
    """Raised when creating a token with an empty or unknown scope list."""


# --------------------------------------------------------------------------- Principal


@dataclass(frozen=True)
class Principal:
    """A resolved identity: who is calling, at what access level, and (once
    resolved through `resolve_effective_scope`) which ecosystem modules they can
    reach. `allowed_modules=None` only ever happens for `access_level == 'user'`
    whose token carries no `allowed_modules` of its own -- see
    `resolve_effective_scope`'s docstring, step 5.
    """

    name: str
    access_level: str
    allowed_modules: frozenset[str] | None
    module_scopes: dict[str, list[str]] = field(default_factory=dict)
    is_root: bool = False

    @property
    def is_admin(self) -> bool:
        return self.access_level == "admin"


# --------------------------------------------------------------------------- permission resolution


@dataclass(frozen=True)
class ResolvedScope:
    name: str
    access_level: str
    allowed_modules: frozenset[str] | None
    module_scopes: dict[str, list[str]]


def _merge_union(base_allowed: set[str], base_scopes: dict[str, set[str]], allowed_modules: list[str], module_scopes: dict[str, Any] | None) -> None:
    """Fold one group's `group_scopes` row into the running union (mutates in place).
    Used for the owner base case (step 4): the union across every group a user belongs to."""
    base_allowed.update(allowed_modules)
    for module, scopes in (module_scopes or {}).items():
        base_scopes.setdefault(module, set()).update(scopes)


def _narrow(
    base_allowed: frozenset[str],
    base_scopes: dict[str, list[str]],
    override_allowed: list[str] | None,
    override_scopes: dict[str, Any] | None,
) -> tuple[frozenset[str], dict[str, list[str]]]:
    """Apply a token's own `allowed_modules`/`module_scopes` as a narrowing cut on
    top of an inherited base -- never a widening one. Used for the owner case
    (step 4): a token that carries nothing of its own inherits the base as-is; a
    token that carries its own values keeps only the intersection.
    """
    if override_allowed is None and not override_scopes:
        return base_allowed, base_scopes

    allowed = base_allowed if override_allowed is None else (base_allowed & set(override_allowed))
    scopes: dict[str, list[str]] = {}
    for module in allowed:
        base_mod_scopes = set(base_scopes.get(module, []))
        if override_scopes and module in override_scopes:
            scopes[module] = sorted(base_mod_scopes & set(override_scopes[module]))
        else:
            scopes[module] = sorted(base_mod_scopes)
    return frozenset(allowed), scopes


async def resolve_effective_scope(pool: asyncpg.Pool, token_hash: str) -> ResolvedScope | None:
    """The permission-resolution algorithm. Given a token's hash, returns its
    fully resolved identity, or `None` if the token doesn't exist, is revoked, or
    (for a user-linked token) the owning user has been deactivated.

    This is the algorithm the other three services (cerebro-memory, cerebro-docs,
    cerebro-flows) will each replicate a READ-ONLY version of against these same
    `cerebro_auth` tables -- keep the steps below literal and easy to port.

    Step 1. Look up the token by hash; missing or `revoked_at IS NOT NULL` -> invalid.

    Step 2. If `user_id` is NOT NULL -> the associated user's `access_level`
    governs (the token's own `access_level` column is guaranteed NULL in this
    case by the `api_tokens` CHECK constraint -- nothing to ignore, it simply
    isn't there). If `user_id` IS NULL -> use `api_tokens.access_level` as-is
    (a service/root-adjacent token).

    Step 3. If resolved `access_level == 'admin'`: the token's own
    `allowed_modules` is a ceiling if present; if NULL, it inherits "all
    modules". `module_scopes` is never consulted for admin -- neither the
    token's own nor any inherited value.

    Step 4. If resolved `access_level == 'owner'`: the inherited base is the
    UNION of `group_scopes` (`allowed_modules` + `module_scopes`, merged) across
    every group the user belongs to (`user_groups` -> `group_scopes`). If the
    token carries its own `allowed_modules`/`module_scopes`, that's an
    additional narrowing on top of the base (never a widening). No groups (or no
    group has a `group_scopes` row) and no scope of its own on the token -> zero
    access to any module. That's a misconfiguration, not a permissive default:
    it must NOT silently resolve to "all modules".

    Step 5. Base case (`access_level == 'user'`): use the token's own
    `allowed_modules`/`module_scopes` as-is -- there is nothing to inherit at
    this level for the "which modules/contexts" gate.
    """
    token_row = await pool.fetchrow(
        """
        SELECT name, user_id, access_level, allowed_modules, module_scopes
        FROM api_tokens
        WHERE token_hash = $1 AND revoked_at IS NULL
        """,
        token_hash,
    )
    if token_row is None:
        return None

    token_allowed_modules: list[str] | None = token_row["allowed_modules"]
    token_module_scopes: dict[str, Any] = json.loads(token_row["module_scopes"]) if isinstance(token_row["module_scopes"], str) else (token_row["module_scopes"] or {})

    if token_row["user_id"] is not None:
        user_row = await pool.fetchrow(
            "SELECT name, access_level, revoked_at FROM users WHERE id = $1",
            token_row["user_id"],
        )
        if user_row is None or user_row["revoked_at"] is not None:
            # Deactivated (or deleted, though the FK is ON DELETE CASCADE so the
            # token would already be gone) account: the token itself may still be
            # active, but the identity behind it is not.
            return None
        name = user_row["name"]
        access_level = user_row["access_level"]
    else:
        # Service/root-adjacent token: its own `name` column is the identity.
        name = token_row["name"]
        access_level = token_row["access_level"]

    if access_level == "admin":
        allowed = ALL_MODULES if token_allowed_modules is None else (ALL_MODULES & set(token_allowed_modules))
        module_scopes: dict[str, list[str]] = {}  # never consulted for admin

    elif access_level == "owner":
        group_rows = await pool.fetch(
            """
            SELECT gs.allowed_modules, gs.module_scopes
            FROM user_groups ug
            JOIN group_scopes gs ON gs.group_id = ug.group_id
            WHERE ug.user_id = $1
            """,
            token_row["user_id"],
        )
        base_allowed: set[str] = set()
        base_scopes: dict[str, set[str]] = {}
        for row in group_rows:
            row_scopes = json.loads(row["module_scopes"]) if isinstance(row["module_scopes"], str) else (row["module_scopes"] or {})
            _merge_union(base_allowed, base_scopes, row["allowed_modules"], row_scopes)
        base_scopes_list = {mod: sorted(s) for mod, s in base_scopes.items()}

        allowed, module_scopes = _narrow(
            frozenset(base_allowed), base_scopes_list, token_allowed_modules, token_module_scopes
        )
        # No groups (or none with a group_scopes row) and no scope of its own on
        # the token: `allowed` is already the empty frozenset here -- zero access,
        # not "all modules". Nothing further to do; this is the documented
        # misconfiguration case from step 4.

    else:  # access_level == "user"
        allowed = frozenset(token_allowed_modules) if token_allowed_modules is not None else None
        module_scopes = token_module_scopes

    return ResolvedScope(name=name, access_level=access_level, allowed_modules=allowed, module_scopes=module_scopes)


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
            access_level="admin",
            allowed_modules=ALL_MODULES,
            module_scopes={},
            is_root=True,
        )

    token = authorization[len("Bearer ") :]
    pool: asyncpg.Pool = request.app.state.pool
    resolved = await resolve_effective_scope(pool, hash_token(token))
    if resolved is None:
        raise unauthorized

    return Principal(
        name=resolved.name,
        access_level=resolved.access_level,
        allowed_modules=resolved.allowed_modules,
        module_scopes=resolved.module_scopes,
        is_root=False,
    )


async def resolve_login(pool: asyncpg.Pool, settings: Settings, token: str) -> ResolvedScope | None:
    """The same resolution `get_principal` applies to an `Authorization: Bearer`
    header, applied instead to a token given directly in a request body -- used
    by `POST /login`, which by design carries the token in the body rather than
    a header (see the route's docstring in `api.py` for why). Includes the same
    root-token bootstrap short-circuit as `get_principal`, so the root token can
    log in even with zero rows in the database.
    """
    if secrets.compare_digest(token, settings.api_token):
        return ResolvedScope(name=ROOT_TOKEN_NAME, access_level="admin", allowed_modules=ALL_MODULES, module_scopes={})
    return await resolve_effective_scope(pool, hash_token(token))


async def require_admin(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"principal '{principal.name}' requires admin access (has: '{principal.access_level}')",
        )
    return principal


# --------------------------------------------------------------------------- token CRUD


async def create_api_token(
    pool: asyncpg.Pool,
    *,
    name: str,
    scopes: list[str],
    user_id: Any | None,
    access_level: str | None,
    allowed_modules: list[str] | None,
    module_scopes: dict[str, Any] | None,
    value: str | None = None,
) -> dict[str, Any]:
    invalid = sorted(set(scopes) - set(VALID_SCOPES))
    if invalid or not scopes:
        raise InvalidScopesError(
            f"invalid scopes {invalid}: must be a non-empty subset of {VALID_SCOPES}"
            if invalid
            else "at least one scope is required"
        )

    plaintext = value if value else generate_token()
    token_hash = hash_token(plaintext)
    module_scopes_json = json.dumps(module_scopes or {})
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO api_tokens (token_hash, name, user_id, scopes, access_level, allowed_modules, module_scopes)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, name, user_id, scopes, access_level, allowed_modules, module_scopes, created_at, revoked_at
            """,
            token_hash,
            name,
            user_id,
            scopes,
            access_level,
            allowed_modules,
            module_scopes_json,
        )
    except asyncpg.UniqueViolationError as exc:
        if value is not None:
            existing = await pool.fetchrow(
                """
                SELECT id, name, user_id, scopes, access_level, allowed_modules, module_scopes, created_at, revoked_at, token_hash
                FROM api_tokens WHERE name = $1 AND revoked_at IS NULL
                """,
                name,
            )
            if existing is not None and existing["token_hash"] == token_hash:
                data = dict(existing)
                data.pop("token_hash")
                data["module_scopes"] = json.loads(data["module_scopes"]) if isinstance(data["module_scopes"], str) else data["module_scopes"]
                return {**data, "token": plaintext}
        raise DuplicateTokenNameError(name) from exc

    data = dict(row)
    data["module_scopes"] = json.loads(data["module_scopes"]) if isinstance(data["module_scopes"], str) else data["module_scopes"]
    return {**data, "token": plaintext}


async def list_api_tokens(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, name, user_id, scopes, access_level, allowed_modules, module_scopes, created_at, revoked_at
        FROM api_tokens ORDER BY created_at
        """
    )
    out = []
    for row in rows:
        data = dict(row)
        data["module_scopes"] = json.loads(data["module_scopes"]) if isinstance(data["module_scopes"], str) else data["module_scopes"]
        out.append(data)
    return out


async def revoke_api_token(pool: asyncpg.Pool, name: str) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        UPDATE api_tokens SET revoked_at = now()
        WHERE name = $1 AND revoked_at IS NULL
        RETURNING id, name, user_id, scopes, access_level, allowed_modules, module_scopes, created_at, revoked_at
        """,
        name,
    )
    if row is None:
        raise TokenNotFoundError(name)
    data = dict(row)
    data["module_scopes"] = json.loads(data["module_scopes"]) if isinstance(data["module_scopes"], str) else data["module_scopes"]
    return data
