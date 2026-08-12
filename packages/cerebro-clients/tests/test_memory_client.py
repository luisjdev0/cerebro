"""`MemoryClient` debe enrutar cada metodo al endpoint/verbo/params correcto de la API
de cerebro-memory y no filtrar ninguna decision propia (p.ej. omitir parametros
opcionales no dados, en vez de mandarlos como None/""). Usa un transporte httpx que
graba requests -- sin necesidad de la API viva (ecosistema-cerebro.md SS15)."""

from __future__ import annotations

import pytest

from cerebro_clients.base import CerebroAPIError, CerebroConnectionError
from cerebro_clients.memory_client import MemoryClient
from ._helpers import RaisingTransport, RecordingTransport


def make_client(transport, **kwargs) -> MemoryClient:
    return MemoryClient(base_url="http://test-memory", token="tok-123", agent="test-agent", transport=transport, **kwargs)


def test_auth_and_agent_headers_are_set():
    transport = RecordingTransport()
    client = make_client(transport)
    client.health()
    headers = transport.last["headers"]
    assert headers["authorization"] == "Bearer tok-123"
    assert headers["x-agent-name"] == "test-agent"


def test_health():
    transport = RecordingTransport(response_json={"status": "ok"})
    result = make_client(transport).health()
    assert transport.last["method"] == "GET"
    assert transport.last["path"] == "/health"
    assert result == {"status": "ok"}


class TestSearchMemories:
    def test_minimal_call_omits_optional_params(self):
        transport = RecordingTransport(response_json={"results": []})
        make_client(transport).search_memories("algo")
        params = transport.last["params"]
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/memories/search"
        assert params == {"q": "algo", "limit": "5"}

    def test_full_call_includes_all_params(self):
        transport = RecordingTransport()
        make_client(transport).search_memories(
            "algo",
            context="proyecto-x",
            scope="all",
            type="decision",
            limit=10,
            include_superseded=True,
            expand=True,
        )
        params = transport.last["params"]
        assert params["context"] == "proyecto-x"
        assert params["scope"] == "all"
        assert params["type"] == "decision"
        assert params["limit"] == "10"
        assert params["include_superseded"] == "true"
        assert params["expand"] == "true"


class TestCreateMemory:
    def test_required_fields_only(self):
        transport = RecordingTransport()
        make_client(transport).create_memory("contenido", "ctx", "semantic")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/memories"
        assert transport.last["json"] == {"content": "contenido", "context": "ctx", "type": "semantic"}

    def test_optional_fields_included_when_given(self):
        transport = RecordingTransport()
        make_client(transport).create_memory(
            "contenido", "ctx", "episodic", title="T", importance=0.8, occurred_at="2026-01-01", source="agent-x"
        )
        body = transport.last["json"]
        assert body == {
            "content": "contenido",
            "context": "ctx",
            "type": "episodic",
            "title": "T",
            "importance": 0.8,
            "occurred_at": "2026-01-01",
            "source": "agent-x",
        }


def test_update_memory():
    transport = RecordingTransport()
    make_client(transport).update_memory("mem-1", "nuevo contenido")
    assert transport.last["method"] == "PATCH"
    assert transport.last["path"] == "/memories/mem-1"
    assert transport.last["json"] == {"content": "nuevo contenido"}


def test_delete_memory():
    transport = RecordingTransport()
    make_client(transport).delete_memory("mem-1", hard=True)
    assert transport.last["method"] == "DELETE"
    assert transport.last["path"] == "/memories/mem-1"
    assert transport.last["params"] == {"hard": "true"}


def test_resolve_disambiguation():
    transport = RecordingTransport()
    make_client(transport).resolve_disambiguation("disamb-1", "ctx")
    assert transport.last["method"] == "POST"
    assert transport.last["path"] == "/disambiguations/disamb-1/resolve"
    assert transport.last["json"] == {"context": "ctx"}


class TestExportDisambiguations:
    def test_default_omits_resolved_only(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).export_disambiguations()
        assert transport.last["path"] == "/disambiguations/export"
        assert transport.last["params"] == {}

    def test_resolved_only_true(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).export_disambiguations(resolved_only=True)
        assert transport.last["params"] == {"resolved_only": "true"}


def test_create_edge():
    transport = RecordingTransport()
    make_client(transport).create_edge("a", "b", "caused_by", note="porque si")
    assert transport.last["method"] == "POST"
    assert transport.last["path"] == "/memories/a/edges"
    assert transport.last["json"] == {"to_memory": "b", "relation": "caused_by", "note": "porque si"}


def test_delete_edge():
    transport = RecordingTransport()
    make_client(transport).delete_edge("a", "edge-1")
    assert transport.last["method"] == "DELETE"
    assert transport.last["path"] == "/memories/a/edges/edge-1"


class TestGetRelated:
    def test_without_relation_filter(self):
        transport = RecordingTransport(response_json={"related": []})
        make_client(transport).get_related("mem-1")
        assert transport.last["path"] == "/memories/mem-1/related"
        assert transport.last["params"] == {}

    def test_with_relation_filter(self):
        transport = RecordingTransport(response_json={"related": []})
        make_client(transport).get_related("mem-1", relation="supersedes")
        assert transport.last["params"] == {"relation": "supersedes"}


class TestGetTimeline:
    def test_defaults(self):
        transport = RecordingTransport(response_json={"items": []})
        make_client(transport).get_timeline()
        assert transport.last["path"] == "/timeline"
        assert transport.last["params"] == {"limit": "50"}

    def test_full(self):
        transport = RecordingTransport(response_json={"items": []})
        make_client(transport).get_timeline(context="ctx", from_date="2026-01-01", to_date="2026-02-01", limit=10)
        params = transport.last["params"]
        assert params == {"limit": "10", "context": "ctx", "from": "2026-01-01", "to": "2026-02-01"}


def test_list_contexts():
    transport = RecordingTransport(response_json=[])
    make_client(transport).list_contexts()
    assert transport.last["method"] == "GET"
    assert transport.last["path"] == "/contexts"


class TestCreateContext:
    def test_without_description(self):
        transport = RecordingTransport()
        make_client(transport).create_context("slug", "Name", "domain")
        assert transport.last["json"] == {"slug": "slug", "name": "Name", "kind": "domain"}

    def test_with_description(self):
        transport = RecordingTransport()
        make_client(transport).create_context("slug", "Name", "domain", description="algo")
        assert transport.last["json"]["description"] == "algo"


def test_delete_context():
    transport = RecordingTransport()
    make_client(transport).delete_context("slug", force=True)
    assert transport.last["method"] == "DELETE"
    assert transport.last["path"] == "/contexts/slug"
    assert transport.last["params"] == {"force": "true"}


def test_get_stats():
    transport = RecordingTransport(response_json={"memories_by_context": []})
    make_client(transport).get_stats()
    assert transport.last["method"] == "GET"
    assert transport.last["path"] == "/stats"


class TestTokens:
    def test_create_token_minimal(self):
        transport = RecordingTransport()
        make_client(transport).create_token("agente-x", ["read", "write"])
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/tokens"
        assert transport.last["json"] == {"name": "agente-x", "scopes": ["read", "write"]}

    def test_create_token_with_contexts_and_value(self):
        transport = RecordingTransport()
        make_client(transport).create_token(
            "agente-x", ["read"], allowed_contexts=["ctx-a"], value="kos_provided-secret"
        )
        assert transport.last["json"] == {
            "name": "agente-x",
            "scopes": ["read"],
            "allowed_contexts": ["ctx-a"],
            "value": "kos_provided-secret",
        }

    def test_list_tokens(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_tokens()
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/tokens"

    def test_revoke_token(self):
        transport = RecordingTransport()
        make_client(transport).revoke_token("agente-x")
        assert transport.last["method"] == "DELETE"
        assert transport.last["path"] == "/tokens/agente-x"


class TestErrorPropagation:
    def test_http_error_raises_cerebro_api_error_with_detail(self):
        transport = RecordingTransport(response_json={"detail": "unknown context 'x'"}, status_code=422)
        client = make_client(transport)
        with pytest.raises(CerebroAPIError) as exc_info:
            client.create_memory("c", "x", "semantic")
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == "unknown context 'x'"

    def test_connection_failure_raises_cerebro_connection_error(self):
        client = make_client(RaisingTransport())
        with pytest.raises(CerebroConnectionError):
            client.health()
