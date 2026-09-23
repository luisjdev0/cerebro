"""Integration tests: `slug_redirects` (luisjdev-pendientes/ecosistema-cerebro,
"Slug redirects"). Automatically skipped if DATABASE_URL doesn't respond (same
pattern as test_documents.py).
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_docs.api import create_app
from cerebro_docs.config import get_settings


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
def auth_headers():
    return {"Authorization": f"Bearer {get_settings().api_token}"}


def _make_category(client, auth_headers, **overrides) -> str:
    slug = overrides.pop("slug", f"test-cat-{uuid.uuid4().hex[:8]}")
    body = {"slug": slug, "name": overrides.pop("name", "Categoria de prueba"), **overrides}
    resp = client.post("/categories", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return slug


def _make_document(client, auth_headers, category: str, *, title="Documento de prueba", content="contenido inicial", slug=None):
    body = {"title": title, "content": content, "category": category}
    if slug is not None:
        body["slug"] = slug
    resp = client.post("/documents", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestDocumentSlugRename:
    def test_renaming_slug_leaves_a_redirect(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, slug="slug-viejo")

        resp = client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": doc["content"], "category": cat, "slug": "slug-nuevo"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        old_route = client.get(f"/documents/{cat}/slug-viejo", headers=auth_headers)
        assert old_route.status_code == 200, old_route.text
        assert old_route.json()["id"] == doc["id"]
        assert old_route.json()["slug"] == "slug-nuevo"
        assert old_route.json()["redirected_from"] == {"category": cat, "slug": "slug-viejo"}

    def test_moving_category_leaves_a_redirect(self, client, auth_headers):
        cat_a = _make_category(client, auth_headers)
        cat_b = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat_a, slug="se-muda")

        resp = client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": doc["content"], "category": cat_b, "slug": "se-muda"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        old_route = client.get(f"/documents/{cat_a}/se-muda", headers=auth_headers)
        assert old_route.status_code == 200, old_route.text
        assert old_route.json()["category"] == cat_b
        assert old_route.json()["redirected_from"] == {"category": cat_a, "slug": "se-muda"}

    def test_no_redirect_row_when_slug_and_category_unchanged(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, slug="sin-cambios")
        resp = client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": "contenido nuevo", "category": cat, "slug": "sin-cambios"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        direct = client.get(f"/documents/{cat}/sin-cambios", headers=auth_headers)
        assert direct.json()["redirected_from"] is None

    def test_never_chains_both_old_routes_resolve_after_two_renames(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, slug="version-a")
        client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": doc["content"], "category": cat, "slug": "version-b"},
            headers=auth_headers,
        )
        client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": doc["content"], "category": cat, "slug": "version-c"},
            headers=auth_headers,
        )

        route_a = client.get(f"/documents/{cat}/version-a", headers=auth_headers)
        route_b = client.get(f"/documents/{cat}/version-b", headers=auth_headers)
        route_c = client.get(f"/documents/{cat}/version-c", headers=auth_headers)
        assert route_a.status_code == route_b.status_code == route_c.status_code == 200
        for r in (route_a, route_b, route_c):
            assert r.json()["id"] == doc["id"]
        assert route_a.json()["slug"] == route_b.json()["slug"] == route_c.json()["slug"] == "version-c"

    def test_real_document_always_wins_over_redirect(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        moved = _make_document(client, auth_headers, cat, slug="coordenada-disputada")
        client.patch(
            f"/documents/{moved['id']}",
            json={"title": moved["title"], "content": moved["content"], "category": cat, "slug": "ya-no-esta-aqui"},
            headers=auth_headers,
        )
        # a NEW document claims the old coordinate left behind by the redirect
        real = _make_document(client, auth_headers, cat, title="El de verdad", slug="coordenada-disputada")

        resp = client.get(f"/documents/{cat}/coordenada-disputada", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == real["id"]
        assert resp.json()["redirected_from"] is None

    def test_redirect_never_appears_in_listings(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, slug="listado-viejo")
        client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": doc["content"], "category": cat, "slug": "listado-nuevo"},
            headers=auth_headers,
        )
        listed = client.get("/documents", params={"category": cat}, headers=auth_headers)
        slugs = {d["slug"] for d in listed.json()}
        assert "listado-nuevo" in slugs
        assert "listado-viejo" not in slugs


class TestCategoryRenameBulkRedirect:
    def test_renaming_category_redirects_every_document_in_it(self, client, auth_headers):
        old_cat = _make_category(client, auth_headers)
        doc1 = _make_document(client, auth_headers, old_cat, slug="uno")
        doc2 = _make_document(client, auth_headers, old_cat, slug="dos")

        new_cat = f"{old_cat}-nueva"
        resp = client.patch(f"/categories/{old_cat}", json={"slug": new_cat}, headers=auth_headers)
        assert resp.status_code == 200, resp.text

        for doc, slug in ((doc1, "uno"), (doc2, "dos")):
            old_route = client.get(f"/documents/{old_cat}/{slug}", headers=auth_headers)
            assert old_route.status_code == 200, old_route.text
            assert old_route.json()["id"] == doc["id"]
            assert old_route.json()["redirected_from"] == {"category": old_cat, "slug": slug}

    def test_renaming_category_twice_never_chains(self, client, auth_headers):
        cat_a = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat_a, slug="doc")

        cat_b = f"{cat_a}-b"
        client.patch(f"/categories/{cat_a}", json={"slug": cat_b}, headers=auth_headers)
        cat_c = f"{cat_a}-c"
        client.patch(f"/categories/{cat_b}", json={"slug": cat_c}, headers=auth_headers)

        route_a = client.get(f"/documents/{cat_a}/doc", headers=auth_headers)
        route_b = client.get(f"/documents/{cat_b}/doc", headers=auth_headers)
        route_c = client.get(f"/documents/{cat_c}/doc", headers=auth_headers)
        assert route_a.status_code == route_b.status_code == route_c.status_code == 200
        for r in (route_a, route_b, route_c):
            assert r.json()["id"] == doc["id"]
            assert r.json()["category"] == cat_c
