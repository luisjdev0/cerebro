"""Isolates this package's tests against an ephemeral Postgres database
(`cerebro_test`), separate from the real development database (`knowledgeos`).

See the equivalent conftest.py in packages/cerebro-memory/tests/ for the
full detail of the mechanism -- this one is identical except for the default
DSN, which here matches `cerebro_docs.config.Settings.database_url`
(same Postgres instance shared between both services, different schema).

Important: this lives in `pytest_configure`, NOT in a fixture (not even a
`session`+`autouse` one) -- `cerebro_docs/api.py` has `app = create_app()` at
MODULE level (needed for `uvicorn cerebro_docs.main:app` in
production), which calls `get_settings()` (with `@lru_cache`) when the
module is IMPORTED, during pytest's "collection" phase -- BEFORE any
fixture gets to run. Only `pytest_configure` runs in time to beat
that import.

Auth unification (ecosistema-cerebro.md SS13): `auth.py`'s `get_principal` now
reads `cerebro_auth.api_tokens`/`users`/`groups`/etc. instead of a local table, so
the ephemeral `cerebro_test` database needs the `cerebro_auth` schema applied
BEFORE this package's own migrations/tests run -- see `_apply_auth_schema` below,
called from `pytest_configure` right after the database is (re)created. Tests that
need a non-root, scoped token (see tests/test_auth.py) insert directly into
`cerebro_auth.api_tokens`/`users`/etc. via raw SQL fixtures, since token
management no longer has an endpoint in this service at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg

logger = logging.getLogger("cerebro_docs.conftest")

TEST_DB_NAME = "cerebro_test"
_DEFAULT_DSN = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"


def _with_database(dsn: str, database: str) -> str:
    return urlunsplit(urlsplit(dsn)._replace(path=f"/{database}"))


async def _recreate_test_database(maintenance_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=maintenance_dsn, timeout=8)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


async def _apply_auth_schema(test_dsn: str) -> None:
    """Applies the shared `cerebro_auth` schema (owned by `packages/cerebro-auth`,
    built in parallel with this change) to the ephemeral test database.

    ASSUMPTION (flag for reconciliation if wrong): `cerebro_auth.db` exposes
    `create_pool(settings) -> asyncpg.Pool` and
    `apply_migrations(pool, settings) -> list[str]`, matching the exact pattern
    used by every other service in this monorepo (see
    `cerebro_flows.db.apply_migrations` and this package's own `db.py`) -- NOT the
    `apply_migrations(dsn: str) -> None` shape floated when this task was
    specified, which doesn't match anything actually in the codebase.
    """
    from cerebro_auth.config import Settings as AuthSettings
    from cerebro_auth.db import apply_migrations as apply_auth_migrations
    from cerebro_auth.db import create_pool as create_auth_pool

    settings = AuthSettings(database_url=test_dsn)
    pool = await create_auth_pool(settings)
    try:
        await apply_auth_migrations(pool, settings)
    finally:
        await pool.close()


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    base_dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    test_dsn = _with_database(base_dsn, TEST_DB_NAME)

    try:
        asyncio.run(_recreate_test_database(_with_database(base_dsn, "postgres")))
    except Exception:
        return

    os.environ["DATABASE_URL"] = test_dsn

    try:
        asyncio.run(_apply_auth_schema(test_dsn))
    except Exception:
        # cerebro-auth not installed/ready yet, or its DB unreachable: don't fail
        # collection over it (same "best effort" spirit as the block above) --
        # tests that actually need cerebro_auth's tables will fail on their own
        # with a clear error instead of silently no-op'ing.
        logger.warning("could not apply the cerebro_auth schema to the test database", exc_info=True)
