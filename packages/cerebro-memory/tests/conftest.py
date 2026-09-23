"""Aisla los tests de este paquete contra una base de datos Postgres efimera
(`cerebro_test`), separada de la base de desarrollo real (`knowledgeos`).

Sin esto, la suite de tests de integracion (test_auth.py, test_supersedence.py,
test_graph.py, ...) escribe directamente contra la base de datos local de
desarrollo via `get_settings().database_url`, acumulando filas `test-*` entre
sesiones -- ver `luisjdev-pendientes/ecosistema-cerebro` (Infraestructura /
Testing) en cerebro-docs.

Se dropea y recrea `cerebro_test` al inicio de la sesion de pytest y se
sobreescribe `DATABASE_URL` en el entorno del proceso ANTES de que cualquier
modulo de test se importe -- por eso esto vive en `pytest_configure`, NO en un
fixture (ni siquiera un fixture `session`+`autouse`): `cerebro_memory/api.py`
tiene `app = create_app()` a nivel de MODULO (necesario para
`uvicorn cerebro_memory.main:app` en produccion), que llama a
`get_settings()` (con `@lru_cache`) en el momento de IMPORTAR el modulo. Un
fixture -- por temprano que corra -- se ejecuta durante la fase de "run", que
en pytest sucede DESPUES de la fase de "collection" (donde se importan todos
los modulos de test, y con ellos `cerebro_memory.api`) -- para entonces el
cache de `get_settings()` ya habria quedado fijo en el `DATABASE_URL` real de
`.env`, sin importar que el fixture despues sobreescriba la variable de
entorno. `pytest_configure` sí corre antes de la collection, a tiempo para
ganarle a ese import.

Como `create_app()` ya corre las migraciones en su `lifespan` de arranque (ver
db.py/api.py), la base efimera se migra sola en el primer `TestClient` de cada
modulo -- no hace falta tocar nada mas.

Requiere que el rol de `DATABASE_URL` pueda crear/borrar bases (el mismo rol
que crea la instancia via `POSTGRES_USER` en compose.yaml ya tiene ese permiso,
por ser el rol de `initdb`). No pensado para correr en paralelo con otra suite
de este ecosistema contra el mismo Postgres -- competirian por el mismo nombre
de base.
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
        # Postgres no alcanzable: cada test se salta solo via su propio
        # _db_reachable() contra DATABASE_URL, igual que antes de este hook.
        return

    os.environ["DATABASE_URL"] = test_dsn
