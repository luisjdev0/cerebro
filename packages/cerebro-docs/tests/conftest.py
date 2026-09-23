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
        return

    os.environ["DATABASE_URL"] = test_dsn
