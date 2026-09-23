"""asyncpg pool + tiny migration runner + cliente Redis.

Mismo runner "estricto" que `cerebro_docs.db` (ver su docstring): crea el schema
`cerebro_flows` y su propia `schema_migrations` calificada ANTES de correr cualquier
migracion, para que una instalacion fresca quede toda en `cerebro_flows` en un solo
arranque sin depender de que el search_path ya sea correcto.

Redis (SS5 del documento de diseno) solo guarda el puntero MUTABLE de una ejecucion
(`flow_run:<run_id>`) -- nunca la definicion completa del flujo, que se relee de
Postgres en cada paso. Un solo cliente async, reusado por toda la app (mismo patron
que el pool de asyncpg).
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg
import redis.asyncio as redis

from cerebro_flows.config import Settings

logger = logging.getLogger("cerebro_flows.db")

# package root, two levels above this file (src/cerebro_flows/db.py -> packages/cerebro-flows)
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=10,
        server_settings={"search_path": "cerebro_flows, public"},
    )


def create_redis(settings: Settings) -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


async def apply_migrations(pool: asyncpg.Pool, settings: Settings) -> list[str]:
    """Apply any pending .sql migrations. Returns the list of versions just applied."""
    migrations_dir = PACKAGE_ROOT / settings.migrations_dir
    applied: list[str] = []

    async with pool.acquire() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS cerebro_flows")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cerebro_flows.schema_migrations (
                version     TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        already_applied = {
            row["version"]
            for row in await conn.fetch("SELECT version FROM cerebro_flows.schema_migrations")
        }

        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.name
            if version in already_applied:
                continue

            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO cerebro_flows.schema_migrations (version) VALUES ($1)", version
                )
            logger.info("applied migration %s", version)
            applied.append(version)

    return applied


async def check_health(pool: asyncpg.Pool, redis_client: redis.Redis) -> bool:
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        await redis_client.ping()
        return True
    except Exception:  # noqa: BLE001
        logger.exception("health check failed")
        return False
