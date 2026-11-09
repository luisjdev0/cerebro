"""`/mcp` over streamable HTTP: per-request token, no environment fallback, no cross-caller leaks."""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.testclient import TestClient

from cerebro_mcp import http as mcp_http
from cerebro_mcp import server

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client: TestClient, method: str, params: dict | None, token: str | None, id_: int = 1):
    headers = dict(HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/mcp",
        headers=headers,
        content=json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}),
    )


@pytest.fixture(scope="module")
def client():
    # The session manager of a FastMCP instance can only run once per process, so the app is
    # created (and its lifespan entered) once for the whole module.
    with TestClient(mcp_http.create_app()) as c:
        yield c
    server.HTTP_MODE = False


@pytest.fixture
def seen(client, monkeypatch):
    """Replace the HTTP transport of the memory client with one that records the Authorization header."""
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"results": [], "scope_decision": {"mode": "global"}})

    transport = httpx.MockTransport(handler)
    real = server.MemoryClient
    monkeypatch.setattr(server, "_memory", server._ClientProxy(lambda *a, **kw: real(*a, transport=transport, **kw)))
    monkeypatch.setenv("CEREBRO_TOKEN", "env-token-must-be-ignored")
    return client, calls


def test_missing_token_is_401_before_any_mcp_processing(seen):
    client, calls = seen
    r = _rpc(client, "tools/list", None, token=None)
    assert r.status_code == 401
    assert "Bearer" in r.headers["www-authenticate"]
    assert calls == []


def test_lists_the_tools_with_a_token(seen):
    client, _ = seen
    r = _rpc(client, "tools/list", None, token="tok-a")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"memory_search", "docs_save", "flow_start", "auth_create_token"} <= names


def test_each_call_uses_its_own_callers_token_never_the_environment(seen):
    client, calls = seen
    for i, tok in enumerate(["tok-a", "tok-b", "tok-a"], start=1):
        r = _rpc(client, "tools/call", {"name": "memory_search", "arguments": {"query": "x"}}, token=tok, id_=i)
        assert r.status_code == 200, r.text
    assert calls == ["Bearer tok-a", "Bearer tok-b", "Bearer tok-a"]


def test_pending_disambiguation_is_not_shared_between_callers(seen, monkeypatch):
    client, _ = seen
    server._last_disambiguation_ids["tok-a"] = "pending-for-a"
    seen_resolved: list[str] = []
    real_resolve = server.MemoryClient.resolve_disambiguation
    monkeypatch.setattr(
        server.MemoryClient, "resolve_disambiguation", lambda self, pid, ctx: seen_resolved.append(pid) or {}
    )
    _rpc(client, "tools/call", {"name": "memory_search", "arguments": {"query": "x", "context": "c"}}, token="tok-b")
    assert seen_resolved == []  # tok-b must not consume tok-a's pending slot
    assert server._last_disambiguation_ids.get("tok-a") == "pending-for-a"
    monkeypatch.setattr(server.MemoryClient, "resolve_disambiguation", real_resolve)


def test_health_needs_no_token(seen):
    client, _ = seen
    assert client.get("/health").json() == {"status": "ok"}


def test_a_rejected_token_is_reported_in_terms_of_the_connection_not_the_environment(seen, monkeypatch):
    client, _ = seen

    def deny(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid token"})

    real = server.MemoryClient
    monkeypatch.setattr(
        server, "_memory", server._ClientProxy(lambda *a, **kw: real(*a, transport=httpx.MockTransport(deny), **kw))
    )
    r = _rpc(client, "tools/call", {"name": "memory_search", "arguments": {"query": "x"}}, token="revoked")
    text = r.json()["result"]["content"][0]["text"]
    assert "Authorization header" in text and "CEREBRO_TOKEN" not in text
