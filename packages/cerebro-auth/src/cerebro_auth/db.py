"""asyncpg pool + tiny migration runner.

Same "strict" runner as `cerebro_flows.db` (see its docstring): it creates the
`cerebro_auth` schema and its own qualified `schema_migrations` table BEFORE
running any migration, so a fresh install lands entirely in `cerebro_auth` in a
single startup without depending on the search_path already being correct.

`apply_migrations()` takes nothing more specific than a pool and a `Settings`
instance carrying `migrations_dir` -- it is deliberately kept importable
standalone (`from cerebro_auth.db import apply_migrations`) by OTHER packages'
test suites later in the plan (they will want to stand up the `cerebro_auth`
schema in their own ephemeral test database to exercise a read-only replica of
the permission-resolution logic against real rows). Nothing here depends on any
cerebro-auth-specific concept beyond the DSN/pool the caller already has.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

from cerebro_auth.config import Settings

logger = logging.getLogger("cerebro_auth.db")

# package root, two levels above this file (src/cerebro_auth/db.py -> packages/cerebro-auth)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=10,
        server_settings={"search_path": "cerebro_auth, public"},
    )


async def apply_migrations(pool: asyncpg.Pool, settings: Settings) -> list[str]:
    """Apply any pending .sql migrations. Returns the list of versions just applied."""
    migrations_dir = PACKAGE_ROOT / settings.migrations_dir
    applied: list[str] = []

    async with pool.acquire() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS cerebro_auth")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cerebro_auth.schema_migrations (
                version     TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        already_applied = {
            row["version"]
            for row in await conn.fetch("SELECT version FROM cerebro_auth.schema_migrations")
        }

        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.name
            if version in already_applied:
                continue

            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO cerebro_auth.schema_migrations (version) VALUES ($1)", version
                )
            logger.info("applied migration %s", version)
            applied.append(version)

    return applied


async def check_health(pool: asyncpg.Pool) -> bool:
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        logger.exception("health check failed")
        return False
