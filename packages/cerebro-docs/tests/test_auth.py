"""Tests for token auth (`cerebro_docs.auth`) against the shared `cerebro_auth`
schema (ecosistema-cerebro.md SS13, auth unification).

`TestPrincipal*` and `test_hash_token_*`/`test_generate_token_*` are unit tests over
pure functions/dataclasses (no I/O) - same spirit as cerebro-memory/tests/test_auth.py.

Everything else needs a real Postgres (`cerebro_auth.*` tables, applied by
conftest.py's `_apply_auth_schema` before this module is even collected, and the
full FastAPI dependency chain) and is automatically skipped if DATABASE_URL doesn't
respond. Token issuance now lives exclusively in the `cerebro-auth` service -- there
is no `/tokens` endpoint here anymore -- so these tests build rows directly in
`cerebro_auth.api_tokens`/`users`/`groups`/`user_groups`/`group_scopes` via raw SQL
helpers below.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_docs.api import create_app
from cerebro_docs.auth import Principal, generate_token, hash_token
from cerebro_docs.config import get_settings

# --------------------------------------------------------------------------- unit: Principal


def test_principal_has_scope():
    p = Principal(
        name="x", scopes=frozenset({"read", "write"}), access_level="user", user_id=None,
        allowed_categories=None, owner_filter=None,
    )
    assert p.has_scope("read")
    assert p.has_scope("write")
    assert not p.has_scope("admin")


def test_principal_category_allowed_none_means_all():
    p = Principal(
        name="x", scopes=frozenset({"read"}), access_level="user", user_id=None,
        allowed_categories=None, owner_filter=None,
    )
    assert p.category_allowed("cualquiera")
    assert p.category_allowed(None)


def test_principal_category_allowed_restricted():
    p = Principal(
        name="x", scopes=frozenset({"read"}), access_level="user", user_id=None,
        allowed_categories=frozenset({"ecosistema", "notas"}), owner_filter=None,
    )
    assert p.category_allowed("ecosistema")
    assert not p.category_allowed("finanzas")
    assert p.category_allowed(None)


def test_principal_filter_slugs_unrestricted_is_identity():
    p = Principal(
        name="x", scopes=frozenset({"read"}), access_level="user", user_id=None,
        allowed_categories=None, owner_filter=None,
    )
    slugs = ["a", "b", "c"]
    assert p.filter_slugs(slugs) == slugs


def test_principal_filter_slugs_restricted():
    p = Principal(
        name="x", scopes=frozenset({"read"}), access_level="user", user_id=None,
        allowed_categories=frozenset({"a", "c"}), owner_filter=None,
    )
    assert p.filter_slugs(["a", "b", "c", "d"]) == ["a", "c"]


def test_principal_root_has_every_scope_and_no_category_restriction():
    p = Principal(
        name="root", scopes=frozenset({"read", "write", "admin"}), access_level="admin", user_id=None,
        allowed_categories=None, owner_filter=None, is_root=True,
    )
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


# --------------------------------------------------------------------------- integration: fixtures


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


def _make_document(client, headers, category: str, *, title="Documento de prueba", content="contenido", slug=None):
    body = {"title": title, "content": content, "category": category}
    if slug is not None:
        body["slug"] = slug
    resp = client.post("/documents", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- integration: cerebro_auth fixtures
#
# Raw SQL helpers against the shared schema -- with token/user/group management no
# longer exposed by this service at all, this is how tests set up the rows that
# `get_principal` (auth.py) reads.


async def _connect(dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn=dsn)


async def _insert_user(conn: asyncpg.Connection, *, name: str, access_level: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO cerebro_auth.users (id, name, email, access_level, password_hash)
        VALUES ($1, $2, $3, $4, 'x')
        """,
        user_id,
        name,
        f"{name}-{user_id.hex[:8]}@example.com",
        access_level,
    )
    return user_id


async def _insert_group(
    conn: asyncpg.Connection, *, slug: str, allowed_modules: list[str] | None, module_scopes: dict | None
) -> uuid.UUID:
    group_id = uuid.uuid4()
    await conn.execute("INSERT INTO cerebro_auth.groups (id, slug, name) VALUES ($1, $2, $3)", group_id, slug, slug)
    await conn.execute(
        "INSERT INTO cerebro_auth.group_scopes (group_id, allowed_modules, module_scopes) VALUES ($1, $2, $3::jsonb)",
        group_id,
        allowed_modules,
        json.dumps(module_scopes) if module_scopes is not None else "{}",
    )
    return group_id


async def _insert_user_group(conn: asyncpg.Connection, *, user_id: uuid.UUID, group_id: uuid.UUID) -> None:
    await conn.execute("INSERT INTO cerebro_auth.user_groups (user_id, group_id) VALUES ($1, $2)", user_id, group_id)


async def _insert_token(
    conn: asyncpg.Connection,
    *,
    name: str,
    user_id: uuid.UUID | None,
    scopes: list[str],
    access_level: str | None,
    allowed_modules: list[str] | None,
    module_scopes: dict | None,
) -> str:
    plaintext = generate_token()
    await conn.execute(
        """
        INSERT INTO cerebro_auth.api_tokens
            (token_hash, name, user_id, scopes, access_level, allowed_modules, module_scopes)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        """,
        hash_token(plaintext),
        name,
        user_id,
        scopes,
        access_level,
        allowed_modules,
        json.dumps(module_scopes) if module_scopes is not None else "{}",
    )
    return plaintext


def _make_service_token(
    dsn: str, *, scopes: list[str], access_level: str, allowed_modules: list[str] | None = None,
    module_scopes: dict | None = None, name: str | None = None,
) -> str:
    """A user_id=NULL (service/root-adjacent) token."""
    name = name or f"test-svc-{uuid.uuid4().hex[:8]}"

    async def _do() -> str:
        conn = await _connect(dsn)
        try:
            return await _insert_token(
                conn, name=name, user_id=None, scopes=scopes, access_level=access_level,
                allowed_modules=allowed_modules, module_scopes=module_scopes,
            )
        finally:
            await conn.close()

    return asyncio.run(_do())


def _make_user_token(
    dsn: str, *, access_level: str, scopes: list[str], allowed_modules: list[str] | None = None,
    module_scopes: dict | None = None, name: str | None = None,
) -> tuple[str, uuid.UUID]:
    """A token linked to a fresh `cerebro_auth.users` row of the given access_level.

    Returns (plaintext_token, user_id)."""
    name = name or f"test-user-{uuid.uuid4().hex[:8]}"

    async def _do() -> tuple[str, uuid.UUID]:
        conn = await _connect(dsn)
        try:
            user_id = await _insert_user(conn, name=name, access_level=access_level)
            token = await _insert_token(
                conn, name=name, user_id=user_id, scopes=scopes, access_level=None,
                allowed_modules=allowed_modules, module_scopes=module_scopes,
            )
            return token, user_id
        finally:
            await conn.close()

    return asyncio.run(_do())


def _make_group(dsn: str, *, allowed_modules: list[str] | None, module_scopes: dict | None = None) -> uuid.UUID:
    slug = f"test-group-{uuid.uuid4().hex[:8]}"

    async def _do() -> uuid.UUID:
        conn = await _connect(dsn)
        try:
            return await _insert_group(conn, slug=slug, allowed_modules=allowed_modules, module_scopes=module_scopes)
        finally:
            await conn.close()

    return asyncio.run(_do())


def _join_group(dsn: str, *, user_id: uuid.UUID, group_id: uuid.UUID) -> None:
    async def _do() -> None:
        conn = await _connect(dsn)
        try:
            await _insert_user_group(conn, user_id=user_id, group_id=group_id)
        finally:
            await conn.close()

    asyncio.run(_do())


def _revoke_token(dsn: str, plaintext: str) -> None:
    async def _do() -> None:
        conn = await _connect(dsn)
        try:
            await conn.execute(
                "UPDATE cerebro_auth.api_tokens SET revoked_at = now() WHERE token_hash = $1", hash_token(plaintext)
            )
        finally:
            await conn.close()

    asyncio.run(_do())


# --------------------------------------------------------------------------- integration: root token


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


# --------------------------------------------------------------------------- integration: /tokens is gone


class TestTokensRoutesRemoved:
    """Token management moved entirely to `cerebro-auth` - these routes no longer
    exist in cerebro-docs at all."""

    def test_post_tokens_is_404(self, client, root_headers):
        resp = client.post("/tokens", json={"name": "x", "scopes": ["read"]}, headers=root_headers)
        assert resp.status_code == 404, resp.text

    def test_get_tokens_is_404(self, client, root_headers):
        resp = client.get("/tokens", headers=root_headers)
        assert resp.status_code == 404, resp.text

    def test_delete_tokens_name_is_404(self, client, root_headers):
        resp = client.delete("/tokens/whatever", headers=root_headers)
        assert resp.status_code == 404, resp.text


# --------------------------------------------------------------------------- integration: shared-schema resolution


class TestSharedSchemaResolution:
    def test_revoked_shared_token_is_401(self, client):
        settings = get_settings()
        # A service token (no user) can never have NULL allowed_modules (the DB
        # CHECK requires it explicitly when there's no user/group to inherit from)
        # -- use a user-tied admin token instead, same as the "inherits all
        # modules" test below, since only user-tied tokens can omit it.
        token, _ = _make_user_token(settings.database_url, access_level="admin", scopes=["read"], allowed_modules=None)
        _revoke_token(settings.database_url, token)
        resp = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, resp.text

    def test_token_without_docs_module_is_forbidden(self, client):
        settings = get_settings()
        token = _make_service_token(
            settings.database_url, scopes=["read"], access_level="user", allowed_modules=["memory"]
        )
        resp = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, resp.text

    def test_admin_access_level_inherits_docs_module_by_default(self, client):
        settings = get_settings()
        # Service tokens (no user) can't have NULL allowed_modules -- only a
        # user-tied token can omit it and inherit "all modules" as an admin.
        token, _ = _make_user_token(
            settings.database_url, access_level="admin", scopes=["read"], allowed_modules=None
        )
        resp = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text

    def test_admin_access_level_synthesizes_admin_service_scope(self, client, root_headers):
        """`cerebro_auth.api_tokens.scopes` only ever holds 'read'/'write' -
        'admin' is an access_level in the shared schema, not a token scope - so
        DELETE /categories (admin-scope-gated) must still work for an admin
        access_level token even though 'admin' never appears in its `scopes`
        column."""
        slug = _make_category(client, root_headers)
        settings = get_settings()
        token, _ = _make_user_token(
            settings.database_url, access_level="admin", scopes=["read", "write"], allowed_modules=None
        )
        resp = client.delete(f"/categories/{slug}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text

    def test_owner_with_no_groups_and_no_token_scope_is_forbidden(self, client):
        """SS13: no groups + no token-level allowed_modules -> empty effective
        access (a misconfiguration, never a permissive default)."""
        settings = get_settings()
        token, _user_id = _make_user_token(
            settings.database_url, access_level="owner", scopes=["read"], allowed_modules=None
        )
        resp = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, resp.text

    def test_owner_inherits_allowed_modules_from_group(self, client):
        settings = get_settings()
        group_id = _make_group(settings.database_url, allowed_modules=["docs"])
        token, user_id = _make_user_token(
            settings.database_url, access_level="owner", scopes=["read"], allowed_modules=None
        )
        _join_group(settings.database_url, user_id=user_id, group_id=group_id)

        resp = client.get("/categories", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- integration: scope enforcement


class TestScopeEnforcement:
    def test_read_only_token_cannot_write(self, client):
        settings = get_settings()
        token, _ = _make_user_token(
            settings.database_url, access_level="user", scopes=["read"], allowed_modules=["docs"]
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/categories", json={"slug": f"blocked-{uuid.uuid4().hex[:8]}", "name": "x"}, headers=headers
        )
        assert resp.status_code == 403, resp.text

    def test_write_only_token_cannot_read(self, client):
        settings = get_settings()
        token, _ = _make_user_token(
            settings.database_url, access_level="user", scopes=["write"], allowed_modules=["docs"]
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/categories", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_delete_category_requires_admin_not_just_write(self, client, root_headers):
        slug = _make_category(client, root_headers)
        settings = get_settings()
        token, _ = _make_user_token(
            settings.database_url, access_level="user", scopes=["read", "write"], allowed_modules=["docs"]
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.delete(f"/categories/{slug}", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_token_name_overrides_x_agent_name_header_in_created_by(self, client, root_headers):
        slug = _make_category(client, root_headers)
        settings = get_settings()
        name = f"test-token-identity-{uuid.uuid4().hex[:8]}"
        token, _ = _make_user_token(
            settings.database_url, access_level="user", scopes=["read", "write"], allowed_modules=["docs"], name=name,
        )
        headers = {"Authorization": f"Bearer {token}", "X-Agent-Name": "someone-else-entirely"}

        resp = client.post(
            "/documents",
            json={"title": "Doc de prueba", "content": "contenido de prueba", "category": slug},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["created_by"] == name


# --------------------------------------------------------------------------- integration: allowed_categories (docs module_scopes)


class TestAllowedCategoriesEnforcement:
    def test_write_outside_allowed_categories_is_403(self, client, root_headers):
        allowed_slug = _make_category(client, root_headers)
        other_slug = _make_category(client, root_headers)
        settings = get_settings()
        token, _ = _make_user_token(
            settings.database_url, access_level="user", scopes=["read", "write"], allowed_modules=["docs"],
            module_scopes={"docs": {"categories": [allowed_slug]}},
        )
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
        settings = get_settings()
        token, _ = _make_user_token(
            settings.database_url, access_level="user", scopes=["read"], allowed_modules=["docs"],
            module_scopes={"docs": {"categories": [allowed_slug]}},
        )
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

        settings = get_settings()
        token, _ = _make_user_token(
            settings.database_url, access_level="user", scopes=["read"], allowed_modules=["docs"],
            module_scopes={"docs": {"categories": [allowed_slug]}},
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/documents", params={"limit": 100}, headers=headers)
        assert resp.status_code == 200, resp.text
        categories_seen = {d["category"] for d in resp.json()}
        assert categories_seen <= {allowed_slug}


# --------------------------------------------------------------------------- integration: ownership filter


class TestOwnershipFilter:
    def test_user_level_token_only_sees_what_it_created(self, client, root_headers):
        settings = get_settings()
        cat = _make_category(client, root_headers)

        token_a, user_a = _make_user_token(
            settings.database_url, access_level="user", scopes=["read", "write"], allowed_modules=["docs"]
        )
        token_b, _user_b = _make_user_token(
            settings.database_url, access_level="user", scopes=["read", "write"], allowed_modules=["docs"]
        )
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        doc_a = _make_document(client, headers_a, cat, title="De A", slug=f"de-a-{uuid.uuid4().hex[:6]}")
        doc_b = _make_document(client, headers_b, cat, title="De B", slug=f"de-b-{uuid.uuid4().hex[:6]}")

        assert doc_a["owner_user_id"] == str(user_a)

        listed_a = client.get("/documents", params={"category": cat, "limit": 100}, headers=headers_a)
        assert listed_a.status_code == 200, listed_a.text
        ids_a = {d["id"] for d in listed_a.json()}
        assert doc_a["id"] in ids_a
        assert doc_b["id"] not in ids_a

        # the exact-path route also respects ownership - reading a document by
        # its exact known path must not bypass it.
        get_b_as_a = client.get(f"/documents/{cat}/{doc_b['slug']}", headers=headers_a)
        assert get_b_as_a.status_code == 404, get_b_as_a.text

        get_a_as_a = client.get(f"/documents/{cat}/{doc_a['slug']}", headers=headers_a)
        assert get_a_as_a.status_code == 200, get_a_as_a.text

    def test_owner_level_token_sees_groupmates_content_not_outsiders(self, client, root_headers):
        settings = get_settings()
        cat = _make_category(client, root_headers)
        dsn = settings.database_url

        group_id = _make_group(dsn, allowed_modules=["docs"])

        creator_token, creator_id = _make_user_token(
            dsn, access_level="user", scopes=["read", "write"], allowed_modules=["docs"]
        )
        _join_group(dsn, user_id=creator_id, group_id=group_id)

        owner_token, owner_id = _make_user_token(dsn, access_level="owner", scopes=["read"], allowed_modules=None)
        _join_group(dsn, user_id=owner_id, group_id=group_id)

        outsider_token, _outsider_id = _make_user_token(
            dsn, access_level="user", scopes=["read", "write"], allowed_modules=["docs"]
        )

        creator_headers = {"Authorization": f"Bearer {creator_token}"}
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        outsider_headers = {"Authorization": f"Bearer {outsider_token}"}

        doc_mate = _make_document(client, creator_headers, cat, title="De companero", slug=f"de-mate-{uuid.uuid4().hex[:6]}")
        doc_outsider = _make_document(client, outsider_headers, cat, title="De ajeno", slug=f"de-ajeno-{uuid.uuid4().hex[:6]}")

        listed = client.get("/documents", params={"category": cat, "limit": 100}, headers=owner_headers)
        assert listed.status_code == 200, listed.text
        ids = {d["id"] for d in listed.json()}
        assert doc_mate["id"] in ids
        assert doc_outsider["id"] not in ids

    def test_admin_and_root_see_everything_regardless_of_owner(self, client, root_headers):
        settings = get_settings()
        cat = _make_category(client, root_headers)

        token, _user_id = _make_user_token(
            settings.database_url, access_level="user", scopes=["read", "write"], allowed_modules=["docs"]
        )
        headers = {"Authorization": f"Bearer {token}"}
        doc = _make_document(client, headers, cat, title="Privado", slug=f"privado-{uuid.uuid4().hex[:6]}")

        listed = client.get("/documents", params={"category": cat, "limit": 100}, headers=root_headers)
        assert listed.status_code == 200, listed.text
        assert doc["id"] in {d["id"] for d in listed.json()}

        direct = client.get(f"/documents/{cat}/{doc['slug']}", headers=root_headers)
        assert direct.status_code == 200, direct.text

    def test_slug_redirect_still_respects_ownership(self, client, root_headers):
        settings = get_settings()
        cat_a = _make_category(client, root_headers)
        cat_b = _make_category(client, root_headers)
        dsn = settings.database_url

        token, _user_id = _make_user_token(dsn, access_level="user", scopes=["read", "write"], allowed_modules=["docs"])
        outsider_token, _outsider_id = _make_user_token(
            dsn, access_level="user", scopes=["read", "write"], allowed_modules=["docs"]
        )
        headers = {"Authorization": f"Bearer {token}"}
        outsider_headers = {"Authorization": f"Bearer {outsider_token}"}

        doc = _make_document(client, headers, cat_a, title="Se mueve", slug="se-mueve")
        moved = client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": doc["content"], "category": cat_b},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text

        # the owner can still reach it via the OLD (redirected) route
        via_redirect_owner = client.get(f"/documents/{cat_a}/se-mueve", headers=headers)
        assert via_redirect_owner.status_code == 200, via_redirect_owner.text
        assert via_redirect_owner.json()["redirected_from"] == {"category": cat_a, "slug": "se-mueve"}

        # a different user, even knowing the exact old route, is blocked by
        # ownership - the redirect fallback must ALSO respect owner_filter
        via_redirect_outsider = client.get(f"/documents/{cat_a}/se-mueve", headers=outsider_headers)
        assert via_redirect_outsider.status_code == 404, via_redirect_outsider.text
