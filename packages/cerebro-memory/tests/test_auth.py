"""Tests for token auth with scopes (`cerebro_memory.auth`, plan_v2.md SS9).

`TestPrincipal*` and `test_hash_token_*`/`test_generate_token_*` below are unit tests
over pure functions/dataclasses (no I/O), same spirit as tests/test_rrf.py and
tests/test_context_engine.py.

`TestScopeEnforcementIntegration` needs real Postgres behavior (the `api_tokens`
table, the full FastAPI auth dependency chain) and skips automatically if
DATABASE_URL is not reachable - same pattern as tests/test_supersedence.py and
tests/test_graph.py.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_memory.api import create_app
from cerebro_memory.auth import Principal, hash_token, generate_token
from cerebro_memory.config import get_settings

# --------------------------------------------------------------------------- unit: Principal


def test_principal_has_scope():
    p = Principal(name="x", scopes=frozenset({"read", "write"}), allowed_contexts=None)
    assert p.has_scope("read")
    assert p.has_scope("write")
    assert not p.has_scope("admin")


def test_principal_context_allowed_none_means_all():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_contexts=None)
    assert p.context_allowed("anything")
    assert p.context_allowed(None)


def test_principal_context_allowed_restricted():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_contexts=frozenset({"salud", "aprendizaje"}))
    assert p.context_allowed("salud")
    assert not p.context_allowed("finanzas-personales")
    # None (no target context yet, e.g. context not decided) is never itself a denial
    assert p.context_allowed(None)


def test_principal_filter_slugs_unrestricted_is_identity():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_contexts=None)
    slugs = ["a", "b", "c"]
    assert p.filter_slugs(slugs) == slugs


def test_principal_filter_slugs_restricted():
    p = Principal(name="x", scopes=frozenset({"read"}), allowed_contexts=frozenset({"a", "c"}))
    assert p.filter_slugs(["a", "b", "c", "d"]) == ["a", "c"]


def test_principal_root_has_every_scope_and_no_context_restriction():
    p = Principal(name="root", scopes=frozenset({"read", "write", "admin"}), allowed_contexts=None, is_root=True)
    assert p.is_root
    for scope in ("read", "write", "admin"):
        assert p.has_scope(scope)
    assert p.context_allowed("literalmente-cualquier-cosa")


# --------------------------------------------------------------------------- unit: token helpers


def test_hash_token_is_deterministic_sha256_hex():
    h1 = hash_token("kos_abc123")
    h2 = hash_token("kos_abc123")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest length
    assert all(c in "0123456789abcdef" for c in h1)


def test_hash_token_differs_for_different_input():
    assert hash_token("kos_a") != hash_token("kos_b")


def test_generate_token_has_prefix_and_is_unique():
    t1 = generate_token()
    t2 = generate_token()
    assert t1.startswith("kos_")
    assert t2.startswith("kos_")
    assert t1 != t2
    assert len(t1) > len("kos_") + 20  # comfortably long, not a placeholder


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


def _make_context(client, root_headers) -> str:
    slug = f"test-auth-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/contexts",
        json={"slug": slug, "name": "Test auth context", "kind": "domain"},
        headers=root_headers,
    )
    assert resp.status_code == 201, resp.text
    return slug


def _make_memory(client, root_headers, slug: str, content: str, type_: str = "semantic") -> str:
    resp = client.post(
        "/memories",
        json={"content": content, "context": slug, "type": type_},
        headers=root_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_token(client, root_headers, *, name, scopes, contexts=None) -> str:
    body = {"name": name, "scopes": scopes}
    if contexts is not None:
        body["allowed_contexts"] = contexts
    resp = client.post("/tokens", json=body, headers=root_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


class TestRootTokenCompat:
    def test_root_token_still_works_for_every_scope(self, client, root_headers):
        resp = client.get("/contexts", headers=root_headers)
        assert resp.status_code == 200, resp.text

    def test_missing_auth_header_is_401(self, client):
        resp = client.get("/contexts")
        assert resp.status_code == 401, resp.text

    def test_garbage_bearer_token_is_401(self, client):
        resp = client.get("/contexts", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401, resp.text


class TestTokenLifecycle:
    def test_create_list_revoke_roundtrip(self, client, root_headers):
        name = f"test-token-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"])
        assert token.startswith("kos_")

        # newly created token authenticates
        resp = client.get("/contexts", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text

        listed = client.get("/tokens", headers=root_headers)
        assert listed.status_code == 200, listed.text
        names = {t["name"] for t in listed.json()}
        assert name in names
        # never leaks a hash or the plaintext in the list response
        for row in listed.json():
            assert "token" not in row
            assert "token_hash" not in row

        revoke_resp = client.delete(f"/tokens/{name}", headers=root_headers)
        assert revoke_resp.status_code == 200, revoke_resp.text
        assert revoke_resp.json()["revoked_at"] is not None

        # revoked token no longer authenticates
        resp = client.get("/contexts", headers={"Authorization": f"Bearer {token}"})
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


class TestScopeEnforcement:
    def test_read_only_token_cannot_write(self, client, root_headers):
        slug = _make_context(client, root_headers)
        name = f"test-token-ro-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            "/memories", json={"content": "algo", "context": slug, "type": "semantic"}, headers=headers
        )
        assert resp.status_code == 403, resp.text

    def test_write_only_token_cannot_read(self, client, root_headers):
        name = f"test-token-wo-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["write"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/contexts", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_export_requires_admin_even_with_read_and_write(self, client, root_headers):
        name = f"test-token-rw-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/disambiguations/export", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_token_name_overrides_x_agent_name_header_in_audit(self, client, root_headers):
        slug = _make_context(client, root_headers)
        name = f"test-token-identity-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}", "X-Agent-Name": "someone-else-entirely"}

        resp = client.post(
            "/memories",
            json={"content": "una memoria de prueba", "context": slug, "type": "semantic"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        # source defaults to the resolved agent identity when not given explicitly -
        # must be the token's real name, never the spoofed header.
        assert resp.json()["source"] == name


class TestAllowedContextsEnforcement:
    def test_write_outside_allowed_contexts_is_403(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        name = f"test-token-ctx-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        ok = client.post(
            "/memories", json={"content": "dentro", "context": allowed_slug, "type": "semantic"}, headers=headers
        )
        assert ok.status_code == 201, ok.text

        blocked = client.post(
            "/memories", json={"content": "fuera", "context": other_slug, "type": "semantic"}, headers=headers
        )
        assert blocked.status_code == 403, blocked.text

    def test_explicit_search_outside_allowed_contexts_is_403(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        name = f"test-token-search-ctx-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/memories/search", params={"q": "x", "context": other_slug}, headers=headers)
        assert resp.status_code == 403, resp.text

    def test_scope_all_search_is_silently_narrowed_to_allowed_contexts(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)

        client.post(
            "/memories",
            json={"content": "presupuesto mensual de la casa", "context": allowed_slug, "type": "semantic"},
            headers=root_headers,
        )
        client.post(
            "/memories",
            json={"content": "presupuesto mensual del proyecto", "context": other_slug, "type": "semantic"},
            headers=root_headers,
        )

        name = f"test-token-narrow-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/memories/search", params={"q": "presupuesto mensual", "scope": "all", "limit": 10}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        contexts_seen = {r["context"] for r in resp.json()["results"]}
        assert contexts_seen <= {allowed_slug}

    def test_stats_filters_memories_by_context_to_allowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        client.post(
            "/memories",
            json={"content": "algo en el contexto permitido", "context": allowed_slug, "type": "semantic"},
            headers=root_headers,
        )
        client.post(
            "/memories",
            json={"content": "algo en el contexto ajeno", "context": other_slug, "type": "semantic"},
            headers=root_headers,
        )

        name = f"test-token-stats-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/stats", headers=headers)
        assert resp.status_code == 200, resp.text
        contexts_seen = {row["context"] for row in resp.json()["memories_by_context"]}
        assert other_slug not in contexts_seen


class TestDeleteContext:
    def test_delete_empty_context_succeeds_without_force(self, client, root_headers):
        slug = _make_context(client, root_headers)
        resp = client.delete(f"/contexts/{slug}", headers=root_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"slug": slug, "status": "deleted", "memories_deleted": 0}

        listed = client.get("/contexts", headers=root_headers)
        assert slug not in {c["slug"] for c in listed.json()}

    def test_delete_context_with_memories_is_409_without_force(self, client, root_headers):
        slug = _make_context(client, root_headers)
        client.post(
            "/memories", json={"content": "algo", "context": slug, "type": "semantic"}, headers=root_headers
        )
        resp = client.delete(f"/contexts/{slug}", headers=root_headers)
        assert resp.status_code == 409, resp.text

    def test_delete_context_with_force_hard_deletes_memories(self, client, root_headers):
        slug = _make_context(client, root_headers)
        client.post(
            "/memories", json={"content": "algo", "context": slug, "type": "semantic"}, headers=root_headers
        )
        resp = client.delete(f"/contexts/{slug}", params={"force": True}, headers=root_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["memories_deleted"] == 1

    def test_delete_nonexistent_context_is_404(self, client, root_headers):
        resp = client.delete(f"/contexts/does-not-exist-{uuid.uuid4().hex[:8]}", headers=root_headers)
        assert resp.status_code == 404, resp.text

    def test_delete_context_requires_admin_not_just_write(self, client, root_headers):
        slug = _make_context(client, root_headers)
        name = f"test-token-del-rw-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.delete(f"/contexts/{slug}", headers=headers)
        assert resp.status_code == 403, resp.text


class TestAllowedContextsEnforcementOnRelated:
    """GET /memories/{id}/related must not leak memories or neighbors from a context
    outside a restricted token's allowlist - including neighbors reached via an
    EXPLICIT edge, which `cross_context` marks as an intentional bridge only for
    tokens that can see both sides, never as an allowlist bypass."""

    def test_related_on_memory_outside_allowed_contexts_is_403(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        other_memory = _make_memory(client, root_headers, other_slug, "memoria en contexto ajeno")

        name = f"test-token-related-403-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(f"/memories/{other_memory}/related", headers=headers)
        assert resp.status_code == 403, resp.text

    def test_related_on_nonexistent_memory_is_404_not_403(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        name = f"test-token-related-404-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(f"/memories/{uuid.uuid4()}/related", headers=headers)
        assert resp.status_code == 404, resp.text

    def test_cross_context_neighbor_via_explicit_edge_is_hidden_from_restricted_token(
        self, client, root_headers
    ):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        anchor = _make_memory(client, root_headers, allowed_slug, "decision dentro del contexto permitido", "decision")
        foreign_neighbor = _make_memory(client, root_headers, other_slug, "causa en un contexto ajeno")

        edge_resp = client.post(
            f"/memories/{anchor}/edges",
            json={"to_memory": foreign_neighbor, "relation": "caused_by"},
            headers=root_headers,
        )
        assert edge_resp.status_code == 201, edge_resp.text

        # Root (unrestricted) sees the cross-context neighbor, marked as such.
        root_related = client.get(f"/memories/{anchor}/related", headers=root_headers)
        assert root_related.status_code == 200, root_related.text
        assert any(r["memory"]["id"] == foreign_neighbor for r in root_related.json()["related"])

        # A token restricted to allowed_slug must NOT see it at all - not even
        # marked cross_context - even though the edge is a real, explicit bridge.
        name = f"test-token-related-hide-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(f"/memories/{anchor}/related", headers=headers)
        assert resp.status_code == 200, resp.text
        neighbor_ids = {r["memory"]["id"] for r in resp.json()["related"]}
        assert foreign_neighbor not in neighbor_ids

    def test_search_expand_hides_cross_context_neighbor_from_restricted_token(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        anchor = _make_memory(client, root_headers, allowed_slug, "presupuesto trimestral aprobado", "decision")
        foreign_neighbor = _make_memory(client, root_headers, other_slug, "motivo del presupuesto en otro contexto")

        edge_resp = client.post(
            f"/memories/{anchor}/edges",
            json={"to_memory": foreign_neighbor, "relation": "caused_by"},
            headers=root_headers,
        )
        assert edge_resp.status_code == 201, edge_resp.text

        name = f"test-token-search-expand-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/memories/search",
            params={"q": "presupuesto trimestral aprobado", "context": allowed_slug, "expand": True},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        related = resp.json().get("related") or []
        neighbor_ids = {r["memory"]["id"] for r in related}
        assert foreign_neighbor not in neighbor_ids


class TestAllowedContextsEnforcementOnEdges:
    """POST/DELETE .../edges must check BOTH endpoints of an edge against
    allowed_contexts, not just the path memory_id - the other side of an edge can sit
    in a different (disallowed) context."""

    def test_create_edge_is_403_when_to_memory_context_is_disallowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        anchor = _make_memory(client, root_headers, allowed_slug, "memoria de origen")
        foreign_target = _make_memory(client, root_headers, other_slug, "memoria de destino ajena")

        name = f"test-token-edge-to-403-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            f"/memories/{anchor}/edges",
            json={"to_memory": foreign_target, "relation": "relates_to"},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text

    def test_create_edge_is_403_when_from_memory_context_is_disallowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        foreign_anchor = _make_memory(client, root_headers, other_slug, "memoria de origen ajena")
        target = _make_memory(client, root_headers, allowed_slug, "memoria de destino")

        name = f"test-token-edge-from-403-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            f"/memories/{foreign_anchor}/edges",
            json={"to_memory": target, "relation": "relates_to"},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text

    def test_create_edge_succeeds_when_both_endpoints_allowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        a = _make_memory(client, root_headers, allowed_slug, "memoria a")
        b = _make_memory(client, root_headers, allowed_slug, "memoria b")

        name = f"test-token-edge-ok-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.post(
            f"/memories/{a}/edges", json={"to_memory": b, "relation": "relates_to"}, headers=headers
        )
        assert resp.status_code == 201, resp.text

    def test_delete_edge_is_403_when_other_endpoint_context_is_disallowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        other_slug = _make_context(client, root_headers)
        anchor = _make_memory(client, root_headers, allowed_slug, "memoria ancla")
        foreign_neighbor = _make_memory(client, root_headers, other_slug, "vecino ajeno")

        edge_resp = client.post(
            f"/memories/{anchor}/edges",
            json={"to_memory": foreign_neighbor, "relation": "relates_to"},
            headers=root_headers,
        )
        assert edge_resp.status_code == 201, edge_resp.text
        edge_id = edge_resp.json()["id"]

        name = f"test-token-edge-del-403-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        # memory_id in the path (anchor) IS allowed, but the edge's other endpoint
        # (foreign_neighbor) is not - must still be rejected.
        resp = client.delete(f"/memories/{anchor}/edges/{edge_id}", headers=headers)
        assert resp.status_code == 403, resp.text

        # the edge must survive the rejected attempt
        still_there = client.get(f"/memories/{anchor}/related", headers=root_headers)
        assert any(r["edge_id"] == edge_id for r in still_there.json()["related"])

    def test_delete_edge_succeeds_when_both_endpoints_allowed(self, client, root_headers):
        allowed_slug = _make_context(client, root_headers)
        a = _make_memory(client, root_headers, allowed_slug, "memoria a2")
        b = _make_memory(client, root_headers, allowed_slug, "memoria b2")
        edge_resp = client.post(
            f"/memories/{a}/edges", json={"to_memory": b, "relation": "relates_to"}, headers=root_headers
        )
        edge_id = edge_resp.json()["id"]

        name = f"test-token-edge-del-ok-{uuid.uuid4().hex[:8]}"
        token = _create_token(client, root_headers, name=name, scopes=["read", "write"], contexts=[allowed_slug])
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.delete(f"/memories/{a}/edges/{edge_id}", headers=headers)
        assert resp.status_code == 200, resp.text
