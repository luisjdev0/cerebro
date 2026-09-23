"""ONE real integration test (ecosistema-cerebro.md SS15): spins up cerebro-memory
on :8005 and cerebro-docs on :8010 as real subprocesses (the same local Postgres as
always), and exercises CROSS-CUTTING `cerebro token create`/`cerebro token revoke`
(SS13) against them end to end -- with no client or transport mocks.

Skips automatically if Postgres doesn't respond at DATABASE_URL (same pattern as the
rest of the ecosystem's integration tests).
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
        # Same default DSN the apps use (compose.yaml SS8) - we resolve it
        # via cerebro_memory.config so as not to duplicate the default.
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
    # Isolated per test so one test's pending state doesn't contaminate the
    # next one (see cerebro_cli.tokens).
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

        shared_commands.cmd_token_create(args)  # real clients, resolved via env vars

        printed = capsys.readouterr().out
        # the printed secret must work against BOTH services
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
        # Point cerebro-docs at a port with nothing listening - simulates the service
        # being down without touching the memory subprocess, which stays healthy.
        monkeypatch.setenv("CEREBRO_DOCS_URL", "http://localhost:1")
        args = argparse.Namespace(name=name, scopes="read", contexts=None, categories=None)

        with pytest.raises(SystemExit) as exc_info:
            shared_commands.cmd_token_create(args)
        assert exc_info.value.code != 0

        # memory DID end up registered
        root_memory = _root_memory_client()
        names = {t["name"] for t in root_memory.list_tokens()}
        assert name in names

    def test_retry_after_partial_failure_completes_without_duplicating(self, live_apis, monkeypatch):
        name = f"itest-retry-{uuid.uuid4().hex[:8]}"
        monkeypatch.setenv("CEREBRO_DOCS_URL", "http://localhost:1")
        args = argparse.Namespace(name=name, scopes="read", contexts=None, categories=None)
        with pytest.raises(SystemExit):
            shared_commands.cmd_token_create(args)

        # now cerebro-docs is available again - retries EXACTLY the
        # same command (same name/scopes); it must complete without duplicating memory's
        # row or requiring a new secret.
        monkeypatch.setenv("CEREBRO_DOCS_URL", f"http://localhost:{DOCS_PORT}")
        shared_commands.cmd_token_create(args)

        root_memory = _root_memory_client()
        matching = [t for t in root_memory.list_tokens() if t["name"] == name]
        assert len(matching) == 1  # the row was never duplicated

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
