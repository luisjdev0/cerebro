"""Portal-only admin endpoints: PATCH /users/{name}, group listing/rename/delete and member removal."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_auth.api import create_app
from cerebro_auth.config import get_settings


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _sql(query: str, *args) -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(dsn=get_settings().database_url, timeout=8)
        try:
            await conn.execute(query, *args)
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.fixture
def client():
    settings = get_settings()
    try:
        _sql("SELECT 1")
    except Exception:
        pytest.skip("Postgres not reachable at DATABASE_URL")
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def root():
    return {"Authorization": f"Bearer {get_settings().api_token}"}


def _user(client, root, level="user", **extra):
    name = _unique("pu")
    r = client.post("/users", json={"name": name, "access_level": level, **extra}, headers=root)
    assert r.status_code == 201, r.text
    return name


class TestUsers:
    def test_list_includes_groups_and_patch_updates_fields(self, client, root):
        name = _user(client, root)
        slug = _unique("pg")
        client.post("/groups", json={"slug": slug, "name": "G"}, headers=root)
        client.post(f"/groups/{slug}/members", json={"user": name}, headers=root)
        row = next(u for u in client.get("/users", headers=root).json() if u["name"] == name)
        assert row["groups"] == [slug]

        r = client.patch(f"/users/{name}", json={"email": "a@b.co", "access_level": "owner"}, headers=root)
        assert r.status_code == 200, r.text
        assert (r.json()["email"], r.json()["access_level"], r.json()["groups"]) == ("a@b.co", "owner", [slug])
        assert client.patch(f"/users/{name}", json={"email": ""}, headers=root).json()["email"] is None

    def test_deactivate_and_reactivate_stops_the_users_tokens(self, client, root):
        name = _user(client, root)
        tok = client.post("/tokens", json={"name": _unique("t"), "scopes": ["read"], "user": name}, headers=root).json()["token"]
        assert client.post("/login", json={"token": tok}).status_code == 200
        off = client.patch(f"/users/{name}", json={"active": False}, headers=root).json()
        assert off["revoked_at"] is not None
        assert client.post("/login", json={"token": tok}).status_code == 401
        on = client.patch(f"/users/{name}", json={"active": True}, headers=root).json()
        assert on["revoked_at"] is None
        assert client.post("/login", json={"token": tok}).status_code == 200

    def test_validation_and_missing_user(self, client, root):
        name = _user(client, root)
        assert client.patch(f"/users/{name}", json={}, headers=root).status_code == 422
        assert client.patch(f"/users/{name}", json={"name": "x"}, headers=root).status_code == 422
        assert client.patch(f"/users/{name}", json={"access_level": "root"}, headers=root).status_code == 422
        assert client.patch("/users/nobody-here", json={"email": "x@y.z"}, headers=root).status_code == 404

    def test_last_active_admin_cannot_be_demoted_or_deactivated(self, client, root):
        sole = _user(client, root, "admin")
        _sql("UPDATE cerebro_auth.users SET revoked_at = now() WHERE access_level = 'admin' AND name <> $1", sole)
        assert client.patch(f"/users/{sole}", json={"access_level": "user"}, headers=root).status_code == 409
        assert client.patch(f"/users/{sole}", json={"active": False}, headers=root).status_code == 409
        other = _user(client, root, "admin")
        assert client.patch(f"/users/{sole}", json={"access_level": "user"}, headers=root).status_code == 200
        assert client.patch(f"/users/{other}", json={"active": False}, headers=root).status_code == 409

    def test_requires_admin(self, client, root):
        name = _user(client, root)
        tok = client.post("/tokens", json={"name": _unique("t"), "scopes": ["read", "write"], "user": name}, headers=root).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert client.patch(f"/users/{name}", json={"email": "x@y.z"}, headers=h).status_code == 403
        assert client.get("/groups", headers=h).status_code == 403


class TestGroups:
    def test_list_rename_remove_member_and_delete(self, client, root):
        slug = _unique("pg")
        u1, u2 = _user(client, root), _user(client, root)
        client.post("/groups", json={"slug": slug, "name": "Original"}, headers=root)
        client.post(f"/groups/{slug}/scopes", json={"allowed_modules": ["docs"], "module_scopes": {"docs": {"categories": ["a"]}}}, headers=root)
        for u in (u1, u2):
            client.post(f"/groups/{slug}/members", json={"user": u}, headers=root)

        g = next(x for x in client.get("/groups", headers=root).json() if x["slug"] == slug)
        assert sorted(g["members"]) == sorted([u1, u2])
        assert g["allowed_modules"] == ["docs"] and g["module_scopes"] == {"docs": {"categories": ["a"]}}

        assert client.patch(f"/groups/{slug}", json={"name": "Renamed"}, headers=root).json()["name"] == "Renamed"
        assert client.delete(f"/groups/{slug}/members/{u1}", headers=root).status_code == 200
        assert client.delete(f"/groups/{slug}/members/{u1}", headers=root).status_code == 404
        g = next(x for x in client.get("/groups", headers=root).json() if x["slug"] == slug)
        assert g["members"] == [u2]

        assert client.delete(f"/groups/{slug}", headers=root).status_code == 200
        assert client.delete(f"/groups/{slug}", headers=root).status_code == 404
        assert all(x["slug"] != slug for x in client.get("/groups", headers=root).json())
        assert any(u["name"] == u2 for u in client.get("/users", headers=root).json())  # users stay

    def test_group_without_scopes_lists_null_modules(self, client, root):
        slug = _unique("pg")
        client.post("/groups", json={"slug": slug, "name": "Bare"}, headers=root)
        g = next(x for x in client.get("/groups", headers=root).json() if x["slug"] == slug)
        assert g["allowed_modules"] is None and g["module_scopes"] == {} and g["members"] == []

    def test_unknown_group_is_404(self, client, root):
        assert client.patch("/groups/nope-nope", json={"name": "x"}, headers=root).status_code == 404
        assert client.delete("/groups/nope-nope/members/someone", headers=root).status_code == 404
