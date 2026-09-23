"""Isolates this package's tests against an ephemeral Postgres database
(`cerebro_test`), separate from the real development database (`knowledgeos`).

See the equivalent conftest.py in packages/cerebro-memory/tests/ for the
full detail of the mechanism, including why this lives in
`pytest_configure` and not in a fixture (`cerebro_memory.api`/`cerebro_docs.api`
instantiate `app = create_app()` at module level, on import -- during pytest's
"collection" phase, before any fixture runs).

It matters here too because test_token_transversal_integration.py spawns
real `cerebro_memory.main`/`cerebro_docs.main` subprocesses inheriting
`os.environ` (`env = os.environ.copy()`) -- since `DATABASE_URL` is already
overwritten in the pytest process before that fixture starts the
subprocesses, both services inherit the ephemeral database automatically, without
touching that file. The `itest-*` prefixes seen in the development database
today come precisely from that test.
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
