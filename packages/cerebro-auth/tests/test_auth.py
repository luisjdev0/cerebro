"""Tests for cerebro-auth's own HTTP API: user/group/token CRUD through the
admin-gated routes, plus the request-shape validation around token creation
(user vs. access_level mutual exclusion, service-token allowed_modules
requirement) -- a mirror of cerebro-flows/tests/test_auth.py's structure
(client/root_headers fixtures, `_db_reachable` skip-guard), adapted to
cerebro-auth's own route set.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_auth.api import create_app
from cerebro_auth.auth import generate_token, hash_token
from cerebro_auth.config import get_settings


# --------------------------------------------------------------------------- unit: token helpers


def test_hash_token_is_deterministic_sha256_hex():
    h1 = hash_token("cbra_abc")
    h2 = hash_token("cbra_abc")
    assert h1 == h2
    assert len(h1) == 64


def test_generate_token_has_prefix_and_is_unique():
    t1 = generate_token()
    t2 = generate_token()
    assert t1.startswith("cbra_")
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


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- auth gate


class TestAuthGate:
    def test_root_token_works(self, client, root_headers):
        resp = client.get("/users", headers=root_headers)
        assert resp.status_code == 200, resp.text

    def test_missing_auth_is_401(self, client):
        assert client.get("/users").status_code == 401

    def test_health_needs_no_auth(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200, resp.text

    def test_non_admin_token_cannot_hit_admin_routes(self, client, root_headers):
        # A service/root-adjacent token at access_level='user' has no admin rights
        # of its own on cerebro-auth's routes.
        name = _unique("test-user-token")
        token = client.post(
            "/tokens",
            json={"name": name, "scopes": ["read"], "access_level": "user", "allowed_modules": ["memory"]},
            headers=root_headers,
        ).json()["token"]
        resp = client.get("/users", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403


# --------------------------------------------------------------------------- users


class TestUsers:
    def test_create_and_list_user(self, client, root_headers):
        name = _unique("alice")
        resp = client.post("/users", json={"name": name, "email": "a@example.com"}, headers=root_headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == name
        assert body["access_level"] == "user"
        assert "password_hash" not in body

        listed = client.get("/users", headers=root_headers).json()
        assert any(u["name"] == name for u in listed)

    def test_create_user_with_explicit_access_level(self, client, root_headers):
        name = _unique("bob-owner")
        resp = client.post("/users", json={"name": name, "access_level": "owner"}, headers=root_headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["access_level"] == "owner"

    def test_duplicate_user_name_is_409(self, client, root_headers):
        name = _unique("dup-user")
        first = client.post("/users", json={"name": name}, headers=root_headers)
        assert first.status_code == 201, first.text
        second = client.post("/users", json={"name": name}, headers=root_headers)
        assert second.status_code == 409

    def test_invalid_access_level_is_422(self, client, root_headers):
        resp = client.post(
            "/users", json={"name": _unique("bad-level"), "access_level": "superadmin"}, headers=root_headers
        )
        assert resp.status_code == 422

    def test_unknown_field_is_422(self, client, root_headers):
        resp = client.post(
            "/users", json={"name": _unique("extra-field"), "nickname": "x"}, headers=root_headers
        )
        assert resp.status_code == 422


# --------------------------------------------------------------------------- groups


class TestGroups:
    def test_create_group_set_scopes_and_add_member(self, client, root_headers):
        slug = _unique("eng")
        group = client.post("/groups", json={"slug": slug, "name": "Engineering"}, headers=root_headers)
        assert group.status_code == 201, group.text

        scopes = client.post(
            f"/groups/{slug}/scopes",
            json={"allowed_modules": ["memory", "docs"], "module_scopes": {"memory": ["read"]}},
            headers=root_headers,
        )
        assert scopes.status_code == 200, scopes.text
        assert sorted(scopes.json()["allowed_modules"]) == ["docs", "memory"]

        # Upsert: replaces, not merges.
        replaced = client.post(
            f"/groups/{slug}/scopes",
            json={"allowed_modules": ["flows"]},
            headers=root_headers,
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["allowed_modules"] == ["flows"]

        user_name = _unique("carol")
        client.post("/users", json={"name": user_name}, headers=root_headers)

        member = client.post(f"/groups/{slug}/members", json={"user": user_name}, headers=root_headers)
        assert member.status_code == 201, member.text

        # Idempotent: adding the same member again does not error.
        again = client.post(f"/groups/{slug}/members", json={"user": user_name}, headers=root_headers)
        assert again.status_code == 201, again.text

    def test_scopes_for_unknown_group_is_404(self, client, root_headers):
        resp = client.post(
            "/groups/does-not-exist/scopes",
            json={"allowed_modules": ["memory"]},
            headers=root_headers,
        )
        assert resp.status_code == 404

    def test_member_for_unknown_user_is_404(self, client, root_headers):
        slug = _unique("grp-for-404")
        client.post("/groups", json={"slug": slug, "name": "x"}, headers=root_headers)
        resp = client.post(f"/groups/{slug}/members", json={"user": "no-such-user"}, headers=root_headers)
        assert resp.status_code == 404

    def test_duplicate_group_slug_is_409(self, client, root_headers):
        slug = _unique("dup-group")
        first = client.post("/groups", json={"slug": slug, "name": "x"}, headers=root_headers)
        assert first.status_code == 201, first.text
        second = client.post("/groups", json={"slug": slug, "name": "y"}, headers=root_headers)
        assert second.status_code == 409

    def test_invalid_module_in_scopes_is_422(self, client, root_headers):
        slug = _unique("grp-bad-module")
        client.post("/groups", json={"slug": slug, "name": "x"}, headers=root_headers)
        resp = client.post(
            f"/groups/{slug}/scopes", json={"allowed_modules": ["not-a-module"]}, headers=root_headers
        )
        assert resp.status_code == 422


# --------------------------------------------------------------------------- tokens


class TestTokens:
    def test_service_token_lifecycle(self, client, root_headers):
        name = _unique("svc-token")
        resp = client.post(
            "/tokens",
            json={"name": name, "scopes": ["read", "write"], "access_level": "admin", "allowed_modules": ["memory"]},
            headers=root_headers,
        )
        assert resp.status_code == 201, resp.text
        token = resp.json()["token"]
        assert token.startswith("cbra_")

        listed = client.get("/tokens", headers=root_headers).json()
        entry = next(t for t in listed if t["name"] == name)
        assert "token" not in entry

        revoke = client.delete(f"/tokens/{name}", headers=root_headers)
        assert revoke.status_code == 200, revoke.text

        revoke_again = client.delete(f"/tokens/{name}", headers=root_headers)
        assert revoke_again.status_code == 404

    def test_user_token_inherits_access_level(self, client, root_headers):
        user_name = _unique("dave")
        client.post("/users", json={"name": user_name, "access_level": "owner"}, headers=root_headers)

        resp = client.post(
            "/tokens",
            json={"name": _unique("dave-token"), "scopes": ["read"], "user": user_name},
            headers=root_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["access_level"] is None  # inherited, not stored redundantly on the token
        assert body["user_id"] is not None

    def test_token_with_both_user_and_access_level_is_422(self, client, root_headers):
        user_name = _unique("erin")
        client.post("/users", json={"name": user_name}, headers=root_headers)
        resp = client.post(
            "/tokens",
            json={"name": _unique("bad-token"), "scopes": ["read"], "user": user_name, "access_level": "admin"},
            headers=root_headers,
        )
        assert resp.status_code == 422

    def test_token_with_neither_user_nor_access_level_is_422(self, client, root_headers):
        resp = client.post(
            "/tokens",
            json={"name": _unique("bad-token-2"), "scopes": ["read"]},
            headers=root_headers,
        )
        assert resp.status_code == 422

    def test_service_token_without_allowed_modules_is_422(self, client, root_headers):
        resp = client.post(
            "/tokens",
            json={"name": _unique("bad-token-3"), "scopes": ["read"], "access_level": "admin"},
            headers=root_headers,
        )
        assert resp.status_code == 422

    def test_token_for_unknown_user_is_404(self, client, root_headers):
        resp = client.post(
            "/tokens",
            json={"name": _unique("bad-token-4"), "scopes": ["read"], "user": "no-such-user"},
            headers=root_headers,
        )
        assert resp.status_code == 404

    def test_duplicate_token_name_is_409(self, client, root_headers):
        name = _unique("dup-token")
        first = client.post(
            "/tokens",
            json={"name": name, "scopes": ["read"], "access_level": "user", "allowed_modules": ["memory"]},
            headers=root_headers,
        )
        assert first.status_code == 201, first.text
        second = client.post(
            "/tokens",
            json={"name": name, "scopes": ["read"], "access_level": "user", "allowed_modules": ["memory"]},
            headers=root_headers,
        )
        assert second.status_code == 409


# --------------------------------------------------------------------------- login


class TestLogin:
    def test_login_with_root_token(self, client):
        resp = client.post("/login", json={"token": get_settings().api_token})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["access_level"] == "admin"
        assert sorted(body["allowed_modules"]) == ["docs", "flows", "memory"]

    def test_login_with_invalid_token_is_401(self, client):
        resp = client.post("/login", json={"token": "cbra_not-a-real-token"})
        assert resp.status_code == 401

    def test_login_with_revoked_token_is_401(self, client, root_headers):
        name = _unique("revoked-login")
        token = client.post(
            "/tokens",
            json={"name": name, "scopes": ["read"], "access_level": "user", "allowed_modules": ["memory"]},
            headers=root_headers,
        ).json()["token"]
        client.delete(f"/tokens/{name}", headers=root_headers)

        resp = client.post("/login", json={"token": token})
        assert resp.status_code == 401

    def test_login_resolves_service_token_identity(self, client, root_headers):
        name = _unique("login-svc")
        token = client.post(
            "/tokens",
            json={"name": name, "scopes": ["read"], "access_level": "user", "allowed_modules": ["docs", "flows"]},
            headers=root_headers,
        ).json()["token"]

        resp = client.post("/login", json={"token": token})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == name
        assert body["access_level"] == "user"
        assert sorted(body["allowed_modules"]) == ["docs", "flows"]


# --------------------------------------------------------------------------- backup


@pytest.fixture(scope="module")
def pg_dump_available():
    # POST /backup shells out to the `pg_dump` binary on PATH (see api.py) -- inside
    # the real container it's installed by the Dockerfile (PGDG postgresql-client-17),
    # but pytest here runs directly on the host, not inside that container. Skip
    # rather than fail if this host happens not to have it, same spirit as
    # `_db_reachable` skipping when Postgres itself isn't reachable.
    if shutil.which("pg_dump") is None:
        pytest.skip("pg_dump not found on PATH - install postgresql-client to test POST /backup locally")


class TestBackup:
    def test_requires_admin(self, client, root_headers):
        name = _unique("backup-non-admin")
        token = client.post(
            "/tokens",
            json={"name": name, "scopes": ["read"], "access_level": "user", "allowed_modules": ["memory"]},
            headers=root_headers,
        ).json()["token"]
        resp = client.post("/backup", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_missing_auth_is_401(self, client):
        assert client.post("/backup").status_code == 401

    def test_dump_is_streamed_sql_covering_this_databases_schema(self, client, root_headers, pg_dump_available):
        # This suite's `cerebro_test` only has cerebro-auth's own migrations applied
        # (this package's conftest.py doesn't pull in memory/docs/flows) -- it can only
        # assert the `cerebro_auth` schema shows up. That `pg_dump` with no `--schema`
        # flag covers every schema in the target database (memory/docs/flows/auth
        # together, in a real deployment where all 4 share one Postgres instance) is
        # verified by the manual end-to-end pass on the test server (see the plan's
        # verification section), not by this unit test.
        resp = client.post("/backup", headers=root_headers)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/sql")
        assert "attachment" in resp.headers["content-disposition"]
        assert ".sql" in resp.headers["content-disposition"]

        body = resp.text
        assert "PostgreSQL database dump" in body
        assert "CREATE SCHEMA cerebro_auth" in body or "cerebro_auth.users" in body
