"""Aisla los tests de este paquete contra una base de datos Postgres efimera
(`cerebro_test`), separada de la base de desarrollo real. Ver el conftest.py
equivalente en packages/cerebro-memory/tests/ para el detalle completo del mecanismo
y por que vive en `pytest_configure` (no un fixture: `cerebro_flows/api.py` instancia
`app = create_app()` a nivel de modulo, que cachea `get_settings()` al importarse
durante la fase de collection de pytest, antes de que cualquier fixture corra).

`REDIS_URL` tambien se sobreescribe aqui a una base logica de Redis dedicada
(`/15`, la ultima de las 16 por defecto) para no chocar con el uso normal de la
`/0` en desarrollo -- Redis no tiene el concepto de "base efimera que se recrea",
asi que en vez de dropear/crear como con Postgres, cada sesion de test hace
`FLUSHDB` sobre esa base dedicada al arrancar.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg

TEST_DB_NAME = "cerebro_test"
_DEFAULT_DSN = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_TEST_REDIS_DB = "15"


def _with_database(dsn: str, database: str) -> str:
    return urlunsplit(urlsplit(dsn)._replace(path=f"/{database}"))


async def _recreate_test_database(maintenance_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=maintenance_dsn, timeout=8)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


def _test_redis_url() -> str:
    base = os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL)
    return urlunsplit(urlsplit(base)._replace(path=f"/{_TEST_REDIS_DB}"))


async def _flush_test_redis(redis_url: str) -> None:
    import redis.asyncio as redis

    client = redis.from_url(redis_url)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    base_dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    test_dsn = _with_database(base_dsn, TEST_DB_NAME)
    test_redis_url = _test_redis_url()

    try:
        asyncio.run(_recreate_test_database(_with_database(base_dsn, "postgres")))
        asyncio.run(_flush_test_redis(test_redis_url))
    except Exception:
        # Postgres/Redis no alcanzables: cada test se salta solo via su propio
        # _db_reachable(), igual que antes de este hook.
        return

    os.environ["DATABASE_URL"] = test_dsn
    os.environ["REDIS_URL"] = test_redis_url
