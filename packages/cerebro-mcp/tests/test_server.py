"""cerebro-mcp is a thin adapter: each tool must call exactly the correct client
method (`_memory`/`_docs`, from `cerebro_clients`) with the correct arguments,
and translate client errors into `{"error": ...}` -- without adding its own
business logic (ecosistema-cerebro.md SS15). It's verified with client mocks, without
needing the live APIs or a real MCP client.
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
    fake_flows = MagicMock()
    fake_auth = MagicMock()
    monkeypatch.setattr(server, "_memory", fake_memory)
    monkeypatch.setattr(server, "_docs", fake_docs)
    monkeypatch.setattr(server, "_flows", fake_flows)
    monkeypatch.setattr(server, "_auth", fake_auth)
    monkeypatch.setattr(server, "_last_disambiguation_id", None)
    return fake_memory, fake_docs, fake_flows, fake_auth


def _api_error(status_code, detail="boom"):
    resp = MagicMock()
    return CerebroAPIError(status_code, detail, response=resp)


def _conn_error():
    return CerebroConnectionError("http://test", RuntimeError("refused"))


# =============================================================================== memory_*


class TestMemorySearch:
    def test_routes_to_client_with_given_args(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
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
        assert out["related"] is None  # expand=True but the API did not return 'related'

    def test_ambiguous_result_sets_message_and_disambiguation_slot(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
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
        fake_memory, _, _, _ = fake_clients
        server._last_disambiguation_id = "pending-1"
        fake_memory.search_memories.return_value = {
            "results": [],
            "scope_decision": {"mode": "explicit", "context": "ctx"},
        }
        fake_memory.resolve_disambiguation.return_value = {"status": "resolved"}

        out = server.memory_search("query", context="ctx")

        fake_memory.resolve_disambiguation.assert_called_once_with("pending-1", "ctx")
        assert out["note"] is not None
        assert server._last_disambiguation_id is None  # slot consumed

    def test_connection_error_becomes_error_dict(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.search_memories.side_effect = _conn_error()
        out = server.memory_search("query")
        assert "error" in out
        assert "cerebro-memory" in out["error"]

    def test_401_becomes_auth_error_message(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.search_memories.side_effect = _api_error(401)
        out = server.memory_search("query")
        assert "error" in out
        assert "401" in out["error"] or "autenticacion" in out["error"]


class TestMemoryRemember:
    def test_rejects_invalid_type_without_calling_client(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        out = server.memory_remember("c", "ctx", "not-a-type")
        assert "error" in out
        fake_memory.create_memory.assert_not_called()

    def test_routes_to_client(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.create_memory.return_value = {"id": "m1"}
        out = server.memory_remember("contenido", "ctx", "semantic", title="T", importance=0.9)
        fake_memory.create_memory.assert_called_once_with(
            "contenido", "ctx", "semantic", title="T", importance=0.9
        )
        assert out == {"memory": {"id": "m1"}}

    def test_unknown_context_422_lists_available_contexts(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.create_memory.side_effect = _api_error(422, "unknown context 'ctx'")
        fake_memory.list_contexts.return_value = [{"slug": "a", "kind": "domain", "description": "x"}]
        out = server.memory_remember("c", "ctx", "semantic")
        assert "error" in out
        assert "ctx" in out["error"]
        assert "a" in out["error"]


class TestMemoryUpdate:
    def test_routes_to_client(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.update_memory.return_value = {"id": "m2"}
        out = server.memory_update("m1", "nuevo")
        fake_memory.update_memory.assert_called_once_with("m1", "nuevo")
        assert out == {"memory": {"id": "m2"}}

    def test_404_becomes_error(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.update_memory.side_effect = _api_error(404)
        out = server.memory_update("missing", "x")
        assert "error" in out


class TestMemoryForget:
    def test_routes_to_client_with_hard_flag(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.delete_memory.return_value = {"id": "m1", "hard": True, "status": "deleted"}
        out = server.memory_forget("m1", hard=True)
        fake_memory.delete_memory.assert_called_once_with("m1", hard=True)
        assert out["status"] == "deleted"


class TestMemoryLink:
    def test_routes_to_client(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.create_edge.return_value = {"id": "e1"}
        out = server.memory_link("a", "b", "caused_by", note="porque")
        fake_memory.create_edge.assert_called_once_with("a", "b", "caused_by", note="porque")
        assert out == {"edge": {"id": "e1"}}


class TestMemoryRelated:
    def test_routes_to_client(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.get_related.return_value = {"related": [{"memory": {"id": "m2"}}]}
        out = server.memory_related("m1", relation="supersedes")
        fake_memory.get_related.assert_called_once_with("m1", relation="supersedes")
        assert out["related"] == [{"memory": {"id": "m2"}}]


class TestMemoryTimeline:
    def test_routes_to_client(self, fake_clients):
        fake_memory, _, _, _ = fake_clients
        fake_memory.get_timeline.return_value = {"items": [{"id": "m1"}]}
        out = server.memory_timeline(context="ctx", limit=10)
        fake_memory.get_timeline.assert_called_once_with(context="ctx", from_date=None, to_date=None, limit=10)
        assert out["items"] == [{"id": "m1"}]


def test_memory_contexts_routes_to_client(fake_clients):
    fake_memory, _, _, _ = fake_clients
    fake_memory.list_contexts.return_value = [{"slug": "a"}]
    out = server.memory_contexts()
    fake_memory.list_contexts.assert_called_once_with()
    assert out == {"contexts": [{"slug": "a"}]}


def test_memory_create_context_routes_to_client(fake_clients):
    fake_memory, _, _, _ = fake_clients
    fake_memory.create_context.return_value = {"slug": "a"}
    out = server.memory_create_context("a", "Name", "domain", description="d")
    fake_memory.create_context.assert_called_once_with("a", "Name", "domain", description="d")
    assert out == {"context": {"slug": "a"}}


def test_memory_stats_routes_to_client(fake_clients):
    fake_memory, _, _, _ = fake_clients
    fake_memory.get_stats.return_value = {"memories_by_context": []}
    out = server.memory_stats()
    fake_memory.get_stats.assert_called_once_with()
    assert out == {"stats": {"memories_by_context": []}}


# =============================================================================== docs_*


def test_docs_create_category_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.create_category.return_value = {"slug": "eco"}
    out = server.docs_create_category("eco", "Ecosistema", description="d")
    fake_docs.create_category.assert_called_once_with("eco", "Ecosistema", description="d", hidden=False, locked=False)
    assert out == {"category": {"slug": "eco"}}

    fake_docs.create_category.side_effect = _api_error(409)
    out = server.docs_create_category("eco", "Ecosistema")
    assert "error" in out


def test_docs_create_category_hidden_locked_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.create_category.return_value = {"slug": "eco"}
    server.docs_create_category("eco", "Ecosistema", hidden=True, locked=True)
    fake_docs.create_category.assert_called_once_with("eco", "Ecosistema", description=None, hidden=True, locked=True)

    fake_docs.create_category.side_effect = _api_error(422, "locked requiere hidden")
    out = server.docs_create_category("eco", "Ecosistema", locked=True)
    assert "locked requiere hidden" in out["error"]


def test_docs_categories_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.list_categories.return_value = [{"slug": "eco"}]
    out = server.docs_categories()
    fake_docs.list_categories.assert_called_once_with()
    assert out == {"categories": [{"slug": "eco"}]}


class TestDocsSave:
    def test_routes_to_client(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        fake_docs.create_document.return_value = {"id": "d1"}
        out = server.docs_save("Titulo", "contenido", "eco", slug="mi-slug")
        fake_docs.create_document.assert_called_once_with("Titulo", "contenido", "eco", slug="mi-slug")
        assert out == {"document": {"id": "d1"}}

    def test_unknown_category_404_lists_available(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        fake_docs.create_document.side_effect = _api_error(404, "categoria inexistente")
        fake_docs.list_categories.return_value = [{"slug": "eco", "name": "Ecosistema"}]
        out = server.docs_save("T", "C", "no-existe")
        assert "error" in out
        assert "no-existe" in out["error"]
        assert "eco" in out["error"]

    def test_slug_collision_409_propagates_detail(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        fake_docs.create_document.side_effect = _api_error(409, "ya existe un documento con slug 'x'")
        out = server.docs_save("T", "C", "eco")
        assert "ya existe" in out["error"]


def test_docs_get_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.get_document.return_value = {"id": "d1"}
    out = server.docs_get("eco", "mi-doc")
    fake_docs.get_document.assert_called_once_with("eco", "mi-doc")
    assert out == {"document": {"id": "d1"}}

    fake_docs.get_document.side_effect = _api_error(404)
    out = server.docs_get("eco", "no-existe")
    assert "error" in out


def test_docs_get_alerts_model_on_redirect(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.get_document.return_value = {
        "id": "d1",
        "category": "eco",
        "slug": "nuevo-slug",
        "redirected_from": {"category": "eco", "slug": "slug-viejo"},
    }
    out = server.docs_get("eco", "slug-viejo")
    assert "alert" in out
    assert "slug-viejo" in out["alert"]
    assert "eco/nuevo-slug" in out["alert"]
    assert out["document"]["slug"] == "nuevo-slug"


def test_docs_get_no_alert_without_redirect(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.get_document.return_value = {"id": "d1", "category": "eco", "slug": "mi-doc"}
    out = server.docs_get("eco", "mi-doc")
    assert "alert" not in out


def test_docs_search_routes_to_list_documents_with_q(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.list_documents.return_value = [{"id": "d1"}]
    out = server.docs_search("busqueda", category="eco", limit=5, offset=1)
    fake_docs.list_documents.assert_called_once_with(category="eco", q="busqueda", limit=5, offset=1)
    assert out == {"documents": [{"id": "d1"}]}


def test_docs_list_routes_to_list_documents_without_q(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.list_documents.return_value = [{"id": "d1"}]
    out = server.docs_list(category="eco")
    fake_docs.list_documents.assert_called_once_with(category="eco", limit=20, offset=0)
    assert out == {"documents": [{"id": "d1"}]}


class TestDocsUpdate:
    def test_routes_to_client(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        fake_docs.update_document.return_value = {"id": "d1"}
        out = server.docs_update("d1", "T", "C", "eco", slug="nuevo")
        fake_docs.update_document.assert_called_once_with("d1", "T", "C", "eco", slug="nuevo")
        assert out == {"document": {"id": "d1"}}

    def test_404_propagates_detail(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        fake_docs.update_document.side_effect = _api_error(404, "document not found")
        out = server.docs_update("missing", "T", "C", "eco")
        assert out["error"] == "document not found"


class TestDocsPatchSection:
    def test_rejects_invalid_operation_without_calling_client(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        out = server.docs_patch_section("d1", "## Intro", "not-an-operation")
        assert "error" in out
        fake_docs.patch_section.assert_not_called()

    def test_routes_to_client_with_all_args(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        fake_docs.patch_section.return_value = {"id": "d1"}
        out = server.docs_patch_section(
            "d1", "Intro", "append", body="texto", create_if_missing=True, new_heading_level=3
        )
        fake_docs.patch_section.assert_called_once_with(
            "d1", "Intro", "append", body="texto", create_if_missing=True, new_heading_level=3
        )
        assert out == {"document": {"id": "d1"}}

    def test_ambiguous_heading_409_propagates_detail(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        fake_docs.patch_section.side_effect = _api_error(409, "heading 'Intro' is ambiguous")
        out = server.docs_patch_section("d1", "Intro", "replace")
        assert "ambiguous" in out["error"]

    def test_heading_not_found_404_propagates_detail(self, fake_clients):
        _, fake_docs, _, _ = fake_clients
        fake_docs.patch_section.side_effect = _api_error(404, "heading 'X' not found")
        out = server.docs_patch_section("d1", "X", "replace")
        assert "not found" in out["error"]


def test_docs_delete_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.delete_document.return_value = {"id": "d1", "status": "deleted"}
    out = server.docs_delete("d1")
    fake_docs.delete_document.assert_called_once_with("d1")
    assert out == {"id": "d1", "status": "deleted"}

    fake_docs.delete_document.side_effect = _api_error(404)
    out = server.docs_delete("missing")
    assert "error" in out


# =============================================================================== flow_*


def test_flow_create_category_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.create_category.return_value = {"slug": "incident", "code": "INC"}
    out = server.flow_create_category("incident", "INC", "Incidencias", description="d")
    fake_flows.create_category.assert_called_once_with("incident", "INC", "Incidencias", description="d")
    assert out == {"category": {"slug": "incident", "code": "INC"}}

    fake_flows.create_category.side_effect = _api_error(409)
    out = server.flow_create_category("incident", "INC", "Incidencias")
    assert "error" in out


def test_flow_categories_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.list_categories.return_value = [{"slug": "incident", "code": "INC"}]
    out = server.flow_categories()
    fake_flows.list_categories.assert_called_once_with()
    assert out == {"categories": [{"slug": "incident", "code": "INC"}]}


class TestFlowValidate:
    def test_valid_returns_true(self, fake_clients):
        _, _, fake_flows, _ = fake_clients
        fake_flows.validate_flow.return_value = {"valid": True}
        out = server.flow_validate("metadata: {}")
        fake_flows.validate_flow.assert_called_once_with("metadata: {}")
        assert out == {"valid": True}

    def test_invalid_422_propagates_detail(self, fake_clients):
        _, _, fake_flows, _ = fake_clients
        fake_flows.validate_flow.side_effect = _api_error(422, "step 'a': falta 'next'")
        out = server.flow_validate("bad yaml")
        assert "falta 'next'" in out["error"]


class TestFlowSave:
    def test_routes_to_client(self, fake_clients):
        _, _, fake_flows, _ = fake_clients
        fake_flows.create_flow.return_value = {"code": "INC-1"}
        out = server.flow_save("incident", "metadata: {}", code="INC-1")
        fake_flows.create_flow.assert_called_once_with("incident", "metadata: {}", code="INC-1")
        assert out == {"flow": {"code": "INC-1"}}

    def test_unknown_category_404_lists_available(self, fake_clients):
        _, _, fake_flows, _ = fake_clients
        fake_flows.create_flow.side_effect = _api_error(404, "categoria inexistente")
        fake_flows.list_categories.return_value = [{"slug": "incident", "name": "Incidencias"}]
        out = server.flow_save("no-existe", "metadata: {}")
        assert "error" in out
        assert "no-existe" in out["error"]
        assert "incident" in out["error"]


def test_flow_get_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.get_flow.return_value = {"code": "INC-1"}
    out = server.flow_get("INC-1")
    fake_flows.get_flow.assert_called_once_with("INC-1")
    assert out == {"flow": {"code": "INC-1"}}

    fake_flows.get_flow.side_effect = _api_error(404)
    out = server.flow_get("missing")
    assert "error" in out


def test_flow_list_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.list_flows.return_value = [{"code": "INC-1"}]
    out = server.flow_list(category="incident", limit=5, offset=1)
    fake_flows.list_flows.assert_called_once_with(category="incident", limit=5, offset=1)
    assert out == {"flows": [{"code": "INC-1"}]}


def test_flow_update_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.update_flow.return_value = {"code": "INC-1", "current_version": 2}
    out = server.flow_update("INC-1", "metadata: {}")
    fake_flows.update_flow.assert_called_once_with("INC-1", "metadata: {}")
    assert out == {"flow": {"code": "INC-1", "current_version": 2}}

    fake_flows.update_flow.side_effect = _api_error(422, "step 'a': falta 'next'")
    out = server.flow_update("INC-1", "bad")
    assert "falta 'next'" in out["error"]


def test_flow_delete_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.delete_flow.return_value = {"code": "INC-1", "status": "deleted"}
    out = server.flow_delete("INC-1")
    fake_flows.delete_flow.assert_called_once_with("INC-1")
    assert out == {"code": "INC-1", "status": "deleted"}


def test_flow_start_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.start_flow.return_value = {"run_id": "r1", "status": "in_progress", "step": {"id": "a"}}
    out = server.flow_start("INC-1")
    fake_flows.start_flow.assert_called_once_with("INC-1")
    assert out["run_id"] == "r1"

    fake_flows.start_flow.side_effect = _api_error(404)
    out = server.flow_start("missing")
    assert "error" in out


def test_flow_next_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.next_step.return_value = {"run_id": "r1", "status": "in_progress", "step": {"id": "b"}}
    out = server.flow_next("r1", decision="sufficient")
    fake_flows.next_step.assert_called_once_with("r1", decision="sufficient")
    assert out["step"] == {"id": "b"}

    fake_flows.next_step.side_effect = _api_error(409, "checkpoint pendiente")
    out = server.flow_next("r1")
    assert "checkpoint pendiente" in out["error"]


def test_flow_approve_checkpoint_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.approve_checkpoint.return_value = {"run_id": "r1", "status": "in_progress"}
    out = server.flow_approve_checkpoint("r1")
    fake_flows.approve_checkpoint.assert_called_once_with("r1")
    assert out["run_id"] == "r1"


def test_flow_reject_checkpoint_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.reject_checkpoint.return_value = {"run_id": "r1", "step": {"id": "analyze"}}
    out = server.flow_reject_checkpoint("r1", "falta evidencia")
    fake_flows.reject_checkpoint.assert_called_once_with("r1", "falta evidencia")
    assert out["step"] == {"id": "analyze"}


def test_flow_abort_routes_to_client(fake_clients):
    _, _, fake_flows, _ = fake_clients
    fake_flows.abort_run.return_value = {"run_id": "r1", "status": "aborted"}
    out = server.flow_abort("r1", reason="ya no aplica")
    fake_flows.abort_run.assert_called_once_with("r1", reason="ya no aplica")
    assert out["status"] == "aborted"

    fake_flows.abort_run.side_effect = _api_error(409, "ya esta completed")
    out = server.flow_abort("r1")
    assert "ya esta completed" in out["error"]


def test_docs_archive_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.archive_document.return_value = {"id": "d1", "status": "archived"}
    out = server.docs_archive("d1")
    fake_docs.archive_document.assert_called_once_with("d1")
    assert out == {"document": {"id": "d1", "status": "archived"}}

    fake_docs.archive_document.side_effect = _api_error(404)
    out = server.docs_archive("missing")
    assert "error" in out


def test_docs_unarchive_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.unarchive_document.return_value = {"id": "d1", "status": "active"}
    out = server.docs_unarchive("d1")
    fake_docs.unarchive_document.assert_called_once_with("d1")
    assert out == {"document": {"id": "d1", "status": "active"}}


def test_docs_list_archived_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.list_archived_documents.return_value = [{"id": "d1"}]
    out = server.docs_list_archived(category="eco", limit=5, offset=1)
    fake_docs.list_archived_documents.assert_called_once_with(category="eco", limit=5, offset=1)
    assert out == {"documents": [{"id": "d1"}]}


def test_docs_history_routes_to_client(fake_clients):
    _, fake_docs, _, _ = fake_clients
    fake_docs.get_document_versions.return_value = [{"version_number": 1}]
    out = server.docs_history("d1")
    fake_docs.get_document_versions.assert_called_once_with("d1")
    assert out == {"versions": [{"version_number": 1}]}

    fake_docs.get_document_versions.side_effect = _api_error(404)
    out = server.docs_history("missing")
    assert "error" in out


# =============================================================================== auth_*


def test_auth_create_user_routes_to_client(fake_clients):
    _, _, _, fake_auth = fake_clients
    fake_auth.create_user.return_value = {"name": "alice", "access_level": "owner"}
    out = server.auth_create_user("alice", email="alice@example.com", access_level="owner")
    fake_auth.create_user.assert_called_once_with("alice", email="alice@example.com", access_level="owner")
    assert out == {"user": {"name": "alice", "access_level": "owner"}}

    fake_auth.create_user.side_effect = _api_error(403, "admin required")
    out = server.auth_create_user("bob")
    assert "error" in out


def test_auth_list_users_routes_to_client(fake_clients):
    _, _, _, fake_auth = fake_clients
    fake_auth.list_users.return_value = [{"name": "alice"}]
    out = server.auth_list_users()
    fake_auth.list_users.assert_called_once_with()
    assert out == {"users": [{"name": "alice"}]}

    fake_auth.list_users.side_effect = _conn_error()
    out = server.auth_list_users()
    assert "error" in out
    assert "cerebro-auth" in out["error"]


def test_auth_create_group_routes_to_client(fake_clients):
    _, _, _, fake_auth = fake_clients
    fake_auth.create_group.return_value = {"slug": "eng-team"}
    out = server.auth_create_group("eng-team", "Engineering")
    fake_auth.create_group.assert_called_once_with("eng-team", "Engineering")
    assert out == {"group": {"slug": "eng-team"}}

    fake_auth.create_group.side_effect = _api_error(409)
    out = server.auth_create_group("eng-team", "Engineering")
    assert "error" in out


def test_auth_set_group_scopes_routes_to_client(fake_clients):
    _, _, _, fake_auth = fake_clients
    fake_auth.set_group_scopes.return_value = {"slug": "eng-team", "allowed_modules": ["memory"]}
    out = server.auth_set_group_scopes(
        "eng-team", ["memory", "docs"], module_scopes={"memory": {"contexts": ["proyecto-x"]}}
    )
    fake_auth.set_group_scopes.assert_called_once_with(
        "eng-team", ["memory", "docs"], module_scopes={"memory": {"contexts": ["proyecto-x"]}}
    )
    assert out == {"group": {"slug": "eng-team", "allowed_modules": ["memory"]}}

    fake_auth.set_group_scopes.side_effect = _api_error(404)
    out = server.auth_set_group_scopes("missing", ["memory"])
    assert "error" in out


def test_auth_add_group_member_routes_to_client(fake_clients):
    _, _, _, fake_auth = fake_clients
    fake_auth.add_group_member.return_value = {"slug": "eng-team", "members": ["alice"]}
    out = server.auth_add_group_member("eng-team", "alice")
    fake_auth.add_group_member.assert_called_once_with("eng-team", "alice")
    assert out == {"group": {"slug": "eng-team", "members": ["alice"]}}

    fake_auth.add_group_member.side_effect = _api_error(403, "admin required")
    out = server.auth_add_group_member("eng-team", "bob")
    assert "error" in out


class TestAuthCreateToken:
    def test_routes_to_client_with_all_args(self, fake_clients):
        _, _, _, fake_auth = fake_clients
        fake_auth.create_token.return_value = {"name": "svc-1", "value": "secret-once"}
        out = server.auth_create_token(
            "svc-1",
            ["read", "write"],
            allowed_modules=["memory"],
            module_scopes={"memory": {"contexts": ["proyecto-x"]}},
            user="alice",
            access_level=None,
        )
        fake_auth.create_token.assert_called_once_with(
            "svc-1",
            ["read", "write"],
            allowed_modules=["memory"],
            module_scopes={"memory": {"contexts": ["proyecto-x"]}},
            user="alice",
            access_level=None,
        )
        assert out == {"token": {"name": "svc-1", "value": "secret-once"}}

    def test_service_token_with_access_level_routes_to_client(self, fake_clients):
        _, _, _, fake_auth = fake_clients
        fake_auth.create_token.return_value = {"name": "root-token", "value": "secret"}
        out = server.auth_create_token("root-token", ["admin"], allowed_modules=["memory", "docs", "flows"], access_level="admin")
        fake_auth.create_token.assert_called_once_with(
            "root-token",
            ["admin"],
            allowed_modules=["memory", "docs", "flows"],
            module_scopes=None,
            user=None,
            access_level="admin",
        )
        assert out["token"]["value"] == "secret"

    def test_conflicting_user_and_access_level_422_propagates_detail(self, fake_clients):
        _, _, _, fake_auth = fake_clients
        fake_auth.create_token.side_effect = _api_error(422, "user and access_level are mutually exclusive")
        out = server.auth_create_token("t1", ["read"], user="alice", access_level="admin")
        assert "mutually exclusive" in out["error"]

    def test_connection_error_becomes_error_dict(self, fake_clients):
        _, _, _, fake_auth = fake_clients
        fake_auth.create_token.side_effect = _conn_error()
        out = server.auth_create_token("t1", ["read"])
        assert "error" in out
        assert "cerebro-auth" in out["error"]


def test_auth_list_tokens_routes_to_client(fake_clients):
    _, _, _, fake_auth = fake_clients
    fake_auth.list_tokens.return_value = [{"name": "t1", "scopes": ["read"]}]
    out = server.auth_list_tokens()
    fake_auth.list_tokens.assert_called_once_with()
    assert out == {"tokens": [{"name": "t1", "scopes": ["read"]}]}

    fake_auth.list_tokens.side_effect = _api_error(401)
    out = server.auth_list_tokens()
    assert "error" in out


def test_auth_revoke_token_routes_to_client(fake_clients):
    _, _, _, fake_auth = fake_clients
    fake_auth.revoke_token.return_value = {"name": "t1", "status": "revoked"}
    out = server.auth_revoke_token("t1")
    fake_auth.revoke_token.assert_called_once_with("t1")
    assert out == {"name": "t1", "status": "revoked"}

    fake_auth.revoke_token.side_effect = _api_error(404)
    out = server.auth_revoke_token("missing")
    assert "error" in out


# =============================================================================== tool registry


def test_all_44_tools_are_registered():
    """The 10 memory_* + the 13 docs_* + the 13 flow_* (cerebro-flows engine:
    definition CRUD + execution) + the 8 new auth_* (cerebro-auth: users, groups,
    tokens) -- the exact inventory the user expects (ecosistema-cerebro.md SS10)."""
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
        "docs_archive",
        "docs_unarchive",
        "docs_list_archived",
        "docs_history",
        "flow_create_category",
        "flow_categories",
        "flow_validate",
        "flow_save",
        "flow_get",
        "flow_list",
        "flow_update",
        "flow_delete",
        "flow_start",
        "flow_next",
        "flow_approve_checkpoint",
        "flow_reject_checkpoint",
        "flow_abort",
        "auth_create_user",
        "auth_list_users",
        "auth_create_group",
        "auth_set_group_scopes",
        "auth_add_group_member",
        "auth_create_token",
        "auth_list_tokens",
        "auth_revoke_token",
    }
    assert len(tool_names) == 44
    for name in tool_names:
        assert hasattr(server, name), f"falta la tool {name}"
        assert callable(getattr(server, name))
