"""Tests for the Fase 3 grafo ligero (`cerebro_memory.graph`, plan_v2.md SS8).

`test_validate_relation_*` below are unit tests over `validate_relation`, a pure
function (no I/O), same spirit as tests/test_rrf.py and tests/test_context_engine.py.

Everything else here needs real Postgres behavior (uniqueness constraint, FK
existence checks, CASCADE on hard-delete, 1-hop direction, timeline ordering) and
lives in `TestEdgesIntegration` / `TestTimelineIntegration` below, which skip
automatically if DATABASE_URL is not reachable - same pattern as
tests/test_supersedence.py.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_memory.api import create_app
from cerebro_memory.config import get_settings
from cerebro_memory.graph import (
    RELATION_VOCAB,
    InvalidRelationError,
    validate_relation,
)

# --------------------------------------------------------------------------- unit: vocabulary


@pytest.mark.parametrize("relation", RELATION_VOCAB)
def test_validate_relation_accepts_every_vocab_word(relation):
    validate_relation(relation)  # must not raise


def test_validate_relation_rejects_unknown_word():
    with pytest.raises(InvalidRelationError):
        validate_relation("depends_on")


def test_validate_relation_rejects_supersedes_by_default():
    # 'supersedes' is a virtual, derived relation - never a value you can write to an
    # explicit edge, only a valid *filter* (allow_supersedes=True, used by
    # GET /memories/{id}/related).
    with pytest.raises(InvalidRelationError):
        validate_relation("supersedes")


def test_validate_relation_allows_supersedes_when_explicitly_permitted():
    validate_relation("supersedes", allow_supersedes=True)  # must not raise


def test_validate_relation_rejects_empty_string():
    with pytest.raises(InvalidRelationError):
        validate_relation("")


# --------------------------------------------------------------------------- integration: edges


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


def _make_context(client, auth_headers) -> str:
    slug = f"test-graph-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/contexts",
        json={"slug": slug, "name": "Test graph context", "kind": "domain"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return slug


def _make_memory(client, auth_headers, slug: str, content: str, type_: str = "semantic") -> str:
    resp = client.post(
        "/memories",
        json={"content": content, "context": slug, "type": type_},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestEdgesIntegration:
    def test_invalid_relation_is_rejected_with_422(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        a = _make_memory(client, auth_headers, slug, "memoria A")
        b = _make_memory(client, auth_headers, slug, "memoria B")

        resp = client.post(
            f"/memories/{a}/edges",
            json={"to_memory": b, "relation": "depends_on"},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text

    def test_self_link_is_rejected(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        a = _make_memory(client, auth_headers, slug, "memoria auto-referenciada")

        resp = client.post(
            f"/memories/{a}/edges",
            json={"to_memory": a, "relation": "relates_to"},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text

    def test_edge_to_nonexistent_memory_is_404(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        a = _make_memory(client, auth_headers, slug, "memoria existente")
        fake_id = str(uuid.uuid4())

        resp = client.post(
            f"/memories/{a}/edges",
            json={"to_memory": fake_id, "relation": "relates_to"},
            headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text

    def test_duplicate_edge_is_409(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        a = _make_memory(client, auth_headers, slug, "decision de migrar", "decision")
        b = _make_memory(client, auth_headers, slug, "el proveedor subio precios", "episodic")

        resp1 = client.post(
            f"/memories/{a}/edges",
            json={"to_memory": b, "relation": "caused_by"},
            headers=auth_headers,
        )
        assert resp1.status_code == 201, resp1.text

        resp2 = client.post(
            f"/memories/{a}/edges",
            json={"to_memory": b, "relation": "caused_by"},
            headers=auth_headers,
        )
        assert resp2.status_code == 409, resp2.text

        # same pair, DIFFERENT relation is allowed (unique triple, not unique pair)
        resp3 = client.post(
            f"/memories/{a}/edges",
            json={"to_memory": b, "relation": "relates_to"},
            headers=auth_headers,
        )
        assert resp3.status_code == 201, resp3.text

    def test_related_reports_both_directions_correctly(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        proc = _make_memory(client, auth_headers, slug, "como hacer deploy", "procedural")
        project = _make_memory(client, auth_headers, slug, "proyecto expense-tracker", "semantic")

        resp = client.post(
            f"/memories/{proc}/edges",
            json={"to_memory": project, "relation": "part_of", "note": "procedimiento del proyecto"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        edge_id = resp.json()["id"]

        # from proc's point of view: outgoing
        resp = client.get(f"/memories/{proc}/related", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        related = resp.json()["related"]
        matches = [r for r in related if r["edge_id"] == edge_id]
        assert len(matches) == 1
        assert matches[0]["direction"] == "outgoing"
        assert matches[0]["relation"] == "part_of"
        assert matches[0]["memory"]["id"] == project
        assert matches[0]["virtual"] is False

        # from project's point of view: incoming
        resp = client.get(f"/memories/{project}/related", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        related = resp.json()["related"]
        matches = [r for r in related if r["edge_id"] == edge_id]
        assert len(matches) == 1
        assert matches[0]["direction"] == "incoming"
        assert matches[0]["memory"]["id"] == proc

    def test_related_filters_by_relation(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        a = _make_memory(client, auth_headers, slug, "memoria a")
        b = _make_memory(client, auth_headers, slug, "memoria b")
        c = _make_memory(client, auth_headers, slug, "memoria c")

        client.post(f"/memories/{a}/edges", json={"to_memory": b, "relation": "relates_to"}, headers=auth_headers)
        client.post(f"/memories/{a}/edges", json={"to_memory": c, "relation": "contradicts"}, headers=auth_headers)

        resp = client.get(f"/memories/{a}/related", params={"relation": "contradicts"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        related = resp.json()["related"]
        assert len(related) == 1
        assert related[0]["relation"] == "contradicts"
        assert related[0]["memory"]["id"] == c

    def test_related_includes_virtual_supersedes_chain(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        old_id = _make_memory(client, auth_headers, slug, "presupuesto mensual: 1000")

        resp = client.patch(
            f"/memories/{old_id}",
            json={"content": "presupuesto mensual: 1200 (corregido)"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]

        resp = client.get(f"/memories/{old_id}/related", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        related = resp.json()["related"]
        supersedes_entries = [r for r in related if r["relation"] == "supersedes"]
        assert len(supersedes_entries) == 1
        assert supersedes_entries[0]["virtual"] is True
        assert supersedes_entries[0]["direction"] == "incoming"  # new supersedes old
        assert supersedes_entries[0]["memory"]["id"] == new_id
        assert supersedes_entries[0]["edge_id"] is None

        resp = client.get(f"/memories/{new_id}/related", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        related = resp.json()["related"]
        supersedes_entries = [r for r in related if r["relation"] == "supersedes"]
        assert len(supersedes_entries) == 1
        assert supersedes_entries[0]["direction"] == "outgoing"  # this (new) supersedes old
        assert supersedes_entries[0]["memory"]["id"] == old_id

    def test_delete_edge_removes_it_and_is_idempotent_404(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        a = _make_memory(client, auth_headers, slug, "memoria x")
        b = _make_memory(client, auth_headers, slug, "memoria y")

        resp = client.post(
            f"/memories/{a}/edges", json={"to_memory": b, "relation": "relates_to"}, headers=auth_headers
        )
        edge_id = resp.json()["id"]

        resp = client.delete(f"/memories/{a}/edges/{edge_id}", headers=auth_headers)
        assert resp.status_code == 200, resp.text

        resp = client.get(f"/memories/{a}/related", headers=auth_headers)
        assert all(r["edge_id"] != edge_id for r in resp.json()["related"])

        resp = client.delete(f"/memories/{a}/edges/{edge_id}", headers=auth_headers)
        assert resp.status_code == 404, resp.text

    def test_hard_delete_cascades_edges(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        a = _make_memory(client, auth_headers, slug, "memoria a borrar")
        b = _make_memory(client, auth_headers, slug, "memoria enlazada")

        resp = client.post(
            f"/memories/{a}/edges", json={"to_memory": b, "relation": "relates_to"}, headers=auth_headers
        )
        assert resp.status_code == 201, resp.text
        edge_id = resp.json()["id"]

        resp = client.delete(f"/memories/{a}", params={"hard": True}, headers=auth_headers)
        assert resp.status_code == 200, resp.text

        # the edge must be gone too (ON DELETE CASCADE), not orphaned - querying it
        # from b's side (a no longer exists to query from directly).
        resp = client.get(f"/memories/{b}/related", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert all(r["edge_id"] != edge_id for r in resp.json()["related"])


class TestTimelineIntegration:
    def test_timeline_orders_by_effective_date_most_recent_first(self, client, auth_headers):
        slug = _make_context(client, auth_headers)

        resp_old = client.post(
            "/memories",
            json={
                "content": "evento antiguo",
                "context": slug,
                "type": "episodic",
                "occurred_at": "2020-01-01T00:00:00Z",
            },
            headers=auth_headers,
        )
        resp_new = client.post(
            "/memories",
            json={
                "content": "evento reciente",
                "context": slug,
                "type": "episodic",
                "occurred_at": "2025-01-01T00:00:00Z",
            },
            headers=auth_headers,
        )
        assert resp_old.status_code == 201, resp_old.text
        assert resp_new.status_code == 201, resp_new.text

        resp = client.get("/timeline", params={"context": slug}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        ids_in_order = [i["id"] for i in items]
        assert ids_in_order.index(resp_new.json()["id"]) < ids_in_order.index(resp_old.json()["id"])

    def test_timeline_excludes_semantic_and_procedural_memories(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        semantic_id = _make_memory(client, auth_headers, slug, "un hecho estable", "semantic")

        resp = client.get("/timeline", params={"context": slug}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        ids = [i["id"] for i in resp.json()["items"]]
        assert semantic_id not in ids

    def test_timeline_respects_date_range_filters(self, client, auth_headers):
        slug = _make_context(client, auth_headers)
        client.post(
            "/memories",
            json={
                "content": "fuera de rango",
                "context": slug,
                "type": "episodic",
                "occurred_at": "2019-06-01T00:00:00Z",
            },
            headers=auth_headers,
        )
        resp_in_range = client.post(
            "/memories",
            json={
                "content": "dentro de rango",
                "context": slug,
                "type": "episodic",
                "occurred_at": "2023-06-01T00:00:00Z",
            },
            headers=auth_headers,
        )

        resp = client.get(
            "/timeline",
            params={"context": slug, "from": "2023-01-01T00:00:00Z", "to": "2023-12-31T00:00:00Z"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        ids = [i["id"] for i in resp.json()["items"]]
        assert ids == [resp_in_range.json()["id"]]

    def test_timeline_unknown_context_is_422(self, client, auth_headers):
        resp = client.get("/timeline", params={"context": "no-existe-de-verdad"}, headers=auth_headers)
        assert resp.status_code == 422, resp.text
