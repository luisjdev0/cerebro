"""Mermaid rendering of a flow definition and its API endpoints."""

from __future__ import annotations

from cerebro_flows.diagram import flow_references, flow_to_mermaid
from cerebro_flows.schema import validate_flow_yaml

from .test_flows import MINIMAL_FLOW, _make_category, _make_flow, auth_headers, client  # noqa: F401

RICH = """
metadata:
  name: Rich
  category: test
  references:
    - cerebro-docs://flows-prompts/base
entry: analyze
tools: [read_file, create_ticket]
procedure:
  - id: analyze
    type: task
    description: Look at "the" incident
    next: decide
  - id: decide
    type: decision
    branches:
      enough: fan-a
      not-enough: analyze
  - id: fan-a
    type: delegate
    delegate: {agent_type: researcher, prompt_ref: "cerebro-docs://flows-prompts/researcher", parallel_group: g1}
    next: resolve
  - id: fan-b
    type: delegate
    delegate: {agent_type: researcher, parallel_group: g1}
    next: resolve
  - id: resolve
    type: task
    checkpoint: {required: true, prompt: "ok?", on_reject: analyze}
    next: end
  - id: end
    type: task
    terminal: true
"""


def test_mermaid_shapes_edges_and_checkpoint():
    out = flow_to_mermaid(validate_flow_yaml(RICH))
    assert out.startswith("flowchart TD\n")
    assert "start__ --> prerequisites__" in out and "prerequisites__ --> s_analyze" in out
    assert 's_decide{"<b>decide</b>"}' in out  # decision diamond
    assert 's_fan_a[["<b>fan-a</b>"]]' in out  # delegate subroutine
    assert 's_end(["<b>end</b>"])' in out  # terminal stadium
    assert "s_decide -->|enough| s_fan_a" in out and "s_decide -->|not-enough| s_analyze" in out
    assert 'subgraph pg_g1["parallel: g1"]' in out
    assert "s_resolve -->|approved| s_end" in out and "s_resolve -.->|rejected| s_analyze" in out
    assert "class s_resolve checkpoint" in out
    assert "#quot;the#quot;" in out  # quotes in descriptions cannot break the syntax


def test_mermaid_without_tools_starts_at_entry():
    out = flow_to_mermaid(validate_flow_yaml(MINIMAL_FLOW))
    assert "start__ --> s_only" in out and "prerequisites__" not in out


def test_references_from_metadata_and_delegate_prompts():
    refs = flow_references(validate_flow_yaml(RICH))
    assert refs == [
        {"ref": "cerebro-docs://flows-prompts/base", "kind": "metadata", "step_id": None},
        {"ref": "cerebro-docs://flows-prompts/researcher", "kind": "prompt", "step_id": "fan-a"},
    ]


class TestEndpoints:
    def test_diagram_current_and_old_version(self, client, auth_headers):  # noqa: F811
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        client.patch(f"/flows/{flow['code']}", json={"yaml_content": RICH}, headers=auth_headers)
        now = client.get(f"/flows/{flow['code']}/diagram", headers=auth_headers)
        assert now.status_code == 200, now.text
        assert now.json()["version"] == 2 and now.json()["format"] == "mermaid" and "s_decide" in now.json()["diagram"]
        old = client.get(f"/flows/{flow['code']}/diagram", params={"version": 1}, headers=auth_headers).json()
        assert old["version"] == 1 and "s_decide" not in old["diagram"]
        assert client.get(f"/flows/{flow['code']}/diagram", params={"version": 9}, headers=auth_headers).status_code == 404
        assert client.get(f"/flows/{flow['code']}/diagram", params={"format": "svg"}, headers=auth_headers).status_code == 422
        assert client.get("/flows/NOPE-1/diagram", headers=auth_headers).status_code == 404

    def test_references_endpoint(self, client, auth_headers):  # noqa: F811
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat, yaml_content=RICH)
        resp = client.get(f"/flows/{flow['code']}/references", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert [r["kind"] for r in resp.json()] == ["metadata", "prompt"]
