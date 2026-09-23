"""Tests for token auth with scopes (`cerebro_flows.auth`) - a mirror of
cerebro-docs/tests/test_auth.py (the exact same mechanism, see its docstring).
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_flows.api import create_app
from cerebro_flows.auth import Principal, generate_token, hash_token
from cerebro_flows.config import get_settings

# --------------------------------------------------------------------------- unit: Principal


def test_principal_has_scope():
    p = Principal(name="x", scopes=frozenset({"read", "write"}), allowed_categories=None)
    assert p.has_scope("read")
    assert not p.has_scope("admin")


def test_principal_category_allowed_none_means_all():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_categories=None)
    assert p.category_allowed("cualquiera")
    assert p.category_allowed(None)


def test_principal_category_allowed_restricted():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_categories=frozenset({"incident"}))
    assert p.category_allowed("incident")
    assert not p.category_allowed("onboarding")


def test_principal_root_has_every_scope_and_no_category_restriction():
    p = Principal(name="root", scopes=frozenset({"read", "write", "admin"}), allowed_categories=None, is_root=True)
    assert p.is_root
    for scope in ("read", "write", "admin"):
        assert p.has_scope(scope)


# --------------------------------------------------------------------------- unit: token helpers


def test_hash_token_is_deterministic_sha256_hex():
    h1 = hash_token("cbrf_abc")
    h2 = hash_token("cbrf_abc")
    assert h1 == h2
    assert len(h1) == 64


def test_generate_token_has_prefix_and_is_unique():
    t1 = generate_token()
    t2 = generate_token()
    assert t1.startswith("cbrf_")
    assert t1 != t2


# --------------------------------------------------------------------------- integration


def _db_reachable(dsn: str) -> bool:
    async def _check() -> None:
        conn = await asyncpg.connect(dsn=dsn, timeout=8)
        await conn.close()

    try:
        asyncio.run(_check())
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not _db_reachable(settings.database_url):
        pytest.skip("Postgres not reachable at DATABASE_URL - run `docker compose up -d` first")
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def root_headers():
    return {"Authorization": f"Bearer {get_settings().api_token}"}


class TestTokenLifecycle:
    def test_root_token_works(self, client, root_headers):
        resp = client.get("/categories", headers=root_headers)
        assert resp.status_code == 200, resp.text

    def test_missing_auth_is_401(self, client):
        assert client.get("/categories").status_code == 401

    def test_create_list_revoke_roundtrip(self, client, root_headers):
        name = f"test-token-{uuid.uuid4().hex[:8]}"
        resp = client.post("/tokens", json={"name": name, "scopes": ["read"]}, headers=root_headers)
        assert resp.status_code == 201, resp.text
        token = resp.json()["token"]
        assert token.startswith("cbrf_")

        auth = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert auth.status_code == 200

        revoke = client.delete(f"/tokens/{name}", headers=root_headers)
        assert revoke.status_code == 200, revoke.text

        after = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert after.status_code == 401

    def test_read_only_token_cannot_write(self, client, root_headers):
        name = f"test-token-ro-{uuid.uuid4().hex[:8]}"
        token = client.post("/tokens", json={"name": name, "scopes": ["read"]}, headers=root_headers).json()["token"]
        resp = client.post(
            "/categories",
            json={"slug": f"cat-{uuid.uuid4().hex[:6]}", "code": "X", "name": "x"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
