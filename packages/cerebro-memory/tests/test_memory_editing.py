"""Integration test: PATCH /memories/{id} metadata edits (in place) vs content edits
(new version), and PATCH /contexts/{slug}. Skipped without a reachable Postgres."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_memory.api import create_app
from cerebro_memory.config import get_settings


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
        pytest.skip("Postgres not reachable at DATABASE_URL")
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="module")
def headers():
    return {"Authorization": f"Bearer {get_settings().api_token}"}


@pytest.fixture()
def ctx_and_memory(client, headers):
    slug = f"edit-ctx-{uuid.uuid4().hex[:8]}"
    assert client.post("/contexts", json={"slug": slug, "name": "Edit", "kind": "domain"}, headers=headers).status_code == 201
    resp = client.post(
        "/memories", json={"content": "Original fact", "context": slug, "type": "semantic", "importance": 0.5}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return slug, resp.json()


def test_metadata_edit_is_in_place(client, headers, ctx_and_memory):
    _, memory = ctx_and_memory
    resp = client.patch(
        f"/memories/{memory['id']}",
        json={"importance": 0.9, "title": "New title", "type": "decision", "confidence": 0.7},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == memory["id"]  # no new version
    assert body["status"] == "active"
    # importance/confidence are float4 columns, so compare with a tolerance
    assert body["importance"] == pytest.approx(0.9)
    assert body["confidence"] == pytest.approx(0.7)
    assert (body["title"], body["type"]) == ("New title", "decision")
    assert body["content"] == "Original fact"


def test_content_edit_versions_and_carries_metadata(client, headers, ctx_and_memory):
    _, memory = ctx_and_memory
    resp = client.patch(f"/memories/{memory['id']}", json={"content": "Changed fact", "importance": 0.8}, headers=headers)
    assert resp.status_code == 200, resp.text
    new = resp.json()
    assert new["id"] != memory["id"]
    assert new["importance"] == pytest.approx(0.8)
    old = client.get(f"/memories/{memory['id']}", headers=headers).json()
    assert old["status"] == "superseded"
    assert old["superseded_by"] == new["id"]


def test_empty_and_invalid_edits_are_rejected(client, headers, ctx_and_memory):
    _, memory = ctx_and_memory
    url = f"/memories/{memory['id']}"
    assert client.patch(url, json={}, headers=headers).status_code == 422
    assert client.patch(url, json={"importance": 1.5}, headers=headers).status_code == 422
    assert client.patch(url, json={"type": "bogus"}, headers=headers).status_code == 422
    assert client.patch(url, json={"content": "Original fact"}, headers=headers).status_code == 422  # nothing changes


def test_superseded_memory_cannot_be_edited(client, headers, ctx_and_memory):
    _, memory = ctx_and_memory
    client.patch(f"/memories/{memory['id']}", json={"content": "v2"}, headers=headers)
    assert client.patch(f"/memories/{memory['id']}", json={"importance": 0.1}, headers=headers).status_code == 409


def test_update_context(client, headers, ctx_and_memory):
    slug, _ = ctx_and_memory
    resp = client.patch(f"/contexts/{slug}", json={"name": "Renamed", "description": "Now described"}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["slug"], body["name"], body["kind"], body["description"]) == (slug, "Renamed", "domain", "Now described")
    assert client.patch(f"/contexts/{slug}", json={}, headers=headers).status_code == 422
    assert client.patch(f"/contexts/{slug}", json={"name": None}, headers=headers).status_code == 422
    assert client.patch("/contexts/does-not-exist", json={"name": "x"}, headers=headers).status_code == 404
