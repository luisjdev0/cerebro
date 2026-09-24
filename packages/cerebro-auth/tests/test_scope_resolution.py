"""Integration tests for `cerebro_auth.auth.resolve_effective_scope` (and the
`resolve_login` wrapper used by `POST /login`) -- the permission-resolution
algorithm that the other three services will each replicate a read-only
version of. Exercises every branch documented in its docstring: admin ignoring
module_scopes, owner inheriting the union of its groups' group_scopes (with a
token narrowing that base, never widening it), the base "user" case using its
own token scope as-is, the owner-with-nothing-inherited zero-access edge case,
and the root token bootstrapping with no matching row in the database at all.

Talks to Postgres directly (inserting rows with plain SQL/`create_api_token`,
no HTTP layer) so each scenario can set up exactly the rows it needs. Skips
itself, same as every other integration test in this repo, if Postgres isn't
reachable.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest
from urllib.parse import urlsplit, urlunsplit

from cerebro_auth.auth import (
    ALL_MODULES,
    create_api_token,
    hash_token,
    resolve_effective_scope,
    resolve_login,
)
from cerebro_auth.config import Settings, get_settings
from cerebro_auth.db import apply_migrations, create_pool


def _db_reachable(dsn: str) -> bool:
    async def _check() -> None:
        conn = await asyncpg.connect(dsn=dsn, timeout=8)
        await conn.close()

    try:
        asyncio.run(_check())
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_if_no_db():
    if not _db_reachable(get_settings().database_url):
        pytest.skip("Postgres not reachable at DATABASE_URL - run `docker compose up -d` first")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _pool() -> asyncpg.Pool:
    settings = get_settings()
    pool = await create_pool(settings)
    await apply_migrations(pool, settings)
    return pool


async def _create_user(pool: asyncpg.Pool, *, access_level: str) -> tuple[str, "uuid.UUID"]:
    name = _unique("scope-user")
    row = await pool.fetchrow(
        "INSERT INTO users (name, access_level) VALUES ($1, $2) RETURNING id", name, access_level
    )
    return name, row["id"]


async def _create_group_with_scopes(
    pool: asyncpg.Pool, *, allowed_modules: list[str], module_scopes: dict | None = None
) -> "uuid.UUID":
    slug = _unique("scope-group")
    group = await pool.fetchrow(
        "INSERT INTO groups (slug, name) VALUES ($1, $2) RETURNING id", slug, slug
    )
    await pool.execute(
        "INSERT INTO group_scopes (group_id, allowed_modules, module_scopes) VALUES ($1, $2, $3)",
        group["id"],
        allowed_modules,
        json.dumps(module_scopes or {}),
    )
    return group["id"]


async def _add_member(pool: asyncpg.Pool, user_id, group_id) -> None:
    await pool.execute("INSERT INTO user_groups (user_id, group_id) VALUES ($1, $2)", user_id, group_id)


async def _issue_token(
    pool: asyncpg.Pool,
    *,
    user_id=None,
    access_level: str | None = None,
    allowed_modules: list[str] | None = None,
    module_scopes: dict | None = None,
) -> str:
    created = await create_api_token(
        pool,
        name=_unique("scope-token"),
        scopes=["read"],
        user_id=user_id,
        access_level=access_level,
        allowed_modules=allowed_modules,
        module_scopes=module_scopes,
    )
    return created["token"]


# --------------------------------------------------------------------------- admin


async def test_admin_ceiling_is_honored_and_module_scopes_ignored():
    pool = await _pool()
    try:
        token = await _issue_token(
            pool,
            access_level="admin",
            allowed_modules=["memory"],
            module_scopes={"memory": ["read"]},  # must be ignored entirely for admin
        )
        resolved = await resolve_effective_scope(pool, hash_token(token))
        assert resolved is not None
        assert resolved.access_level == "admin"
        assert resolved.allowed_modules == frozenset({"memory"})
        assert resolved.module_scopes == {}
    finally:
        await pool.close()


async def test_admin_with_no_allowed_modules_inherits_all():
    pool = await _pool()
    try:
        token = await _issue_token(pool, access_level="admin", allowed_modules=["memory", "docs", "flows"])
        resolved = await resolve_effective_scope(pool, hash_token(token))
        assert resolved is not None
        assert resolved.allowed_modules == ALL_MODULES
    finally:
        await pool.close()


# --------------------------------------------------------------------------- owner


async def test_owner_inherits_union_of_group_scopes():
    pool = await _pool()
    try:
        _, user_id = await _create_user(pool, access_level="owner")
        group_a = await _create_group_with_scopes(
            pool, allowed_modules=["memory"], module_scopes={"memory": ["read"]}
        )
        group_b = await _create_group_with_scopes(
            pool, allowed_modules=["docs"], module_scopes={"docs": ["write"]}
        )
        await _add_member(pool, user_id, group_a)
        await _add_member(pool, user_id, group_b)

        token = await _issue_token(pool, user_id=user_id)  # no narrowing of its own
        resolved = await resolve_effective_scope(pool, hash_token(token))

        assert resolved is not None
        assert resolved.access_level == "owner"
        assert resolved.allowed_modules == frozenset({"memory", "docs"})
        assert resolved.module_scopes == {"memory": ["read"], "docs": ["write"]}
    finally:
        await pool.close()


async def test_owner_token_narrows_but_never_widens_the_group_union():
    pool = await _pool()
    try:
        _, user_id = await _create_user(pool, access_level="owner")
        group = await _create_group_with_scopes(
            pool, allowed_modules=["memory", "docs"], module_scopes={"memory": ["read", "write"]}
        )
        await _add_member(pool, user_id, group)

        # Token narrows to just 'memory' with only 'read' -- a subset of the base.
        token = await _issue_token(
            pool, user_id=user_id, allowed_modules=["memory"], module_scopes={"memory": ["read"]}
        )
        resolved = await resolve_effective_scope(pool, hash_token(token))
        assert resolved is not None
        assert resolved.allowed_modules == frozenset({"memory"})
        assert resolved.module_scopes == {"memory": ["read"]}

        # A token claiming a module outside the inherited base ('flows') never
        # widens it -- the intersection with the base is empty.
        token2 = await _issue_token(pool, user_id=user_id, allowed_modules=["flows"])
        resolved2 = await resolve_effective_scope(pool, hash_token(token2))
        assert resolved2 is not None
        assert resolved2.allowed_modules == frozenset()
    finally:
        await pool.close()


async def test_owner_with_no_groups_and_no_token_scope_is_zero_access():
    pool = await _pool()
    try:
        _, user_id = await _create_user(pool, access_level="owner")
        token = await _issue_token(pool, user_id=user_id)  # no groups, no scope of its own

        resolved = await resolve_effective_scope(pool, hash_token(token))
        assert resolved is not None
        assert resolved.access_level == "owner"
        # Zero access is an explicit empty set -- never None ("all modules" for
        # 'user'-only tokens is a different case, see below) and never ALL_MODULES.
        assert resolved.allowed_modules == frozenset()
        assert resolved.module_scopes == {}
    finally:
        await pool.close()


# --------------------------------------------------------------------------- user (base case)


async def test_user_level_uses_its_own_token_scope_as_is():
    pool = await _pool()
    try:
        _, user_id = await _create_user(pool, access_level="user")
        token = await _issue_token(
            pool, user_id=user_id, allowed_modules=["flows"], module_scopes={"flows": ["write"]}
        )
        resolved = await resolve_effective_scope(pool, hash_token(token))
        assert resolved is not None
        assert resolved.access_level == "user"
        assert resolved.allowed_modules == frozenset({"flows"})
        assert resolved.module_scopes == {"flows": ["write"]}
    finally:
        await pool.close()


async def test_user_level_with_no_allowed_modules_passes_through_none():
    """Documented edge case (auth.py, resolve_effective_scope step 5): there is
    nothing to inherit at the 'user' level, so a token with no allowed_modules of
    its own resolves to `None`, not an empty set and not "all modules"."""
    pool = await _pool()
    try:
        _, user_id = await _create_user(pool, access_level="user")
        token = await _issue_token(pool, user_id=user_id)
        resolved = await resolve_effective_scope(pool, hash_token(token))
        assert resolved is not None
        assert resolved.access_level == "user"
        assert resolved.allowed_modules is None
    finally:
        await pool.close()


# --------------------------------------------------------------------------- revocation / deactivation


async def test_revoked_token_resolves_to_none():
    pool = await _pool()
    try:
        _, user_id = await _create_user(pool, access_level="user")
        token = await _issue_token(pool, user_id=user_id, allowed_modules=["memory"])
        await pool.execute("UPDATE api_tokens SET revoked_at = now() WHERE token_hash = $1", hash_token(token))

        resolved = await resolve_effective_scope(pool, hash_token(token))
        assert resolved is None
    finally:
        await pool.close()


async def test_deactivated_user_resolves_to_none_even_with_active_token():
    pool = await _pool()
    try:
        _, user_id = await _create_user(pool, access_level="user")
        token = await _issue_token(pool, user_id=user_id, allowed_modules=["memory"])
        await pool.execute("UPDATE users SET revoked_at = now() WHERE id = $1", user_id)

        resolved = await resolve_effective_scope(pool, hash_token(token))
        assert resolved is None
    finally:
        await pool.close()


# --------------------------------------------------------------------------- root bootstrap


async def test_root_token_bootstraps_with_zero_rows_in_the_database():
    """The root token must resolve successfully even before a single user or
    token row exists anywhere -- that's the bootstrap problem this token exists
    to solve. Proven here against a genuinely empty, freshly migrated database
    (not just "no rows relevant to this test" in the shared test DB, which by
    this point in the suite has plenty of rows from other tests).
    """
    base_settings = get_settings()
    base_dsn = base_settings.database_url
    maintenance_dsn = urlunsplit(urlsplit(base_dsn)._replace(path="/postgres"))
    bootstrap_db = f"cerebro_test_bootstrap_{uuid.uuid4().hex[:8]}"
    bootstrap_dsn = urlunsplit(urlsplit(base_dsn)._replace(path=f"/{bootstrap_db}"))

    conn = await asyncpg.connect(dsn=maintenance_dsn, timeout=8)
    try:
        await conn.execute(f'CREATE DATABASE "{bootstrap_db}"')
    finally:
        await conn.close()

    try:
        settings = Settings(
            database_url=bootstrap_dsn,
            api_token=base_settings.api_token,
            migrations_dir=base_settings.migrations_dir,
        )
        pool = await create_pool(settings)
        try:
            await apply_migrations(pool, settings)

            assert await pool.fetchval("SELECT count(*) FROM users") == 0
            assert await pool.fetchval("SELECT count(*) FROM api_tokens") == 0

            resolved = await resolve_login(pool, settings, settings.api_token)
            assert resolved is not None
            assert resolved.name == "root"
            assert resolved.access_level == "admin"
            assert resolved.allowed_modules == ALL_MODULES

            # A non-root, non-existent token against the same empty database is
            # correctly rejected rather than crashing on missing rows.
            assert await resolve_login(pool, settings, "cbra_definitely-not-a-real-token") is None
        finally:
            await pool.close()
    finally:
        conn = await asyncpg.connect(dsn=maintenance_dsn, timeout=8)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{bootstrap_db}" WITH (FORCE)')
        finally:
            await conn.close()
