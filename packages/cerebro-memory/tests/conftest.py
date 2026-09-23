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
each module -- nothing else needs to be touched.

Requires that the `DATABASE_URL` role can create/drop databases (the same role
that creates the instance via `POSTGRES_USER` in compose.yaml already has that
permission, being the `initdb` role). Not meant to run in parallel with another
suite from this ecosystem against the same Postgres -- they would compete for
the same database name.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg

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


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    base_dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    test_dsn = _with_database(base_dsn, TEST_DB_NAME)

    try:
        asyncio.run(_recreate_test_database(_with_database(base_dsn, "postgres")))
    except Exception:
        # Postgres unreachable: each test skips itself via its own
        # _db_reachable() against DATABASE_URL, same as before this hook.
        return

    os.environ["DATABASE_URL"] = test_dsn
