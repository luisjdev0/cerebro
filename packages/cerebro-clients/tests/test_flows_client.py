"""`FlowsClient` must route each method to the correct endpoint/verb/params of the
cerebro-flows API -- same criterion as test_docs_client.py."""

from __future__ import annotations

import pytest

from cerebro_clients.base import CerebroAPIError, CerebroConnectionError
from cerebro_clients.flows_client import FlowsClient

from ._helpers import RaisingTransport, RecordingTransport


def make_client(transport, **kwargs) -> FlowsClient:
    return FlowsClient(base_url="http://test-flows", token="tok-abc", agent="test-agent", transport=transport, **kwargs)


def test_health():
    transport = RecordingTransport(response_json={"status": "ok"})
    result = make_client(transport).health()
    assert transport.last["path"] == "/health"
    assert result == {"status": "ok"}


class TestCategories:
    def test_create_category(self):
        transport = RecordingTransport()
        make_client(transport).create_category("incident", "INC", "Incidencias", description="algo")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/categories"
        assert transport.last["json"] == {"slug": "incident", "code": "INC", "name": "Incidencias", "description": "algo"}

    def test_list_categories(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_categories()
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/categories"


class TestFlowDefinitions:
    def test_validate_flow(self):
        transport = RecordingTransport(response_json={"valid": True})
        make_client(transport).validate_flow("metadata: {}")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/flows/validate"
        assert transport.last["json"] == {"yaml_content": "metadata: {}"}

    def test_create_flow_minimal(self):
        transport = RecordingTransport()
        make_client(transport).create_flow("incident", "metadata: {}")
        assert transport.last["path"] == "/flows"
        assert transport.last["json"] == {"category": "incident", "yaml_content": "metadata: {}"}

    def test_create_flow_with_explicit_code(self):
        transport = RecordingTransport()
        make_client(transport).create_flow("incident", "metadata: {}", code="INC-99")
        assert transport.last["json"]["code"] == "INC-99"

    def test_get_flow(self):
        transport = RecordingTransport()
        make_client(transport).get_flow("INC-22")
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/flows/INC-22"

    def test_list_flows_defaults(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_flows()
        assert transport.last["params"] == {"limit": "20", "offset": "0"}

    def test_update_flow(self):
        transport = RecordingTransport()
        make_client(transport).update_flow("INC-22", "metadata: {}")
        assert transport.last["method"] == "PATCH"
        assert transport.last["path"] == "/flows/INC-22"
        assert transport.last["json"] == {"yaml_content": "metadata: {}"}

    def test_delete_flow(self):
        transport = RecordingTransport()
        make_client(transport).delete_flow("INC-22")
        assert transport.last["method"] == "DELETE"
        assert transport.last["path"] == "/flows/INC-22"


class TestExecutionEngine:
    def test_start_flow(self):
        transport = RecordingTransport()
        make_client(transport).start_flow("INC-22")
        assert transport.last["method"] == "POST"
        assert transport.last["path"] == "/flows/INC-22/start"

    def test_next_step(self):
        transport = RecordingTransport()
        make_client(transport).next_step("run-1", decision="sufficient")
        assert transport.last["path"] == "/runs/run-1/next"
        assert transport.last["json"] == {"decision": "sufficient"}

    def test_approve_checkpoint(self):
        transport = RecordingTransport()
        make_client(transport).approve_checkpoint("run-1")
        assert transport.last["path"] == "/runs/run-1/approve-checkpoint"

    def test_reject_checkpoint(self):
        transport = RecordingTransport()
        make_client(transport).reject_checkpoint("run-1", "falta evidencia")
        assert transport.last["path"] == "/runs/run-1/reject-checkpoint"
        assert transport.last["json"] == {"reason": "falta evidencia"}

    def test_abort_run(self):
        transport = RecordingTransport()
        make_client(transport).abort_run("run-1", reason="ya no aplica")
        assert transport.last["path"] == "/runs/run-1/abort"
        assert transport.last["json"] == {"reason": "ya no aplica"}

    def test_get_run(self):
        transport = RecordingTransport()
        make_client(transport).get_run("run-1")
        assert transport.last["method"] == "GET"
        assert transport.last["path"] == "/runs/run-1"


def test_get_stats():
    transport = RecordingTransport(response_json={"categories": 0, "flows": 0, "runs": 0})
    make_client(transport).get_stats()
    assert transport.last["path"] == "/stats"


class TestTokens:
    def test_create_token_minimal(self):
        transport = RecordingTransport()
        make_client(transport).create_token("agente-x", ["read"])
        assert transport.last["json"] == {"name": "agente-x", "scopes": ["read"]}

    def test_list_tokens(self):
        transport = RecordingTransport(response_json=[])
        make_client(transport).list_tokens()
        assert transport.last["path"] == "/tokens"

    def test_revoke_token(self):
        transport = RecordingTransport()
        make_client(transport).revoke_token("agente-x")
        assert transport.last["method"] == "DELETE"
        assert transport.last["path"] == "/tokens/agente-x"


class TestErrorPropagation:
    def test_http_error_raises_cerebro_api_error(self):
        transport = RecordingTransport(response_json={"detail": "flow not found"}, status_code=404)
        client = make_client(transport)
        with pytest.raises(CerebroAPIError) as exc_info:
            client.get_flow("no-existe")
        assert exc_info.value.status_code == 404

    def test_connection_failure_raises_cerebro_connection_error(self):
        client = make_client(RaisingTransport())
        with pytest.raises(CerebroConnectionError):
            client.health()
