"""Integration coverage for `cerebro token create`/`cerebro token revoke` against a
real `cerebro-auth` subprocess.

This replaces the previous ONE real integration test in this file, which spun up
`cerebro-memory` and `cerebro-docs` together to exercise the old CROSS-CUTTING token
mechanism (one secret registered separately in both services, with local
pending-retry state for partial failures -- ecosistema-cerebro.md SS13). Token
management no longer spans multiple services: `cerebro-auth` is now the single
source of truth, so there's no more partial-failure/retry scenario to cover. What's
left worth testing for real (not mocked) is that `cerebro token create`/`revoke`
round-trip correctly through `AuthClient` against a live `cerebro-auth` process on
the same local Postgres as the rest of the ecosystem's integration tests.

KNOWN GAP as of this writing (flagged for the parallel agent building cerebro-auth's
HTTP layer and cerebro-clients' `AuthClient`): `packages/cerebro-auth` currently has
no HTTP app entrypoint (no `cerebro_auth.main`/`cerebro_auth.api`) and no
`pyproject.toml` (so it isn't pip-installable/importable yet), and
`cerebro_clients.AuthClient` doesn't exist yet either. Every test in this module
therefore skips at collection time via `importorskip` instead of failing -- this is
scaffolding for real coverage once both land, not currently-passing verified
coverage. Re-run this file once those two pieces exist to confirm it actually works
end to end (in particular the exact response shape of `create_token`/`login`, which
this file assumes but can't verify yet).

Also skips automatically if Postgres doesn't respond at DATABASE_URL (same pattern
as the rest of the ecosystem's integration tests).
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

pytest.importorskip(
    "cerebro_auth.main",
    reason="cerebro-auth has no HTTP app entrypoint yet (packages/cerebro-auth is mid-build in parallel)",
)
_clients_mod = pytest.importorskip("cerebro_clients")
if not hasattr(_clients_mod, "AuthClient"):
    pytest.skip(
        "cerebro_clients.AuthClient not implemented yet (built in parallel alongside this change)",
        allow_module_level=True,
    )

from cerebro_clients import AuthClient  # noqa: E402

from cerebro_cli import shared_commands  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
AUTH_PORT = 8030
TEST_API_TOKEN = f"test-root-token-{uuid.uuid4().hex[:8]}"


def _db_reachable() -> bool:
    async def _check() -> None:
        # Same default DSN the apps use (compose.yaml SS8) - resolved via
        # cerebro_auth.config so as not to duplicate the default.
        from cerebro_auth.config import get_settings

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
def live_auth():
    if not _db_reachable():
        pytest.skip("Postgres not reachable at DATABASE_URL - run `docker compose up -d` first")

    env = os.environ.copy()
    env["API_TOKEN"] = TEST_API_TOKEN
    env["APP_PORT"] = str(AUTH_PORT)

    proc = subprocess.Popen(
        [sys.executable, "-m", "cerebro_auth.main"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_health(AUTH_PORT)
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(autouse=True)
def cerebro_env(monkeypatch):
    monkeypatch.setenv("CEREBRO_AUTH_URL", f"http://localhost:{AUTH_PORT}")
    monkeypatch.setenv("CEREBRO_TOKEN", TEST_API_TOKEN)


def _root_auth_client() -> AuthClient:
    return AuthClient(base_url=f"http://localhost:{AUTH_PORT}", token=TEST_API_TOKEN)


def _create_args(name: str, **overrides) -> argparse.Namespace:
    defaults = dict(
        name=name,
        scopes="read,write",
        modules="memory,docs",
        user=None,
        access_level="user",
        memory_contexts=None,
        docs_categories=None,
        flows_categories=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestTokenCreateLive:
    def test_creates_a_service_token_end_to_end(self, live_auth, capsys):
        name = f"itest-token-{uuid.uuid4().hex[:8]}"
        args = _create_args(name)

        shared_commands.cmd_token_create(args)  # real client, resolved via env vars

        printed = capsys.readouterr().out
        assert name in printed

        root = _root_auth_client()
        names = {t["name"] for t in root.list_tokens()}
        assert name in names


class TestTokenRevokeLive:
    def test_revokes_end_to_end(self, live_auth):
        name = f"itest-revoke-{uuid.uuid4().hex[:8]}"
        shared_commands.cmd_token_create(_create_args(name))

        shared_commands.cmd_token_revoke(argparse.Namespace(name=name))

        root = _root_auth_client()
        row = next(t for t in root.list_tokens() if t["name"] == name)
        assert row["revoked_at"] is not None

    def test_revoking_unknown_name_is_idempotent_success(self, live_auth):
        # must not raise SystemExit - a name with no active token is treated as
        # "already revoked", same idempotent-revoke behavior as before the
        # cerebro-auth unification.
        shared_commands.cmd_token_revoke(argparse.Namespace(name=f"does-not-exist-{uuid.uuid4().hex[:8]}"))
