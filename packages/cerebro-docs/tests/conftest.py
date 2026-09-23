"""Aisla los tests de este paquete contra una base de datos Postgres efimera
(`cerebro_test`), separada de la base de desarrollo real (`knowledgeos`).

Ver el conftest.py equivalente en packages/cerebro-memory/tests/ para el
detalle completo del mecanismo -- este es identico salvo por el DSN por
defecto, que aqui coincide con `cerebro_docs.config.Settings.database_url`
(misma instancia Postgres compartida entre ambos servicios, distinto schema).

Importante: esto vive en `pytest_configure`, NO en un fixture (ni siquiera uno
`session`+`autouse`) -- `cerebro_docs/api.py` tiene `app = create_app()` a
nivel de MODULO (necesario para `uvicorn cerebro_docs.main:app` en
produccion), que llama a `get_settings()` (con `@lru_cache`) al IMPORTAR el
modulo, durante la fase de "collection" de pytest -- ANTES de que cualquier
fixture llegue a correr. Solo `pytest_configure` corre a tiempo para ganarle a
ese import.
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
