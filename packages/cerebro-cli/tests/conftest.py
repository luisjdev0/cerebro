"""Aisla los tests de este paquete contra una base de datos Postgres efimera
(`cerebro_test`), separada de la base de desarrollo real (`knowledgeos`).

Ver el conftest.py equivalente en packages/cerebro-memory/tests/ para el
detalle completo del mecanismo, incluido por que esto vive en
`pytest_configure` y no en un fixture (`cerebro_memory.api`/`cerebro_docs.api`
instancian `app = create_app()` a nivel de modulo, al importarse -- durante la
fase de "collection" de pytest, antes de que cualquier fixture corra).

Aqui importa ademas porque test_token_transversal_integration.py lanza
subprocesos reales de `cerebro_memory.main`/`cerebro_docs.main` heredando
`os.environ` (`env = os.environ.copy()`) -- como `DATABASE_URL` ya queda
sobreescrito en el proceso de pytest antes de que ese fixture arranque los
subprocesos, ambos servicios heredan la base efimera automaticamente, sin
tocar ese archivo. Los prefijos `itest-*` que se ven en la base de desarrollo
hoy vienen justamente de ese test.
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
