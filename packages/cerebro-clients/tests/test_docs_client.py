"""`DocsClient` debe enrutar cada metodo al endpoint/verbo/params correcto de la API
de cerebro-docs y no filtrar ninguna decision propia -- mismo criterio que
test_memory_client.py (ver su docstring), sin necesidad de la API viva."""

from __future__ import annotations

import pytest

from cerebro_clients.base import CerebroAPIError, CerebroConnectionError
from cerebro_clients.docs_client import DocsClient

from ._helpers import RaisingTransport, RecordingTransport


def make_client(transport, **kwargs) -> DocsClient:
    return DocsClient(base_url="http://test-docs", token="tok-abc", agent="test-agent", transport=transport, **kwargs)


def test_auth_and_agent_headers_are_set():
    transport = RecordingTransport()
    make_client(transport).health()
    headers = transport.last["headers"]
    assert headers["authorization"] == "Bearer tok-abc"
    assert headers["x-agent-name"] == "test-agent"


def test_health():
    transport = RecordingTransport(response_json={"status": "ok"})
    result = make_client(transport).health()
    assert transport.last["method"] == "GET"
    assert transport.last["path"] == "/health"
    assert result == {"status": "ok"}


class TestCategories:
    def test_create_category_minimal(self):
        transport = RecordingTransport()
        make_client(transport).create_category("eco", "Ecosistema")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/categories"
        assert transport.last["json"] == {"slug": "eco", "name": "Ecosistema"}

    def test_create_category_with_description(self):
        transport = RecordingTransport()
        make_client(transport).create_category("eco", "Ecosistema", description="algo")
        assert transport.last["json"] == {"slug": "eco", "name": "Ecosistema", "description": "algo"}

    def test_list_categories(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_categories()
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/categories"

    def test_update_category_only_sends_given_fields(self):
        transport = RecordingTransport()
        make_client(transport).update_category("eco", new_slug="ecosistema-nuevo")
        assert transport.last["method"] == "PATCH"
        assert transport.last["path"] == "/categories/eco"
        assert transport.last["json"] == {"slug": "ecosistema-nuevo"}

    def test_update_category_all_fields(self):
        transport = RecordingTransport()
        make_client(transport).update_category("eco", new_slug="eco2", name="N", description="D")
        assert transport.last["json"] == {"slug": "eco2", "name": "N", "description": "D"}

    def test_delete_category(self):
        transport = RecordingTransport()
        make_client(transport).delete_category("eco", force=True)
        assert transport.last["method"] == "DELETE"
        assert transport.last["path"] == "/categories/eco"
        assert transport.last["params"] == {"force": "true"}


class TestDocuments:
    def test_create_document_minimal(self):
        transport = RecordingTransport()
        make_client(transport).create_document("Titulo", "contenido", "eco")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/documents"
        assert transport.last["json"] == {"title": "Titulo", "content": "contenido", "category": "eco"}

    def test_create_document_with_explicit_slug(self):
        transport = RecordingTransport()
        make_client(transport).create_document("Titulo", "contenido", "eco", slug="mi-slug")
        assert transport.last["json"]["slug"] == "mi-slug"

    def test_get_document(self):
        transport = RecordingTransport()
        make_client(transport).get_document("eco", "mi-doc")
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/documents/eco/mi-doc"

    def test_list_documents_defaults(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_documents()
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/documents"
        assert transport.last["params"] == {"limit": "20", "offset": "0"}

    def test_list_documents_with_category_and_q_is_the_same_endpoint_as_search(self):
        # docs_list y docs_search (cerebro-mcp/cerebro-cli) son dos superficies sobre
        # ESTE mismo metodo/endpoint - no hay logica de negocio duplicada aqui.
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_documents(category="eco", q="busqueda", limit=5, offset=10)
        params = transport.last["params"]
        assert params == {"limit": "5", "offset": "10", "category": "eco", "q": "busqueda"}

    def test_update_document(self):
        transport = RecordingTransport()
        make_client(transport).update_document("doc-1", "Nuevo titulo", "nuevo contenido", "otra-cat")
        assert transport.last["method"] == "PATCH"
        assert transport.last["path"] == "/documents/doc-1"
        assert transport.last["json"] == {
            "title": "Nuevo titulo",
            "content": "nuevo contenido",
            "category": "otra-cat",
        }

    def test_update_document_with_slug(self):
        transport = RecordingTransport()
        make_client(transport).update_document("doc-1", "T", "C", "cat", slug="nuevo-slug")
        assert transport.last["json"]["slug"] == "nuevo-slug"

    def test_patch_section_defaults(self):
        transport = RecordingTransport()
        make_client(transport).patch_section("doc-1", "## Intro", "replace")
        assert transport.last["method"] == "PATCH"
        assert transport.last["path"] == "/documents/doc-1/section"
        assert transport.last["json"] == {
            "heading": "## Intro",
            "operation": "replace",
            "body": "",
            "create_if_missing": False,
            "new_heading_level": 2,
        }

    def test_patch_section_full(self):
        transport = RecordingTransport()
        make_client(transport).patch_section(
            "doc-1", "Nueva seccion", "append", body="texto nuevo", create_if_missing=True, new_heading_level=3
        )
        assert transport.last["json"] == {
            "heading": "Nueva seccion",
            "operation": "append",
            "body": "texto nuevo",
            "create_if_missing": True,
            "new_heading_level": 3,
        }

    def test_delete_document(self):
        transport = RecordingTransport()
        make_client(transport).delete_document("doc-1")
        assert transport.last["method"] == "DELETE"
        assert transport.last["path"] == "/documents/doc-1"


def test_get_stats():
    transport = RecordingTransport(response_json={"categories": 0, "documents": 0, "versions": 0})
    make_client(transport).get_stats()
    assert transport.last["method"] == "GET"
    assert transport.last["path"] == "/stats"


class TestTokens:
    def test_create_token_minimal(self):
        transport = RecordingTransport()
        make_client(transport).create_token("agente-x", ["read"])
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/tokens"
        assert transport.last["json"] == {"name": "agente-x", "scopes": ["read"]}

    def test_create_token_with_categories_and_value(self):
        transport = RecordingTransport()
        make_client(transport).create_token(
            "agente-x", ["read", "write"], allowed_categories=["eco"], value="cbrd_provided-secret"
        )
        assert transport.last["json"] == {
            "name": "agente-x",
            "scopes": ["read", "write"],
            "allowed_categories": ["eco"],
            "value": "cbrd_provided-secret",
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
        transport = RecordingTransport(response_json={"detail": "categoria inexistente"}, status_code=404)
        client = make_client(transport)
        with pytest.raises(CerebroAPIError) as exc_info:
            client.create_document("T", "C", "no-existe")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "categoria inexistente"

    def test_connection_failure_raises_cerebro_connection_error(self):
        client = make_client(RaisingTransport())
        with pytest.raises(CerebroConnectionError):
            client.health()
