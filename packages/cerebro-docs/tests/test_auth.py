"""Tests for token auth with scopes (`cerebro_docs.auth`).

`TestPrincipal*` and `test_hash_token_*`/`test_generate_token_*` are unit tests over
pure functions/dataclasses (no I/O) - mismo espiritu que
cerebro-memory/tests/test_auth.py.

El resto necesita Postgres real (tabla `api_tokens`, cadena completa de dependencias
FastAPI) y se salta automaticamente si DATABASE_URL no responde.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_docs.api import create_app
from cerebro_docs.auth import Principal, generate_token, hash_token
from cerebro_docs.config import get_settings

# --------------------------------------------------------------------------- unit: Principal


def test_principal_has_scope():
    p = Principal(name="x", scopes=frozenset({"read", "write"}), allowed_categories=None)
    assert p.has_scope("read")
    assert p.has_scope("write")
    assert not p.has_scope("admin")


def test_principal_category_allowed_none_means_all():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_categories=None)
    assert p.category_allowed("cualquiera")
    assert p.category_allowed(None)


def test_principal_category_allowed_restricted():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_categories=frozenset({"ecosistema", "notas"}))
    assert p.category_allowed("ecosistema")
    assert not p.category_allowed("finanzas")
    assert p.category_allowed(None)


def test_principal_filter_slugs_unrestricted_is_identity():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_categories=None)
    slugs = ["a", "b", "c"]
    assert p.filter_slugs(slugs) == slugs


def test_principal_filter_slugs_restricted():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_categories=frozenset({"a", "c"}))
    assert p.filter_slugs(["a", "b", "c", "d"]) == ["a", "c"]


def test_principal_root_has_every_scope_and_no_category_restriction():
    p = Principal(name="root", scopes=frozenset({"read", "write", "admin"}), allowed_categories=None, is_root=True)
    assert p.is_root
    for scope in ("read", "write", "admin"):
        assert p.has_scope(scope)
    assert p.category_allowed("literalmente-cualquier-cosa")


# --------------------------------------------------------------------------- unit: token helpers


def test_hash_token_is_deterministic_sha256_hex():
    h1 = hash_token("cbrd_abc123")
    h2 = hash_token("cbrd_abc123")
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_hash_token_differs_for_different_input():
    assert hash_token("cbrd_a") != hash_token("cbrd_b")


def test_generate_token_has_prefix_and_is_unique():
    t1 = generate_token()
    t2 = generate_token()
    assert t1.startswith("cbrd_")
    assert t2.startswith("cbrd_")
    assert t1 != t2
    assert len(t1) > len("cbrd_") + 20


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


def _make_category(client, root_headers) -> str:
    slug = f"test-auth-{uuid.uuid4().hex[:8]}"
    resp = client.post("/categories", json={"slug": slug, "name": "Test auth category"}, headers=root_headers)
    assert resp.status_code == 201, resp.text
    return slug


def _create_token(client, root_headers, *, name, scopes, categories=None) -> str:
    body = {"name": name, "scopes": scopes}
    if categories is not None:
        body["allowed_categories"] = categories
    resp = client.post("/tokens", json=body, headers=root_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


class TestRootTokenCompat:
    def test_root_token_works_for_every_scope(self, client, root_headers):
        resp = client.get("/categories", headers=root_headers)
        assert resp.status_code == 200, resp.text

    def test_missing_auth_header_is_401(self, client):
        resp = client.get("/categories")
        assert resp.status_code == 401, resp.text

    def test_garbage_bearer_token_is_401(self, client):
        resp = client.get("/categories", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401, resp.text


class TestTokenLifecycle:
    def test_create_list_revoke_roundtrip(self, client, root_headers):
        name = f"test-token-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"])
        assert token.startswith("cbrd_")

        resp = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text

        listed = client.get("/tokens", headers=root_headers)
        assert listed.status_code == 200, listed.text
        names = {t["name"] for t in listed.json()}
        assert name in names
        for row in listed.json():
            assert "token" not in row
            assert "token_hash" not in row

        revoke_resp = client.delete(f"/tokens/{name}", headers=root_headers)
        assert revoke_resp.status_code == 200, revoke_resp.text
        assert revoke_resp.json()["revoked_at"] is not None

        resp = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, resp.text

    def test_duplicate_active_name_is_409(self, client, root_headers):
        name = f"test-token-dup-{uuid.uuid4().hex[:8]}"
        _create_token(client, root_headers, name=name, scopes=["read"])
        resp = client.post("/tokens", json={"name": name, "scopes": ["read"]}, headers=root_headers)
        assert resp.status_code == 409, resp.text

    def test_invalid_scope_is_422(self, client, root_headers):
        resp = client.post(
            "/tokens",
            json={"name": f"test-bad-scope-{uuid.uuid4().hex[:8]}", "scopes": ["superuser"]},
            headers=root_headers,
        )
        assert resp.status_code == 422, resp.text

    def test_revoking_unknown_name_is_404(self, client, root_headers):
        resp = client.delete(f"/tokens/does-not-exist-{uuid.uuid4().hex[:8]}", headers=root_headers)
        assert resp.status_code == 404, resp.text

    def test_token_management_requires_admin_scope(self, client, root_headers):
        name = f"test-token-nonadmin-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/tokens", headers=headers).status_code == 403
        assert client.post("/tokens", json={"name": "x", "scopes": ["read"]}, headers=headers).status_code == 403
        assert client.delete(f"/tokens/{name}", headers=headers).status_code == 403


class TestTokenValueField:
    """`value` (ecosistema-cerebro.md SS13, tokens transversales): admin puede pasar
    el secreto en claro para que el servidor lo hashee, en vez de generar uno -
    permite registrar el MISMO token en cerebro-memory y cerebro-docs. Idempotente
    por nombre: reintentar con el mismo `value` no duplica ni falla."""

    def test_create_with_value_uses_that_exact_secret(self, client, root_headers):
        name = f"test-token-value-{uuid.uuid4().hex[:8]}"
        provided = f"cbrd_transversal-{uuid.uuid4().hex}"
        resp = client.post(
            "/tokens", json={"name": name, "scopes": ["read"], "value": provided}, headers=root_headers
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["token"] == provided

        auth = client.get("/categories", headers={"Authorization": f"Bearer {provided}"})
        assert auth.status_code == 200, auth.text

    def test_recreating_same_name_and_value_is_idempotent_not_409(self, client, root_headers):
        name = f"test-token-idem-{uuid.uuid4().hex[:8]}"
        provided = f"cbrd_idempotent-{uuid.uuid4().hex}"
        first = client.post(
            "/tokens", json={"name": name, "scopes": ["read"], "value": provided}, headers=root_headers
        )
        assert first.status_code == 201, first.text

        second = client.post(
            "/tokens", json={"name": name, "scopes": ["read"], "value": provided}, headers=root_headers
        )
        assert second.status_code == 201, second.text
        assert second.json()["token"] == provided
        assert second.json()["id"] == first.json()["id"]

        listed = client.get("/tokens", headers=root_headers)
        names = [t["name"] for t in listed.json()]
        assert names.count(name) == 1  # no duplicate row

    def test_recreating_same_name_with_different_value_is_still_409(self, client, root_headers):
        name = f"test-token-conflict-{uuid.uuid4().hex[:8]}"
        client.post(
            "/tokens",
            json={"name": name, "scopes": ["read"], "value": f"cbrd_a-{uuid.uuid4().hex}"},
            headers=root_headers,
        )
        resp = client.post(
            "/tokens",
            json={"name": name, "scopes": ["read"], "value": f"cbrd_b-{uuid.uuid4().hex}"},
            headers=root_headers,
        )
        assert resp.status_code == 409, resp.text


class TestScopeEnforcement:
    def test_read_only_token_cannot_write(self, client, root_headers):
        name = f"test-token-ro-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/categories", json={"slug": f"blocked-{uuid.uuid4().hex[:8]}", "name": "x"}, headers=headers
        )
        assert resp.status_code == 403, resp.text

    def test_write_only_token_cannot_read(self, client, root_headers):
        name = f"test-token-wo-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["write"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/categories", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_delete_category_requires_admin_not_just_write(self, client, root_headers):
        slug = _make_category(client, root_headers)
        name = f"test-token-del-rw-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.delete(f"/categories/{slug}", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_token_name_overrides_x_agent_name_header_in_created_by(self, client, root_headers):
        slug = _make_category(client, root_headers)
        name = f"test-token-identity-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}", "X-Agent-Name": "someone-else-entirely"}

        resp = client.post(
            "/documents",
            json={"title": "Doc de prueba", "content": "contenido de prueba", "category": slug},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["created_by"] == name


class TestAllowedCategoriesEnforcement:
    def test_write_outside_allowed_categories_is_403(self, client, root_headers):
        allowed_slug = _make_category(client, root_headers)
        other_slug = _make_category(client, root_headers)
        name = f"test-token-cat-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"], categories=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        ok = client.post(
            "/documents",
            json={"title": "dentro", "content": "contenido dentro", "category": allowed_slug},
            headers=headers,
        )
        assert ok.status_code == 201, ok.text

        blocked = client.post(
            "/documents",
            json={"title": "fuera", "content": "contenido fuera", "category": other_slug},
            headers=headers,
        )
        assert blocked.status_code == 403, blocked.text

    def test_explicit_read_outside_allowed_categories_is_403(self, client, root_headers):
        allowed_slug = _make_category(client, root_headers)
        other_slug = _make_category(client, root_headers)
        name = f"test-token-read-cat-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], categories=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/documents", params={"category": other_slug}, headers=headers)
        assert resp.status_code == 403, resp.text

    def test_unscoped_list_is_silently_narrowed_to_allowed_categories(self, client, root_headers):
        allowed_slug = _make_category(client, root_headers)
        other_slug = _make_category(client, root_headers)
        client.post(
            "/documents",
            json={"title": "en permitida", "content": "contenido", "category": allowed_slug},
            headers=root_headers,
        )
        client.post(
            "/documents",
            json={"title": "en ajena", "content": "contenido", "category": other_slug},
            headers=root_headers,
        )

        name = f"test-token-narrow-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], categories=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/documents", params={"limit": 100}, headers=headers)
        assert resp.status_code == 200, resp.text
        categories_seen = {d["category"] for d in resp.json()}
        assert categories_seen <= {allowed_slug}
