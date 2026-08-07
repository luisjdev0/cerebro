"""asyncpg pool + tiny migration runner.

Migrations are plain .sql files in `db/migrations/`, applied in filename order,
tracked in a `schema_migrations` table. `{{EMBEDDING_DIM}}` in a migration file is
substituted with `settings.embedding_dimension` before execution, so the pgvector
column width follows whatever embedding model is configured.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

from knowledgeos.config import Settings

logger = logging.getLogger("knowledgeos.db")

# repo root, two levels above this file (src/knowledgeos/db.py -> repo root)
REPO_ROOT = Path(__file__).resolve().parents[2]


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=10)


async def apply_migrations(pool: asyncpg.Pool, settings: Settings) -> list[str]:
    """Apply any pending .sql migrations. Returns the list of versions just applied."""
    migrations_dir = REPO_ROOT / settings.migrations_dir
    applied: list[str] = []

    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        already_applied = {
            row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations")
        }

        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.name
            if version in already_applied:
                continue

            sql = path.read_text(encoding="utf-8").replace(
                "{{EMBEDDING_DIM}}", str(settings.embedding_dimension)
            )
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", version
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
