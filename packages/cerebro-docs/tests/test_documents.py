"""Integration tests: CRUD de categorias/documentos, unicidad, cascada, versionado,
concurrencia y busqueda full-text. Se saltan automaticamente si DATABASE_URL no
responde (mismo patron que cerebro-memory/tests/test_supersedence.py).
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_docs.api import create_app
from cerebro_docs.config import get_settings


async def _connect(dsn: str) -> asyncpg.Connection:
    """Conexion ad-hoc (fuera del pool de la app) para inspeccionar
    `document_versions` directamente - con el mismo search_path que usa `db.py`, ya
    que todo el servicio vive en el schema `cerebro_docs`, nunca en `public`."""
    return await asyncpg.connect(dsn=dsn, server_settings={"search_path": "cerebro_docs, public"})


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


# --------------------------------------------------------------------------- categories CRUD


class TestCategoriesCrud:
    def test_create_and_list_category(self, client, auth_headers):
        slug = _make_category(client, auth_headers, name="Ecosistema", description="algo")
        listed = client.get("/categories", headers=auth_headers)
        assert listed.status_code == 200, listed.text
        assert slug in {c["slug"] for c in listed.json()}

    def test_create_duplicate_category_slug_is_409(self, client, auth_headers):
        slug = _make_category(client, auth_headers)
        resp = client.post("/categories", json={"slug": slug, "name": "otra"}, headers=auth_headers)
        assert resp.status_code == 409, resp.text

    def test_rename_category_does_not_touch_documents_and_routes_still_resolve(self, client, auth_headers):
        old_slug = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, old_slug, title="Doc estable", slug="doc-estable")

        new_slug = f"{old_slug}-renamed"
        resp = client.patch(f"/categories/{old_slug}", json={"slug": new_slug}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["slug"] == new_slug

        # la ruta vieja ya no resuelve, la nueva si - mismo documento, mismo id.
        old_route = client.get(f"/documents/{old_slug}/doc-estable", headers=auth_headers)
        assert old_route.status_code == 404, old_route.text

        new_route = client.get(f"/documents/{new_slug}/doc-estable", headers=auth_headers)
        assert new_route.status_code == 200, new_route.text
        assert new_route.json()["id"] == doc["id"]
        assert new_route.json()["content"] == "contenido inicial"

    def test_delete_empty_category_succeeds_without_force(self, client, auth_headers):
        slug = _make_category(client, auth_headers)
        resp = client.delete(f"/categories/{slug}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"slug": slug, "status": "deleted", "documents_deleted": 0}

    def test_delete_category_with_documents_is_409_without_force(self, client, auth_headers):
        slug = _make_category(client, auth_headers)
        _make_document(client, auth_headers, slug)
        resp = client.delete(f"/categories/{slug}", headers=auth_headers)
        assert resp.status_code == 409, resp.text

    def test_delete_category_with_force_cascades_to_documents_and_versions(self, client, auth_headers):
        slug = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, slug)
        # deja al menos una version en el historial antes de la cascada
        client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": "contenido editado", "category": slug},
            headers=auth_headers,
        )

        resp = client.delete(f"/categories/{slug}", params={"force": True}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["documents_deleted"] == 1

        gone = client.get(f"/documents/{slug}/{doc['slug']}", headers=auth_headers)
        assert gone.status_code == 404, gone.text

    def test_delete_nonexistent_category_is_404(self, client, auth_headers):
        resp = client.delete(f"/categories/does-not-exist-{uuid.uuid4().hex[:8]}", headers=auth_headers)
        assert resp.status_code == 404, resp.text


# --------------------------------------------------------------------------- documents CRUD


class TestDocumentsCrud:
    def test_create_requires_existing_category(self, client, auth_headers):
        resp = client.post(
            "/documents",
            json={"title": "x", "content": "y", "category": f"no-existe-{uuid.uuid4().hex[:8]}"},
            headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text
        assert "docs_create_category" in resp.json()["detail"]

    def test_create_autogenerates_slug_from_title(self, client, auth_headers):
        slug = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, slug, title="Mi Título De Prueba", slug=None)
        assert doc["slug"] == "mi-titulo-de-prueba"

    def test_get_by_exact_route_resolves(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, slug="ruta-exacta")
        resp = client.get(f"/documents/{cat}/ruta-exacta", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == doc["id"]

    def test_get_nonexistent_document_is_404(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        resp = client.get(f"/documents/{cat}/no-existe", headers=auth_headers)
        assert resp.status_code == 404, resp.text

    def test_duplicate_slug_within_same_category_is_409(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        _make_document(client, auth_headers, cat, slug="mismo-slug")
        resp = client.post(
            "/documents",
            json={"title": "otro titulo", "content": "otro contenido", "category": cat, "slug": "mismo-slug"},
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text
        assert "docs_update" in resp.json()["detail"]

    def test_same_slug_in_different_categories_does_not_collide(self, client, auth_headers):
        cat_a = _make_category(client, auth_headers)
        cat_b = _make_category(client, auth_headers)
        doc_a = _make_document(client, auth_headers, cat_a, slug="mismo-slug-otra-cat")
        doc_b = _make_document(client, auth_headers, cat_b, slug="mismo-slug-otra-cat")
        assert doc_a["id"] != doc_b["id"]

    def test_delete_document(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat)
        resp = client.delete(f"/documents/{doc['id']}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert client.get(f"/documents/{cat}/{doc['slug']}", headers=auth_headers).status_code == 404


# --------------------------------------------------------------------------- versioning


class TestVersioning:
    def test_full_replace_creates_incrementing_version_snapshots(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, content="v1")

        r2 = client.patch(
            f"/documents/{doc['id']}", json={"title": doc["title"], "content": "v2", "category": cat}, headers=auth_headers
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["content"] == "v2"

        r3 = client.patch(
            f"/documents/{doc['id']}", json={"title": doc["title"], "content": "v3", "category": cat}, headers=auth_headers
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["content"] == "v3"

        # v1 y v2 deben seguir en document_versions con numeracion correcta (via psql,
        # ya que no hay endpoint de lectura de versiones en v1 - ver
        # ecosistema-cerebro.md SS12, "sin endpoint de restore en v1").
        settings = get_settings()

        async def _fetch_versions():
            conn = await _connect(settings.database_url)
            try:
                return await conn.fetch(
                    "SELECT version_number, content FROM document_versions WHERE document_id = $1 ORDER BY version_number",
                    uuid.UUID(doc["id"]),
                )
            finally:
                await conn.close()

        versions = asyncio.run(_fetch_versions())
        assert [v["version_number"] for v in versions] == [1, 2]
        assert [v["content"] for v in versions] == ["v1", "v2"]

    def test_section_patch_also_creates_a_version_snapshot(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, content="## Intro\ntexto original\n")

        resp = client.patch(
            f"/documents/{doc['id']}/section",
            json={"heading": "Intro", "operation": "replace", "body": "texto nuevo"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert "texto nuevo" in resp.json()["content"]

        settings = get_settings()

        async def _count_versions():
            conn = await _connect(settings.database_url)
            try:
                return await conn.fetchval(
                    "SELECT count(*) FROM document_versions WHERE document_id = $1", uuid.UUID(doc["id"])
                )
            finally:
                await conn.close()

        assert asyncio.run(_count_versions()) == 1

    def test_section_patch_heading_not_found_is_404(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, content="## Intro\nalgo\n")
        resp = client.patch(
            f"/documents/{doc['id']}/section",
            json={"heading": "No existe", "operation": "append", "body": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text

    def test_section_patch_ambiguous_heading_is_409(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, content="## Repetido\na\n## Repetido\nb\n")
        resp = client.patch(
            f"/documents/{doc['id']}/section",
            json={"heading": "Repetido", "operation": "append", "body": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text

    def test_section_patch_creates_heading_when_flagged(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat, content="## Intro\nalgo\n")
        resp = client.patch(
            f"/documents/{doc['id']}/section",
            json={
                "heading": "Seccion nueva",
                "operation": "append",
                "body": "contenido nuevo",
                "create_if_missing": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert "Seccion nueva" in resp.json()["content"]

    def test_move_document_to_another_category_validates_uniqueness_in_destination(self, client, auth_headers):
        cat_a = _make_category(client, auth_headers)
        cat_b = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat_a, slug="conflicto")
        _make_document(client, auth_headers, cat_b, slug="conflicto")

        resp = client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": doc["content"], "category": cat_b, "slug": "conflicto"},
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text

    def test_move_document_to_another_category_succeeds_when_no_conflict(self, client, auth_headers):
        cat_a = _make_category(client, auth_headers)
        cat_b = _make_category(client, auth_headers)
        doc = _make_document(client, auth_headers, cat_a, slug="movible")

        resp = client.patch(
            f"/documents/{doc['id']}",
            json={"title": doc["title"], "content": doc["content"], "category": cat_b},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["category"] == cat_b

        moved = client.get(f"/documents/{cat_b}/movible", headers=auth_headers)
        assert moved.status_code == 200, moved.text


# --------------------------------------------------------------------------- concurrency


class TestConcurrentUpdates:
    async def test_two_concurrent_full_replaces_both_survive_in_history(self):
        """Dos PATCH /documents/{id} casi simultaneos (asyncio.gather) no deben
        perder ninguna de las dos escrituras: Postgres serializa sobre el lock de
        `SELECT ... FOR UPDATE` (ecosistema-cerebro.md SS12) y cada uno archiva el
        contenido previo antes de aplicar el suyo, asi que el historial termina con
        AMBOS snapshots (el original + la primera escritura que gano la carrera), y
        el contenido final es una de las dos escrituras - ninguna se pierde en
        silencio.

        Corre como test async propio (en vez de usar el fixture `client` sincrono)
        para que la creacion del pool y las dos requests concurrentes compartan el
        MISMO event loop - asyncpg ata sus conexiones al loop donde se crearon, y
        `TestClient` sincrono las crea en un loop de fondo distinto al de
        `asyncio.gather` en el test.
        """
        import httpx

        settings = get_settings()
        # Chequeo de alcanzabilidad in-line (no la funcion `_db_reachable` de arriba,
        # que llama `asyncio.run()` internamente - no se puede anidar un event loop
        # dentro de otro, y este test YA corre dentro de uno via pytest-asyncio).
        try:
            probe = await asyncpg.connect(dsn=settings.database_url, timeout=8)
            await probe.close()
        except Exception:
            pytest.skip("Postgres not reachable at DATABASE_URL - run `docker compose up -d` first")

        app = create_app(settings)
        auth_headers = {"Authorization": f"Bearer {settings.api_token}"}

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                cat_slug = f"test-concurrency-{uuid.uuid4().hex[:8]}"
                cat_resp = await ac.post(
                    "/categories", json={"slug": cat_slug, "name": "Concurrencia"}, headers=auth_headers
                )
                assert cat_resp.status_code == 201, cat_resp.text

                doc_resp = await ac.post(
                    "/documents",
                    json={"title": "Doc concurrente", "content": "original", "category": cat_slug},
                    headers=auth_headers,
                )
                assert doc_resp.status_code == 201, doc_resp.text
                doc = doc_resp.json()

                async def _patch(content: str):
                    return await ac.patch(
                        f"/documents/{doc['id']}",
                        json={"title": doc["title"], "content": content, "category": cat_slug},
                        headers=auth_headers,
                    )

                r1, r2 = await asyncio.gather(_patch("concurrente-A"), _patch("concurrente-B"))
                assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)

                final = await ac.get(f"/documents/{cat_slug}/{doc['slug']}", headers=auth_headers)
                assert final.status_code == 200, final.text
                assert final.json()["content"] in {"concurrente-A", "concurrente-B"}

            pool = app.state.pool
            versions = await pool.fetch(
                "SELECT version_number, content FROM document_versions WHERE document_id = $1 ORDER BY version_number",
                uuid.UUID(doc["id"]),
            )

        # dos escrituras concurrentes sobre un documento con 0 versiones previas (la
        # creacion no cuenta) dejan exactamente 2 snapshots: el original, y el
        # contenido de quien gano la carrera de escritura.
        assert len(versions) == 2
        assert [v["version_number"] for v in versions] == [1, 2]
        contents = {v["content"] for v in versions}
        assert "original" in contents
        assert contents & {"concurrente-A", "concurrente-B"}


# --------------------------------------------------------------------------- search


class TestSearch:
    def test_search_finds_matching_document(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        _make_document(client, auth_headers, cat, title="Guia de despliegue en VPS", content="pasos para desplegar")
        resp = client.get("/documents", params={"q": "despliegue VPS", "category": cat}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        titles = {d["title"] for d in resp.json()}
        assert "Guia de despliegue en VPS" in titles

    def test_search_with_hostile_input_does_not_break_and_returns_no_match(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        _make_document(client, auth_headers, cat, title="Documento normal", content="contenido normal")

        for hostile in ["'; DROP TABLE documents--", "\" OR 1=1 --", "%; SELECT * FROM api_tokens; --"]:
            resp = client.get("/documents", params={"q": hostile, "category": cat}, headers=auth_headers)
            assert resp.status_code == 200, resp.text
            assert resp.json() == []

        # la tabla sigue intacta despues de los intentos hostiles
        still_there = client.get(f"/documents/{cat}/documento-normal", headers=auth_headers)
        assert still_there.status_code == 200, still_there.text

    def test_list_without_q_orders_by_updated_at_desc(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        first = _make_document(client, auth_headers, cat, title="Primero", slug="primero")
        second = _make_document(client, auth_headers, cat, title="Segundo", slug="segundo")

        resp = client.get("/documents", params={"category": cat}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        ids = [d["id"] for d in resp.json()]
        assert ids.index(second["id"]) < ids.index(first["id"])

    def test_pagination_limit_and_offset(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        for i in range(5):
            _make_document(client, auth_headers, cat, title=f"Doc {i}", slug=f"doc-{i}")

        page1 = client.get("/documents", params={"category": cat, "limit": 2, "offset": 0}, headers=auth_headers)
        page2 = client.get("/documents", params={"category": cat, "limit": 2, "offset": 2}, headers=auth_headers)
        assert page1.status_code == 200 and page2.status_code == 200
        assert len(page1.json()) == 2
        assert len(page2.json()) == 2
        ids_page1 = {d["id"] for d in page1.json()}
        ids_page2 = {d["id"] for d in page2.json()}
        assert ids_page1.isdisjoint(ids_page2)
