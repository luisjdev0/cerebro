"""UN test de integracion real (ecosistema-cerebro.md SS15): levanta cerebro-memory
en :8005 y cerebro-docs en :8010 como subprocesos reales (mismo Postgres local de
siempre), y ejercita `cerebro token create`/`cerebro token revoke` TRANSVERSAL
(SS13) contra ellas de punta a punta -- sin mocks de cliente ni de transporte.

Se salta automaticamente si Postgres no responde en DATABASE_URL (mismo patron que el
resto de tests de integracion del ecosistema).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import pytest
from cerebro_clients import DocsClient, MemoryClient

from cerebro_cli import shared_commands, tokens

REPO_ROOT = Path(__file__).resolve().parents[4]
MEMORY_PORT = 8005
DOCS_PORT = 8010
TEST_API_TOKEN = f"test-root-token-{uuid.uuid4().hex[:8]}"


def _db_reachable() -> bool:
    async def _check() -> None:
        # Misma DSN por defecto que usan las apps (compose.yaml SS8) - la resolvemos
        # via cerebro_memory.config para no duplicar el default.
        from cerebro_memory.config import get_settings

        conn = await asyncpg.connect(dsn=get_settings().database_url, timeout=8)
        await conn.close()

    try:
        asyncio.run(_check())
        return True
    except Exception:
        return False


def _wait_for_health(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://localhost:{port}/health", timeout=2.0)
            if resp.status_code == 200:
                return
        except httpx.RequestError as exc:
            last_exc = exc
        time.sleep(0.5)
    raise RuntimeError(f"servicio en :{port} no respondio /health a tiempo: {last_exc}")


@pytest.fixture(scope="module")
def live_apis():
    if not _db_reachable():
        pytest.skip("Postgres not reachable at DATABASE_URL - run `docker compose up -d` first")

    env = os.environ.copy()
    env["API_TOKEN"] = TEST_API_TOKEN

    memory_env = {**env, "APP_PORT": str(MEMORY_PORT)}
    docs_env = {**env, "APP_PORT": str(DOCS_PORT)}

    memory_proc = subprocess.Popen(
        [sys.executable, "-m", "cerebro_memory.main"],
        cwd=REPO_ROOT,
        env=memory_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    docs_proc = subprocess.Popen(
        [sys.executable, "-m", "cerebro_docs.main"],
        cwd=REPO_ROOT,
        env=docs_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_health(MEMORY_PORT)
        _wait_for_health(DOCS_PORT)
        yield
    finally:
        memory_proc.terminate()
        docs_proc.terminate()
        memory_proc.wait(timeout=10)
        docs_proc.wait(timeout=10)


@pytest.fixture(autouse=True)
def isolated_pending_dir(tmp_path, monkeypatch):
    # Aislado por test para que el estado pendiente de un test no contamine el
    # siguiente (ver cerebro_cli.tokens).
    monkeypatch.setattr(tokens, "PENDING_TOKENS_DIR", tmp_path / "pending-tokens")


@pytest.fixture(autouse=True)
def cerebro_env(monkeypatch):
    monkeypatch.setenv("CEREBRO_MEMORY_URL", f"http://localhost:{MEMORY_PORT}")
    monkeypatch.setenv("CEREBRO_DOCS_URL", f"http://localhost:{DOCS_PORT}")
    monkeypatch.setenv("CEREBRO_TOKEN", TEST_API_TOKEN)


def _root_memory_client() -> MemoryClient:
    return MemoryClient(base_url=f"http://localhost:{MEMORY_PORT}", token=TEST_API_TOKEN)


def _root_docs_client() -> DocsClient:
    return DocsClient(base_url=f"http://localhost:{DOCS_PORT}", token=TEST_API_TOKEN)


class TestTokenCreateTransversalLive:
    def test_registers_same_secret_in_both_services(self, live_apis, capsys):
        name = f"itest-token-{uuid.uuid4().hex[:8]}"
        args = argparse.Namespace(name=name, scopes="read,write", contexts=None, categories=None)

        shared_commands.cmd_token_create(args)  # clientes reales, resueltos por env vars

        printed = capsys.readouterr().out
        # el secreto impreso debe funcionar contra AMBOS servicios
        secret_line = [line.strip() for line in printed.splitlines() if line.strip().startswith("cbr_")]
        assert secret_line, printed
        secret = secret_line[0]

        memory_check = httpx.get(
            f"http://localhost:{MEMORY_PORT}/contexts", headers={"Authorization": f"Bearer {secret}"}
        )
        assert memory_check.status_code == 200, memory_check.text

        docs_check = httpx.get(
            f"http://localhost:{DOCS_PORT}/categories", headers={"Authorization": f"Bearer {secret}"}
        )
        assert docs_check.status_code == 200, docs_check.text

    def test_partial_failure_reports_per_service_and_exits_nonzero(self, live_apis, monkeypatch):
        name = f"itest-partial-{uuid.uuid4().hex[:8]}"
        # Apunta cerebro-docs a un puerto sin nada escuchando - simula el servicio
        # caido sin tocar el subproceso de memory, que sigue sano.
        monkeypatch.setenv("CEREBRO_DOCS_URL", "http://localhost:1")
        args = argparse.Namespace(name=name, scopes="read", contexts=None, categories=None)

        with pytest.raises(SystemExit) as exc_info:
            shared_commands.cmd_token_create(args)
        assert exc_info.value.code != 0

        # memory SI quedo registrado
        root_memory = _root_memory_client()
        names = {t["name"] for t in root_memory.list_tokens()}
        assert name in names

    def test_retry_after_partial_failure_completes_without_duplicating(self, live_apis, monkeypatch):
        name = f"itest-retry-{uuid.uuid4().hex[:8]}"
        monkeypatch.setenv("CEREBRO_DOCS_URL", "http://localhost:1")
        args = argparse.Namespace(name=name, scopes="read", contexts=None, categories=None)
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_create(args)

        # ahora cerebro-docs vuelve a estar disponible - reintenta EXACTAMENTE el
        # mismo comando (mismo name/scopes), debe completar sin duplicar la fila de
        # memory ni requerir un secreto nuevo.
        monkeypatch.setenv("CEREBRO_DOCS_URL", f"http://localhost:{DOCS_PORT}")
        shared_commands.cmd_token_create(args)

        root_memory = _root_memory_client()
        matching = [t for t in root_memory.list_tokens() if t["name"] == name]
        assert len(matching) == 1  # nunca se duplico la fila

        root_docs = _root_docs_client()
        matching_docs = [t for t in root_docs.list_tokens() if t["name"] == name]
        assert len(matching_docs) == 1


class TestTokenRevokeTransversalLive:
    def test_revokes_in_both_services(self, live_apis):
        name = f"itest-revoke-{uuid.uuid4().hex[:8]}"
        create_args = argparse.Namespace(name=name, scopes="read", contexts=None, categories=None)
        shared_commands.cmd_token_create(create_args)

        revoke_args = argparse.Namespace(name=name)
        shared_commands.cmd_token_revoke(revoke_args)

        root_memory = _root_memory_client()
        memory_row = next(t for t in root_memory.list_tokens() if t["name"] == name)
        assert memory_row["revoked_at"] is not None

        root_docs = _root_docs_client()
        docs_row = next(t for t in root_docs.list_tokens() if t["name"] == name)
        assert docs_row["revoked_at"] is not None
