"""Isolates this package's tests against an ephemeral Postgres database
(`cerebro_test`), separate from the real development database (`knowledgeos`).

Without this, the integration test suite (test_auth.py, test_supersedence.py,
test_graph.py, ...) writes directly against the local development database
via `get_settings().database_url`, accumulating `test-*` rows across
sessions -- see `luisjdev-pendientes/ecosistema-cerebro` (Infrastructure /
Testing) in cerebro-docs.

`cerebro_test` is dropped and recreated at the start of the pytest session and
`DATABASE_URL` is overwritten in the process environment BEFORE any test
module is imported -- that's why this lives in `pytest_configure`, NOT in a
fixture (not even a `session`+`autouse` fixture): `cerebro_memory/api.py`
has `app = create_app()` at MODULE level (needed for
`uvicorn cerebro_memory.main:app` in production), which calls
`get_settings()` (with `@lru_cache`) at the moment the module is IMPORTED. A
fixture -- no matter how early it runs -- executes during the "run" phase,
which in pytest happens AFTER the "collection" phase (where all test
modules are imported, and with them `cerebro_memory.api`) -- by then
`get_settings()`'s cache would have already been fixed to the real
`DATABASE_URL` from `.env`, no matter that the fixture later overwrites the
environment variable. `pytest_configure` DOES run before collection, in time
to beat that import.

Since `create_app()` already runs the migrations in its startup `lifespan` (see
db.py/api.py), the ephemeral database migrates itself on the first `TestClient` of
each module -- nothing else needs to be touched, EXCEPT for the shared `cerebro_auth`
schema (unified auth, `packages/cerebro-auth`): `get_principal` (src/cerebro_memory/
auth.py) reads `cerebro_auth.api_tokens`/`users`/`user_groups`/`group_scopes` for any
non-root token, so that schema must exist in `cerebro_test` BEFORE this package's own
migrations/tests run -- done here, once, via `cerebro_auth.db.apply_migrations`
(the same function/entry point the `cerebro-auth` service itself uses to migrate).
Tests that only exercise the root-token bootstrap don't strictly need this, but any
test that inserts a non-root token (see test_auth.py) does.

Requires that the `DATABASE_URL` role can create/drop databases (the same role
that creates the instance via `POSTGRES_USER` in compose.yaml already has that
permission, being the `initdb` role). Not meant to run in parallel with another
suite from this ecosystem against the same Postgres -- they would compete for
the same database name.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg

logger = logging.getLogger("cerebro_memory.tests.conftest")

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


async def _apply_cerebro_auth_schema(test_dsn: str) -> None:
    """Bring up the shared `cerebro_auth` schema in the ephemeral test database
    before this package's own migrations touch it. Matches `cerebro-auth`'s real
    signature (same pattern as every other service in this monorepo): a pool +
    settings, not a bare DSN string.
    """
    from cerebro_auth.config import Settings as AuthSettings
    from cerebro_auth.db import apply_migrations as apply_cerebro_auth_migrations
    from cerebro_auth.db import create_pool as create_auth_pool

    settings = AuthSettings(database_url=test_dsn)
    pool = await create_auth_pool(settings)
    try:
        await apply_cerebro_auth_migrations(pool, settings)
    finally:
        await pool.close()


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    base_dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    test_dsn = _with_database(base_dsn, TEST_DB_NAME)

    try:
        asyncio.run(_recreate_test_database(_with_database(base_dsn, "postgres")))
    except Exception:
        # Postgres unreachable: each test skips itself via its own
        # _db_reachable() against DATABASE_URL, same as before this hook.
        return

    # Point DATABASE_URL at the (now empty) ephemeral database BEFORE attempting the
    # cerebro_auth schema below - if that next step fails (e.g. `cerebro-auth` isn't
    # installed yet), tests must still fail loudly against the empty cerebro_test
    # database rather than silently falling through to the real dev database that
    # base_dsn/_DEFAULT_DSN points at.
    os.environ["DATABASE_URL"] = test_dsn

    try:
        asyncio.run(_apply_cerebro_auth_schema(test_dsn))
    except Exception:
        # cerebro_auth not installed/reachable yet: leave DATABASE_URL pointed at
        # cerebro_test regardless - tests that need cerebro_auth.* tables will error
        # loudly (not silently run against a real database) until that package lands.
        logger.warning("could not apply the cerebro_auth schema to %s", TEST_DB_NAME, exc_info=True)
