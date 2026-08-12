"""Integration test: PATCH /memories/{id} supersedes instead of editing in place,
and the superseded row drops out of default search results.

Skipped automatically if DATABASE_URL is not reachable (e.g. `docker compose up -d`
was not run before `pytest`).
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_memory.api import create_app
from cerebro_memory.config import get_settings


def _db_reachable(dsn: str) -> bool:
    # generous timeout: on Windows the first connection through a fresh event loop
    # (proactor) can take a couple seconds even when Postgres is up and healthy.
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


def test_update_supersedes_old_memory_and_excludes_it_from_search(client, auth_headers):
    slug = f"test-ctx-{uuid.uuid4().hex[:8]}"

    resp = client.post(
        "/contexts",
        json={"slug": slug, "name": "Test context", "kind": "domain", "description": "pytest scratch context"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    resp = client.post(
        "/memories",
        json={
            "content": "Gaste 200 en el supermercado esta semana",
            "context": slug,
            "type": "episodic",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    old_memory = resp.json()
    old_id = old_memory["id"]
    assert old_memory["status"] == "active"

    resp = client.get(
        "/memories/search",
        params={"q": "cuanto gaste en el supermercado", "context": slug},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["results"]]
    assert old_id in ids

    resp = client.patch(
        f"/memories/{old_id}",
        json={"content": "Gaste 250 en el supermercado esta semana (corregido)"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    new_memory = resp.json()
    new_id = new_memory["id"]
    assert new_id != old_id
    assert new_memory["status"] == "active"
    assert new_memory["content"].startswith("Gaste 250")

    resp = client.get(
        "/memories/search",
        params={"q": "cuanto gaste en el supermercado", "context": slug},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    ids_after = [r["id"] for r in resp.json()["results"]]
    assert old_id not in ids_after
    assert new_id in ids_after

    resp = client.get(
        "/memories/search",
        params={"q": "cuanto gaste en el supermercado", "context": slug, "include_superseded": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    ids_with_superseded = [r["id"] for r in resp.json()["results"]]
    assert old_id in ids_with_superseded


def test_creating_memory_with_credential_is_rejected(client, auth_headers):
    slug = f"test-ctx-{uuid.uuid4().hex[:8]}"
    client.post(
        "/contexts",
        json={"slug": slug, "name": "Test context", "kind": "domain"},
        headers=auth_headers,
    )
    resp = client.post(
        "/memories",
        json={
            "content": "mi AWS_SECRET=AKIAIOSFODNN7EXAMPLE",
            "context": slug,
            "type": "semantic",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "secret://" in resp.json()["detail"]
