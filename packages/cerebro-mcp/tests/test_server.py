"""cerebro-mcp es un adaptador delgado: cada tool debe llamar exactamente el metodo
correcto del cliente (`_memory`/`_docs`, de `cerebro_clients`) con los argumentos
correctos, y traducir errores del cliente a `{"error": ...}` -- sin agregar logica de
negocio propia (ecosistema-cerebro.md SS15). Se verifica con mocks del cliente, sin
necesidad de las APIs vivas ni de un cliente MCP real.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cerebro_clients import CerebroAPIError, CerebroConnectionError

from cerebro_mcp import server


@pytest.fixture(autouse=True)
def fake_clients(monkeypatch):
    fake_memory = MagicMock()
    fake_docs = MagicMock()
    monkeypatch.setattr(server, "_memory", fake_memory)
    monkeypatch.setattr(server, "_docs", fake_docs)
    monkeypatch.setattr(server, "_last_disambiguation_id", None)
    return fake_memory, fake_docs


def _api_error(status_code, detail="boom"):
    resp = MagicMock()
    return CerebroAPIError(status_code, detail, response=resp)


def _conn_error():
    return CerebroConnectionError("http://test", RuntimeError("refused"))


# =============================================================================== memory_*


class TestMemorySearch:
    def test_routes_to_client_with_given_args(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.search_memories.return_value = {
            "results": [{"id": "m1"}],
            "scope_decision": {"mode": "explicit", "context": "ctx"},
        }
        out = server.memory_search("query", context="ctx", type="semantic", limit=3, expand=True)
        fake_memory.search_memories.assert_called_once_with(
            "query", context="ctx", type="semantic", limit=3, expand=True
        )
        assert out["results"] == [{"id": "m1"}]
        assert out["ambiguous"] is False
        assert out["related"] is None  # expand=True pero la API no devolvio 'related'

    def test_ambiguous_result_sets_message_and_disambiguation_slot(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.search_memories.return_value = {
            "results": [],
            "scope_decision": {
                "mode": "ambiguous",
                "disambiguation_id": "d1",
                "candidates": [{"slug": "a", "score": 0.6}],
                "results_by_candidate": {"a": []},
            },
        }
        out = server.memory_search("query")
        assert out["ambiguous"] is True
        assert "ambigua" in out["message"]
        assert server._last_disambiguation_id == "d1"

    def test_next_call_with_context_resolves_pending_disambiguation(self, fake_clients):
        fake_memory, _ = fake_clients
        server._last_disambiguation_id = "pending-1"
        fake_memory.search_memories.return_value = {
            "results": [],
            "scope_decision": {"mode": "explicit", "context": "ctx"},
        }
        fake_memory.resolve_disambiguation.return_value = {"status": "resolved"}

        out = server.memory_search("query", context="ctx")

        fake_memory.resolve_disambiguation.assert_called_once_with("pending-1", "ctx")
        assert out["note"] is not None
        assert server._last_disambiguation_id is None  # slot consumido

    def test_connection_error_becomes_error_dict(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.search_memories.side_effect = _conn_error()
        out = server.memory_search("query")
        assert "error" in out
        assert "cerebro-memory" in out["error"]

    def test_401_becomes_auth_error_message(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.search_memories.side_effect = _api_error(401)
        out = server.memory_search("query")
        assert "error" in out
        assert "401" in out["error"] or "autenticacion" in out["error"]


class TestMemoryRemember:
    def test_rejects_invalid_type_without_calling_client(self, fake_clients):
        fake_memory, _ = fake_clients
        out = server.memory_remember("c", "ctx", "not-a-type")
        assert "error" in out
        fake_memory.create_memory.assert_not_called()

    def test_routes_to_client(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.create_memory.return_value = {"id": "m1"}
        out = server.memory_remember("contenido", "ctx", "semantic", title="T", importance=0.9)
        fake_memory.create_memory.assert_called_once_with(
            "contenido", "ctx", "semantic", title="T", importance=0.9
        )
        assert out == {"memory": {"id": "m1"}}

    def test_unknown_context_422_lists_available_contexts(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.create_memory.side_effect = _api_error(422, "unknown context 'ctx'")
        fake_memory.list_contexts.return_value = [{"slug": "a", "kind": "domain", "description": "x"}]
        out = server.memory_remember("c", "ctx", "semantic")
        assert "error" in out
        assert "ctx" in out["error"]
        assert "a" in out["error"]


class TestMemoryUpdate:
    def test_routes_to_client(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.update_memory.return_value = {"id": "m2"}
        out = server.memory_update("m1", "nuevo")
        fake_memory.update_memory.assert_called_once_with("m1", "nuevo")
        assert out == {"memory": {"id": "m2"}}

    def test_404_becomes_error(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.update_memory.side_effect = _api_error(404)
        out = server.memory_update("missing", "x")
        assert "error" in out


class TestMemoryForget:
    def test_routes_to_client_with_hard_flag(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.delete_memory.return_value = {"id": "m1", "hard": True, "status": "deleted"}
        out = server.memory_forget("m1", hard=True)
        fake_memory.delete_memory.assert_called_once_with("m1", hard=True)
        assert out["status"] == "deleted"


class TestMemoryLink:
    def test_routes_to_client(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.create_edge.return_value = {"id": "e1"}
        out = server.memory_link("a", "b", "caused_by", note="porque")
        fake_memory.create_edge.assert_called_once_with("a", "b", "caused_by", note="porque")
        assert out == {"edge": {"id": "e1"}}


class TestMemoryRelated:
    def test_routes_to_client(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.get_related.return_value = {"related": [{"memory": {"id": "m2"}}]}
        out = server.memory_related("m1", relation="supersedes")
        fake_memory.get_related.assert_called_once_with("m1", relation="supersedes")
        assert out["related"] == [{"memory": {"id": "m2"}}]


class TestMemoryTimeline:
    def test_routes_to_client(self, fake_clients):
        fake_memory, _ = fake_clients
        fake_memory.get_timeline.return_value = {"items": [{"id": "m1"}]}
        out = server.memory_timeline(context="ctx", limit=10)
        fake_memory.get_timeline.assert_called_once_with(context="ctx", from_date=None, to_date=None, limit=10)
        assert out["items"] == [{"id": "m1"}]


def test_memory_contexts_routes_to_client(fake_clients):
    fake_memory, _ = fake_clients
    fake_memory.list_contexts.return_value = [{"slug": "a"}]
    out = server.memory_contexts()
    fake_memory.list_contexts.assert_called_once_with()
    assert out == {"contexts": [{"slug": "a"}]}


def test_memory_create_context_routes_to_client(fake_clients):
    fake_memory, _ = fake_clients
    fake_memory.create_context.return_value = {"slug": "a"}
    out = server.memory_create_context("a", "Name", "domain", description="d")
    fake_memory.create_context.assert_called_once_with("a", "Name", "domain", description="d")
    assert out == {"context": {"slug": "a"}}


def test_memory_stats_routes_to_client(fake_clients):
    fake_memory, _ = fake_clients
    fake_memory.get_stats.return_value = {"memories_by_context": []}
    out = server.memory_stats()
    fake_memory.get_stats.assert_called_once_with()
    assert out == {"stats": {"memories_by_context": []}}


# =============================================================================== docs_*


def test_docs_create_category_routes_to_client(fake_clients):
    _, fake_docs = fake_clients
    fake_docs.create_category.return_value = {"slug": "eco"}
    out = server.docs_create_category("eco", "Ecosistema", description="d")
    fake_docs.create_category.assert_called_once_with("eco", "Ecosistema", description="d")
    assert out == {"category": {"slug": "eco"}}

    fake_docs.create_category.side_effect = _api_error(409)
    out = server.docs_create_category("eco", "Ecosistema")
    assert "error" in out


def test_docs_categories_routes_to_client(fake_clients):
    _, fake_docs = fake_clients
    fake_docs.list_categories.return_value = [{"slug": "eco"}]
    out = server.docs_categories()
    fake_docs.list_categories.assert_called_once_with()
    assert out == {"categories": [{"slug": "eco"}]}


class TestDocsSave:
    def test_routes_to_client(self, fake_clients):
        _, fake_docs = fake_clients
        fake_docs.create_document.return_value = {"id": "d1"}
        out = server.docs_save("Titulo", "contenido", "eco", slug="mi-slug")
        fake_docs.create_document.assert_called_once_with("Titulo", "contenido", "eco", slug="mi-slug")
        assert out == {"document": {"id": "d1"}}

    def test_unknown_category_404_lists_available(self, fake_clients):
        _, fake_docs = fake_clients
        fake_docs.create_document.side_effect = _api_error(404, "categoria inexistente")
        fake_docs.list_categories.return_value = [{"slug": "eco", "name": "Ecosistema"}]
        out = server.docs_save("T", "C", "no-existe")
        assert "error" in out
        assert "no-existe" in out["error"]
        assert "eco" in out["error"]

    def test_slug_collision_409_propagates_detail(self, fake_clients):
        _, fake_docs = fake_clients
        fake_docs.create_document.side_effect = _api_error(409, "ya existe un documento con slug 'x'")
        out = server.docs_save("T", "C", "eco")
        assert "ya existe" in out["error"]


def test_docs_get_routes_to_client(fake_clients):
    _, fake_docs = fake_clients
    fake_docs.get_document.return_value = {"id": "d1"}
    out = server.docs_get("eco", "mi-doc")
    fake_docs.get_document.assert_called_once_with("eco", "mi-doc")
    assert out == {"document": {"id": "d1"}}

    fake_docs.get_document.side_effect = _api_error(404)
    out = server.docs_get("eco", "no-existe")
    assert "error" in out


def test_docs_search_routes_to_list_documents_with_q(fake_clients):
    _, fake_docs = fake_clients
    fake_docs.list_documents.return_value = [{"id": "d1"}]
    out = server.docs_search("busqueda", category="eco", limit=5, offset=1)
    fake_docs.list_documents.assert_called_once_with(category="eco", q="busqueda", limit=5, offset=1)
    assert out == {"documents": [{"id": "d1"}]}


def test_docs_list_routes_to_list_documents_without_q(fake_clients):
    _, fake_docs = fake_clients
    fake_docs.list_documents.return_value = [{"id": "d1"}]
    out = server.docs_list(category="eco")
    fake_docs.list_documents.assert_called_once_with(category="eco", limit=20, offset=0)
    assert out == {"documents": [{"id": "d1"}]}


class TestDocsUpdate:
    def test_routes_to_client(self, fake_clients):
        _, fake_docs = fake_clients
        fake_docs.update_document.return_value = {"id": "d1"}
        out = server.docs_update("d1", "T", "C", "eco", slug="nuevo")
        fake_docs.update_document.assert_called_once_with("d1", "T", "C", "eco", slug="nuevo")
        assert out == {"document": {"id": "d1"}}

    def test_404_propagates_detail(self, fake_clients):
        _, fake_docs = fake_clients
        fake_docs.update_document.side_effect = _api_error(404, "document not found")
        out = server.docs_update("missing", "T", "C", "eco")
        assert out["error"] == "document not found"


class TestDocsPatchSection:
    def test_rejects_invalid_operation_without_calling_client(self, fake_clients):
        _, fake_docs = fake_clients
        out = server.docs_patch_section("d1", "## Intro", "not-an-operation")
        assert "error" in out
        fake_docs.patch_section.assert_not_called()

    def test_routes_to_client_with_all_args(self, fake_clients):
        _, fake_docs = fake_clients
        fake_docs.patch_section.return_value = {"id": "d1"}
        out = server.docs_patch_section(
            "d1", "Intro", "append", body="texto", create_if_missing=True, new_heading_level=3
        )
        fake_docs.patch_section.assert_called_once_with(
            "d1", "Intro", "append", body="texto", create_if_missing=True, new_heading_level=3
        )
        assert out == {"document": {"id": "d1"}}

    def test_ambiguous_heading_409_propagates_detail(self, fake_clients):
        _, fake_docs = fake_clients
        fake_docs.patch_section.side_effect = _api_error(409, "heading 'Intro' is ambiguous")
        out = server.docs_patch_section("d1", "Intro", "replace")
        assert "ambiguous" in out["error"]

    def test_heading_not_found_404_propagates_detail(self, fake_clients):
        _, fake_docs = fake_clients
        fake_docs.patch_section.side_effect = _api_error(404, "heading 'X' not found")
        out = server.docs_patch_section("d1", "X", "replace")
        assert "not found" in out["error"]


def test_docs_delete_routes_to_client(fake_clients):
    _, fake_docs = fake_clients
    fake_docs.delete_document.return_value = {"id": "d1", "status": "deleted"}
    out = server.docs_delete("d1")
    fake_docs.delete_document.assert_called_once_with("d1")
    assert out == {"id": "d1", "status": "deleted"}

    fake_docs.delete_document.side_effect = _api_error(404)
    out = server.docs_delete("missing")
    assert "error" in out


# =============================================================================== registro de tools


def test_all_19_tools_are_registered():
    """Las 10 memory_* existentes + las 9 docs_* nuevas -- el inventario exacto que
    espera el usuario (ecosistema-cerebro.md SS10)."""
    tool_names = {
        "memory_search",
        "memory_remember",
        "memory_update",
        "memory_forget",
        "memory_link",
        "memory_related",
        "memory_timeline",
        "memory_contexts",
        "memory_create_context",
        "memory_stats",
        "docs_create_category",
        "docs_categories",
        "docs_save",
        "docs_get",
        "docs_search",
        "docs_list",
        "docs_update",
        "docs_patch_section",
        "docs_delete",
    }
    assert len(tool_names) == 19
    for name in tool_names:
        assert hasattr(server, name), f"falta la tool {name}"
        assert callable(getattr(server, name))
