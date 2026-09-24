"""Tests for token auth with scopes (`cerebro_flows.auth`) - now resolved against
the shared `cerebro_auth` schema instead of a local `api_tokens` table. Token
creation/CRUD no longer lives in this service, so integration tests populate
`cerebro_auth` directly via `cerebro_auth_helpers` instead of calling a local
`/tokens` endpoint.
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

from .cerebro_auth_helpers import (
    add_user_to_group,
    insert_group,
    insert_token,
    insert_user,
    set_group_scopes,
)

# --------------------------------------------------------------------------- unit: Principal


def _principal(**overrides) -> Principal:
    defaults = dict(
        name="x",
        scopes=frozenset({"read", "write"}),
        access_level="user",
        user_id=None,
        allowed_categories=None,
        owner_filter=None,
    )
    defaults.update(overrides)
    return Principal(**defaults)


def test_principal_has_scope():
    p = _principal(scopes=frozenset({"read", "write"}))
    assert p.has_scope("read")
    assert not p.has_scope("admin")


def test_principal_category_allowed_none_means_all():
    p = _principal(allowed_categories=None)
    assert p.category_allowed("cualquiera")
    assert p.category_allowed(None)


def test_principal_category_allowed_restricted():
    p = _principal(allowed_categories=frozenset({"incident"}))
    assert p.category_allowed("incident")
    assert not p.category_allowed("onboarding")


def test_principal_root_has_every_scope_and_no_category_restriction():
    p = _principal(
        name="root",
        scopes=frozenset({"read", "write", "admin"}),
        access_level="admin",
        allowed_categories=None,
        owner_filter=None,
        is_root=True,
    )
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestRootToken:
    def test_root_token_works(self, client, root_headers):
        resp = client.get("/categories", headers=root_headers)
        assert resp.status_code == 200, resp.text

    def test_missing_auth_is_401(self, client):
        assert client.get("/categories").status_code == 401


class TestSharedSchemaResolution:
    """Exercises the resolution algorithm in `cerebro_flows.auth.get_principal`
    against `cerebro_auth.*` rows inserted directly (no local /tokens endpoint)."""

    def test_unknown_token_is_401(self, client):
        resp = client.get("/categories", headers=_bearer("cbrf_does-not-exist"))
        assert resp.status_code == 401

    def test_revoked_token_is_401(self, client):
        pool = client.app.state.pool

        async def setup():
            user_id = await insert_user(pool, access_level="user")
            return await insert_token(pool, name=f"revoked-{uuid.uuid4().hex[:8]}", user_id=user_id, allowed_modules=["flows"])

        token = asyncio.run(setup())

        async def revoke():
            await pool.execute(
                "UPDATE cerebro_auth.api_tokens SET revoked_at = now() WHERE token_hash = $1",
                hash_token(token),
            )

        asyncio.run(revoke())
        resp = client.get("/categories", headers=_bearer(token))
        assert resp.status_code == 401

    def test_token_without_flows_module_is_blocked(self, client):
        pool = client.app.state.pool

        async def setup():
            user_id = await insert_user(pool, access_level="user")
            return await insert_token(
                pool,
                name=f"no-flows-{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                allowed_modules=["docs"],
            )

        token = asyncio.run(setup())
        resp = client.get("/categories", headers=_bearer(token))
        assert resp.status_code == 403

    def test_admin_access_level_inherits_all_modules_when_token_allowed_modules_is_null(self, client):
        pool = client.app.state.pool

        async def setup():
            user_id = await insert_user(pool, access_level="admin")
            return await insert_token(pool, name=f"admin-{uuid.uuid4().hex[:8]}", user_id=user_id, allowed_modules=None)

        token = asyncio.run(setup())
        resp = client.get("/categories", headers=_bearer(token))
        assert resp.status_code == 200, resp.text

    def test_user_access_level_with_no_allowed_modules_is_blocked(self, client):
        """access_level == 'user': nothing to inherit -- a NULL allowed_modules on
        the token means zero effective modules, not a permissive default."""
        pool = client.app.state.pool

        async def setup():
            user_id = await insert_user(pool, access_level="user")
            return await insert_token(pool, name=f"bare-user-{uuid.uuid4().hex[:8]}", user_id=user_id, allowed_modules=None)

        token = asyncio.run(setup())
        resp = client.get("/categories", headers=_bearer(token))
        assert resp.status_code == 403

    def test_owner_with_no_groups_and_no_token_scope_is_blocked(self, client):
        pool = client.app.state.pool

        async def setup():
            user_id = await insert_user(pool, access_level="owner")
            return await insert_token(pool, name=f"lonely-owner-{uuid.uuid4().hex[:8]}", user_id=user_id, allowed_modules=None)

        token = asyncio.run(setup())
        resp = client.get("/categories", headers=_bearer(token))
        assert resp.status_code == 403

    def test_owner_inherits_group_modules_and_narrows_categories(self, client):
        pool = client.app.state.pool

        async def setup():
            user_id = await insert_user(pool, access_level="owner")
            group_id = await insert_group(pool)
            await add_user_to_group(pool, user_id, group_id)
            await set_group_scopes(
                pool,
                group_id,
                allowed_modules=["flows"],
                module_scopes={"flows": {"categories": ["incident", "onboarding"]}},
            )
            return await insert_token(
                pool,
                name=f"owner-{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                allowed_modules=None,
                module_scopes={"flows": {"categories": ["incident"]}},
            )

        token = asyncio.run(setup())
        resp = client.get("/categories", headers=_bearer(token))
        assert resp.status_code == 200, resp.text

    def test_write_scope_required(self, client):
        pool = client.app.state.pool

        async def setup():
            user_id = await insert_user(pool, access_level="user")
            return await insert_token(
                pool,
                name=f"ro-{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                scopes=["read"],
                allowed_modules=["flows"],
            )

        token = asyncio.run(setup())
        resp = client.post(
            "/categories",
            json={"slug": f"cat-{uuid.uuid4().hex[:6]}", "code": "X", "name": "x"},
            headers=_bearer(token),
        )
        assert resp.status_code == 403


class TestTokensRoutesRemoved:
    """Token management moved to the `cerebro-auth` service - this package no
    longer exposes any of it."""

    def test_tokens_routes_are_gone(self, client, root_headers):
        assert client.post("/tokens", json={"name": "x", "scopes": ["read"]}, headers=root_headers).status_code == 404
        assert client.get("/tokens", headers=root_headers).status_code == 404
        assert client.delete("/tokens/whatever", headers=root_headers).status_code == 404
