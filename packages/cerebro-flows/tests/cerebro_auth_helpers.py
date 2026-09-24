"""Test-only helpers for populating the shared `cerebro_auth` schema directly via
raw SQL. Token/user/group management no longer lives in this service (see
`cerebro_flows.auth`'s docstring) -- it's owned by the sibling `cerebro-auth`
package, so tests that need a non-root, non-admin token must set up
users/groups/tokens by hand instead of calling a local `/tokens` endpoint.

Each helper opens its OWN standalone connection (via a DSN, not a shared pool) and
closes it before returning -- these are called from plain `asyncio.run(...)` blocks
inside otherwise-sync test functions, each of which gets its own fresh event loop.
`client.app.state.pool` (the app's own pool, created inside the TestClient's
lifespan on a DIFFERENT event loop) must never be reused here: asyncpg pools/
connections are bound to the loop they were created on, and crossing loops raises
`RuntimeError: ... attached to a different loop` / `InterfaceError: cannot perform
operation: another operation is in progress`. Same pattern as the sibling
`_run_sql` helper in `packages/cerebro-memory/tests/test_auth.py`.

Shared by `test_auth.py` and `test_flows.py`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

import asyncpg

from cerebro_flows.auth import generate_token, hash_token


async def insert_user(
    dsn: str,
    *,
    access_level: str = "user",
    name: str = "Test User",
    email: str | None = None,
) -> uuid.UUID:
    email = email or f"{uuid.uuid4().hex}@test.local"
    conn = await asyncpg.connect(dsn=dsn, timeout=8)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO cerebro_auth.users (name, email, access_level, password_hash)
            VALUES ($1, $2, $3, 'x')
            RETURNING id
            """,
            name,
            email,
            access_level,
        )
    finally:
        await conn.close()
    return row["id"]


async def insert_group(dsn: str, *, slug: str | None = None, name: str = "Test Group") -> uuid.UUID:
    slug = slug or f"group-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(dsn=dsn, timeout=8)
    try:
        row = await conn.fetchrow(
            "INSERT INTO cerebro_auth.groups (slug, name) VALUES ($1, $2) RETURNING id",
            slug,
            name,
        )
    finally:
        await conn.close()
    return row["id"]


async def add_user_to_group(dsn: str, user_id: uuid.UUID, group_id: uuid.UUID) -> None:
    conn = await asyncpg.connect(dsn=dsn, timeout=8)
    try:
        await conn.execute(
            "INSERT INTO cerebro_auth.user_groups (user_id, group_id) VALUES ($1, $2)",
            user_id,
            group_id,
        )
    finally:
        await conn.close()


async def set_group_scopes(
    dsn: str,
    group_id: uuid.UUID,
    *,
    allowed_modules: Iterable[str] | None,
    module_scopes: dict[str, Any] | None = None,
) -> None:
    conn = await asyncpg.connect(dsn=dsn, timeout=8)
    try:
        await conn.execute(
            """
            INSERT INTO cerebro_auth.group_scopes (group_id, allowed_modules, module_scopes)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (group_id) DO UPDATE
                SET allowed_modules = EXCLUDED.allowed_modules, module_scopes = EXCLUDED.module_scopes
            """,
            group_id,
            list(allowed_modules) if allowed_modules is not None else None,
            json.dumps(module_scopes) if module_scopes is not None else "{}",
        )
    finally:
        await conn.close()


async def insert_token(
    dsn: str,
    *,
    name: str,
    user_id: uuid.UUID | None = None,
    scopes: Iterable[str] = ("read", "write"),
    access_level: str | None = None,
    allowed_modules: Iterable[str] | None = None,
    module_scopes: dict[str, Any] | None = None,
) -> str:
    """Inserts a row directly into `cerebro_auth.api_tokens` and returns the
    plaintext bearer token (only its hash is stored, same as production)."""
    plaintext = generate_token()
    conn = await asyncpg.connect(dsn=dsn, timeout=8)
    try:
        await conn.execute(
            """
            INSERT INTO cerebro_auth.api_tokens
                (token_hash, name, user_id, scopes, access_level, allowed_modules, module_scopes)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            hash_token(plaintext),
            name,
            user_id,
            list(scopes),
            access_level,
            list(allowed_modules) if allowed_modules is not None else None,
            json.dumps(module_scopes) if module_scopes is not None else "{}",
        )
    finally:
        await conn.close()
    return plaintext


async def revoke_token(dsn: str, plaintext_token: str) -> None:
    conn = await asyncpg.connect(dsn=dsn, timeout=8)
    try:
        await conn.execute(
            "UPDATE cerebro_auth.api_tokens SET revoked_at = now() WHERE token_hash = $1",
            hash_token(plaintext_token),
        )
    finally:
        await conn.close()
