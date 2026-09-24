"""Isolates this package's tests against an ephemeral Postgres database
(`cerebro_test`), separate from the real development database. See the equivalent
conftest.py in packages/cerebro-memory/tests/ for the full detail of the mechanism
and why it lives in `pytest_configure` (not a fixture: `cerebro_flows/api.py`
instantiates `app = create_app()` at module level, which caches `get_settings()` on
import during pytest's collection phase, before any fixture runs).

Auth now reads the shared `cerebro_auth` schema (see `cerebro_flows.auth`), so this
ephemeral database also needs that schema to exist before this package's own tests
(and its own migrations, applied later via the app's lifespan) run. We reuse
`cerebro-auth`'s own migration runner (`cerebro_auth.db.apply_migrations`) for that
-- it is a `[dev]` extra of this package, and its runner follows the same pattern as
this package's own `cerebro_flows.db.apply_migrations` (creates its schema and
`schema_migrations` table, then applies pending `.sql` files).

`REDIS_URL` is also overridden here to a dedicated logical Redis database (`/15`,
the last of the default 16) so it doesn't collide with the normal use of `/0` in
development -- Redis has no concept of an "ephemeral database that gets recreated",
so instead of dropping/creating like with Postgres, each test session runs
`FLUSHDB` on that dedicated database on startup.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg

TEST_DB_NAME = "cerebro_test"
_DEFAULT_DSN = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_TEST_REDIS_DB = "15"


def _with_database(dsn: str, database: str) -> str:
    return urlunsplit(urlsplit(dsn)._replace(path=f"/{database}"))


async def _recreate_test_database(maintenance_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=maintenance_dsn, timeout=8)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


async def _apply_cerebro_auth_migrations(test_dsn: str) -> None:
    # Deferred import: only needed for tests, and only once the ephemeral database
    # above exists. `cerebro-auth` is a `[dev]` extra of this package (see
    # pyproject.toml) built by the sibling `packages/cerebro-auth` package. Its
    # migration runner takes a pool + settings, same pattern as this package's own
    # `cerebro_flows.db.apply_migrations`, not a bare DSN string.
    from cerebro_auth.config import Settings as AuthSettings
    from cerebro_auth.db import apply_migrations as apply_auth_migrations
    from cerebro_auth.db import create_pool as create_auth_pool

    settings = AuthSettings(database_url=test_dsn)
    pool = await create_auth_pool(settings)
    try:
        await apply_auth_migrations(pool, settings)
    finally:
        await pool.close()


def _test_redis_url() -> str:
    base = os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL)
    return urlunsplit(urlsplit(base)._replace(path=f"/{_TEST_REDIS_DB}"))


async def _flush_test_redis(redis_url: str) -> None:
    import redis.asyncio as redis

    client = redis.from_url(redis_url)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    base_dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    test_dsn = _with_database(base_dsn, TEST_DB_NAME)
    test_redis_url = _test_redis_url()

    try:
        asyncio.run(_recreate_test_database(_with_database(base_dsn, "postgres")))
        # cerebro_auth schema must exist before this package's own migrations run
        # (the app's lifespan applies cerebro_flows's own migrations later, via
        # cerebro_flows.db.apply_migrations, when the `client` fixture starts it).
        asyncio.run(_apply_cerebro_auth_migrations(test_dsn))
        asyncio.run(_flush_test_redis(test_redis_url))
    except Exception:
        # Postgres/Redis unreachable, or cerebro-auth not installed/importable:
        # each test skips itself via its own _db_reachable(), same as before this
        # hook.
        return

    os.environ["DATABASE_URL"] = test_dsn
    os.environ["REDIS_URL"] = test_redis_url
