"""asyncpg pool + tiny migration runner.

Migrations are plain .sql files in `db/migrations/`, applied in filename order,
tracked in a `schema_migrations` table.

Difference from `cerebro_memory.db`: cerebro-memory's runner relies on the pool's
search_path (which falls back to `public` when `cerebro_memory` doesn't exist yet)
because it had to keep working against pre-existing data in `public`. cerebro-docs
has no such history, so its runner is stricter: it explicitly creates the
`cerebro_docs` schema and its own `schema_migrations` table with FULLY QUALIFIED
names *before* running anything else, so a fresh install lands entirely inside
`cerebro_docs` in a single boot without depending on the search_path being right yet
(see ecosistema-cerebro.md SS6 - "a fresh install must leave EVERYTHING in
cerebro_docs in a single boot").
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

from cerebro_docs.config import Settings

logger = logging.getLogger("cerebro_docs.db")

# package root, two levels above this file (src/cerebro_docs/db.py -> packages/cerebro-docs)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


async def create_pool(settings: Settings) -> asyncpg.Pool:
    # cerebro_docs first, public after (where pgcrypto lives if another
    # install - e.g. cerebro-memory - already registered it there). On a fresh DB
    # where `cerebro_docs` doesn't exist yet, apply_migrations() creates it before
    # any migration runs (see the module docstring), so this search_path is
    # already valid by the time the first connection actually gets used.
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=10,
        server_settings={"search_path": "cerebro_docs, public"},
    )


async def apply_migrations(pool: asyncpg.Pool, settings: Settings) -> list[str]:
    """Apply any pending .sql migrations. Returns the list of versions just applied."""
    migrations_dir = PACKAGE_ROOT / settings.migrations_dir
    applied: list[str] = []

    async with pool.acquire() as conn:
        # Deliberately qualified names (cerebro_docs.schema_migrations, not
        # bare schema_migrations): on a fresh install the `cerebro_docs` schema
        # doesn't exist yet at the moment this connection was acquired, so we can't
        # rely on the search_path for the runner's own control table - we create it
        # ourselves, qualified, before touching anything else.
        await conn.execute("CREATE SCHEMA IF NOT EXISTS cerebro_docs")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cerebro_docs.schema_migrations (
                version     TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        already_applied = {
            row["version"]
            for row in await conn.fetch("SELECT version FROM cerebro_docs.schema_migrations")
        }

        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.name
            if version in already_applied:
                continue

            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO cerebro_docs.schema_migrations (version) VALUES ($1)", version
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
