"""Isolates this package's tests against an ephemeral Postgres database
(`cerebro_test`), separate from the real development database. See the
equivalent conftest.py in packages/cerebro-flows/tests/ (or cerebro-memory's)
for the full detail of the mechanism and why it lives in `pytest_configure`
(not a fixture: `cerebro_auth/api.py` instantiates `app = create_app()` at
module level, which caches `get_settings()` on import during pytest's
collection phase, before any fixture runs).

No Redis here, unlike cerebro-flows's conftest.py -- cerebro-auth has no
ephemeral/mutable state of its own.
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
        # _db_reachable(), same as before this hook.
        return

    os.environ["DATABASE_URL"] = test_dsn
