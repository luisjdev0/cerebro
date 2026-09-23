"""Token auth with scopes - espejo exacto de `cerebro_docs.auth` (ver su docstring
para el detalle del razonamiento), aqui con `allowed_categories` sobre categorias de
flujo. NO es la "gestion de usuarios con scopes" que el documento de diseno deja
fuera de alcance (esa es sobre entidades de usuario con jerarquia user/owner/admin) -
es el mismo mecanismo de tokens ya usado en memory/docs, reusado por consistencia.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status

TOKEN_PREFIX = "cbrf_"
VALID_SCOPES = ("read", "write", "admin")

ROOT_TOKEN_NAME = "root"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    name: str
    scopes: frozenset[str]
    allowed_categories: frozenset[str] | None
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
            allowed_categories=None,
            is_root=True,
        )

    token = authorization[len("Bearer ") :]
    pool: asyncpg.Pool = request.app.state.pool
    row = await pool.fetchrow(
        "SELECT name, scopes, allowed_categories FROM api_tokens WHERE token_hash = $1 AND revoked_at IS NULL",
        hash_token(token),
    )
    if row is None:
        raise unauthorized

    allowed = (
        frozenset(row["allowed_categories"]) if row["allowed_categories"] is not None else None
    )
    return Principal(
        name=row["name"],
        scopes=frozenset(row["scopes"]),
        allowed_categories=allowed,
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


# --------------------------------------------------------------------------- token CRUD


async def create_api_token(
    pool: asyncpg.Pool,
    *,
    name: str,
    scopes: list[str],
    allowed_categories: list[str] | None,
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
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO api_tokens (token_hash, name, scopes, allowed_categories)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, scopes, allowed_categories, created_at, revoked_at
            """,
            token_hash,
            name,
            scopes,
            allowed_categories,
        )
    except asyncpg.UniqueViolationError as exc:
        if value is not None:
            existing = await pool.fetchrow(
                """
                SELECT id, name, scopes, allowed_categories, created_at, revoked_at, token_hash
                FROM api_tokens WHERE name = $1 AND revoked_at IS NULL
                """,
                name,
            )
            if existing is not None and existing["token_hash"] == token_hash:
                data = dict(existing)
                data.pop("token_hash")
                return {**data, "token": plaintext}
        raise DuplicateTokenNameError(name) from exc

    return {**dict(row), "token": plaintext}


async def list_api_tokens(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, name, scopes, allowed_categories, created_at, revoked_at FROM api_tokens ORDER BY created_at"
    )


async def revoke_api_token(pool: asyncpg.Pool, name: str) -> asyncpg.Record:
    row = await pool.fetchrow(
        """
        UPDATE api_tokens SET revoked_at = now()
        WHERE name = $1 AND revoked_at IS NULL
        RETURNING id, name, scopes, allowed_categories, created_at, revoked_at
        """,
        name,
    )
    if row is None:
        raise TokenNotFoundError(name)
    return row
