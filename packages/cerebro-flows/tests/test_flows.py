"""Integration tests: CRUD de categorias/definiciones de flujo y el motor de
ejecucion end-to-end (luisjdev-pendientes/cerebro-flows). Se saltan automaticamente
si DATABASE_URL/REDIS_URL no responden (mismo patron que cerebro-docs/tests/test_documents.py).
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_flows.api import create_app
from cerebro_flows.config import get_settings

from .test_schema import INC_22


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
def auth_headers():
    return {"Authorization": f"Bearer {get_settings().api_token}"}


def _make_category(client, auth_headers, **overrides) -> str:
    slug = overrides.pop("slug", f"test-cat-{uuid.uuid4().hex[:8]}")
    code = overrides.pop("code", f"T{uuid.uuid4().hex[:4].upper()}")
    body = {"slug": slug, "code": code, "name": overrides.pop("name", "Categoria de prueba"), **overrides}
    resp = client.post("/categories", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return slug


def _make_flow(client, auth_headers, category: str, *, yaml_content=None, code=None):
    yaml_content = yaml_content or MINIMAL_FLOW
    body = {"category": category, "yaml_content": yaml_content}
    if code is not None:
        body["code"] = code
    resp = client.post("/flows", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


MINIMAL_FLOW = """
metadata:
  name: Flujo minimo de prueba
  category: test
entry: only
procedure:
  - id: only
    type: task
    terminal: true
"""


# --------------------------------------------------------------------------- categories


class TestCategoriesCrud:
    def test_create_and_list_category(self, client, auth_headers):
        slug = _make_category(client, auth_headers, name="Incidencias")
        listed = client.get("/categories", headers=auth_headers)
        assert listed.status_code == 200, listed.text
        assert slug in {c["slug"] for c in listed.json()}

    def test_duplicate_slug_is_409(self, client, auth_headers):
        slug = _make_category(client, auth_headers)
        resp = client.post(
            "/categories", json={"slug": slug, "code": "ZZZ", "name": "otra"}, headers=auth_headers
        )
        assert resp.status_code == 409, resp.text


# --------------------------------------------------------------------------- flow validate/CRUD


class TestFlowValidate:
    def test_valid_yaml_passes(self, client, auth_headers):
        resp = client.post("/flows/validate", json={"yaml_content": MINIMAL_FLOW}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"valid": True}

    def test_invalid_yaml_is_422_with_specific_detail(self, client, auth_headers):
        bad = MINIMAL_FLOW.replace("entry: only", "entry: nowhere")
        resp = client.post("/flows/validate", json={"yaml_content": bad}, headers=auth_headers)
        assert resp.status_code == 422, resp.text
        assert "no existe en 'procedure'" in resp.json()["detail"]


class TestFlowCrud:
    def test_create_autogenerates_code_from_category(self, client, auth_headers):
        cat = _make_category(client, auth_headers, code="AUTO")
        flow = _make_flow(client, auth_headers, cat)
        assert flow["code"].startswith("AUTO-")
        assert flow["current_version"] == 1
        assert flow["yaml_content"] == MINIMAL_FLOW

    def test_create_with_explicit_code(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat, code=f"CUSTOM-{uuid.uuid4().hex[:6]}")
        assert flow["code"].startswith("CUSTOM-")

    def test_sequential_codes_increment(self, client, auth_headers):
        cat = _make_category(client, auth_headers, code="SEQ")
        first = _make_flow(client, auth_headers, cat)
        second = _make_flow(client, auth_headers, cat)
        n1 = int(first["code"].split("-")[1])
        n2 = int(second["code"].split("-")[1])
        assert n2 == n1 + 1

    def test_create_rejects_invalid_yaml(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        bad = MINIMAL_FLOW.replace("entry: only", "entry: nowhere")
        resp = client.post("/flows", json={"category": cat, "yaml_content": bad}, headers=auth_headers)
        assert resp.status_code == 422, resp.text

    def test_get_by_code(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        resp = client.get(f"/flows/{flow['code']}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == flow["id"]

    def test_update_creates_new_version_and_snapshots(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        new_yaml = INC_22
        resp = client.patch(f"/flows/{flow['code']}", json={"yaml_content": new_yaml}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["current_version"] == 2
        assert "Procesamiento de incidencia" in resp.json()["yaml_content"]

    def test_delete_flow(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        resp = client.delete(f"/flows/{flow['code']}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert client.get(f"/flows/{flow['code']}", headers=auth_headers).status_code == 404


# --------------------------------------------------------------------------- execution engine


class TestExecutionEngine:
    def test_full_inc_22_walkthrough(self, client, auth_headers):
        """Recorre el flujo INC-22 del documento de diseno de punta a punta: arranque
        con prerequisitos -> analyze -> investigate -> decision (sufficient) ->
        resolve (bloqueado por checkpoint hasta aprobarlo) -> end (completed)."""
        cat = _make_category(client, auth_headers, code="INC")
        flow = _make_flow(client, auth_headers, cat, yaml_content=INC_22)

        start = client.post(f"/flows/{flow['code']}/start", headers=auth_headers)
        assert start.status_code == 200, start.text
        run = start.json()
        run_id = run["run_id"]
        assert run["status"] == "in_progress"
        assert run["step"]["id"] == "__prerequisites__"
        assert set(run["step"]["tools_required"]) == {"search_incidents", "read_file", "create_ticket"}

        step = client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers).json()
        assert step["step"]["id"] == "analyze"

        step = client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers).json()
        assert step["step"]["id"] == "investigate"

        step = client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers).json()
        assert step["step"]["id"] == "decision"
        assert set(step["step"]["branches"].keys()) == {"sufficient", "insufficient"}

        # decision invalida
        bad = client.post(f"/runs/{run_id}/next", json={"decision": "no-existe"}, headers=auth_headers)
        assert bad.status_code == 422, bad.text

        step = client.post(f"/runs/{run_id}/next", json={"decision": "sufficient"}, headers=auth_headers).json()
        assert step["step"]["id"] == "resolve"
        assert step["step"]["checkpoint"]["required"] is True

        # flow_next bloqueado hasta aprobar el checkpoint
        blocked = client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers)
        assert blocked.status_code == 409, blocked.text

        approved = client.post(f"/runs/{run_id}/approve-checkpoint", headers=auth_headers)
        assert approved.status_code == 200, approved.text

        finished = client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers)
        assert finished.status_code == 200, finished.text
        assert finished.json() == {"run_id": run_id, "status": "completed", "step": None}

        # una vez completado, ya no admite mas transiciones
        again = client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers)
        assert again.status_code == 409, again.text

    def test_checkpoint_rejection_routes_to_on_reject(self, client, auth_headers):
        cat = _make_category(client, auth_headers, code="INC2")
        flow = _make_flow(client, auth_headers, cat, yaml_content=INC_22)
        run_id = client.post(f"/flows/{flow['code']}/start", headers=auth_headers).json()["run_id"]

        for _ in range(3):
            client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers)
        client.post(f"/runs/{run_id}/next", json={"decision": "sufficient"}, headers=auth_headers)

        rejected = client.post(
            f"/runs/{run_id}/reject-checkpoint", json={"reason": "falta evidencia"}, headers=auth_headers
        )
        assert rejected.status_code == 200, rejected.text
        # checkpoint.on_reject de "resolve" en INC_22 es "analyze"
        assert rejected.json()["step"]["id"] == "analyze"

    def test_flow_without_tools_and_terminal_entry_completes_immediately(self, client, auth_headers):
        """MINIMAL_FLOW no tiene 'tools:' (sin prerequisitos) y su unico step
        ('only') ya es terminal -- start_run debe completarlo de una vez, no
        dejarlo 'in_progress' colgado en un step que nunca se va a poder avanzar."""
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat, yaml_content=MINIMAL_FLOW)
        start = client.post(f"/flows/{flow['code']}/start", headers=auth_headers).json()
        assert start["status"] == "completed"
        assert start["step"] is None

    def test_start_unknown_flow_is_404(self, client, auth_headers):
        resp = client.post("/flows/DOES-NOT-EXIST/start", headers=auth_headers)
        assert resp.status_code == 404, resp.text

    def test_next_unknown_run_is_404(self, client, auth_headers):
        resp = client.post(f"/runs/{uuid.uuid4()}/next", json={}, headers=auth_headers)
        assert resp.status_code == 404, resp.text

    def test_abort_marks_run_aborted(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat, yaml_content=INC_22)
        run_id = client.post(f"/flows/{flow['code']}/start", headers=auth_headers).json()["run_id"]

        aborted = client.post(f"/runs/{run_id}/abort", json={"reason": "ya no aplica"}, headers=auth_headers)
        assert aborted.status_code == 200, aborted.text
        assert aborted.json()["status"] == "aborted"

        state = client.get(f"/runs/{run_id}", headers=auth_headers)
        assert state.status_code == 200, state.text
        assert state.json()["status"] == "aborted"

        again = client.post(f"/runs/{run_id}/abort", json={}, headers=auth_headers)
        assert again.status_code == 409, again.text

    def test_get_run_state_reflects_current_step(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat, yaml_content=INC_22)
        run_id = client.post(f"/flows/{flow['code']}/start", headers=auth_headers).json()["run_id"]

        state = client.get(f"/runs/{run_id}", headers=auth_headers)
        assert state.status_code == 200, state.text
        assert state.json()["step"]["id"] == "__prerequisites__"

        client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers)
        state = client.get(f"/runs/{run_id}", headers=auth_headers)
        assert state.json()["step"]["id"] == "analyze"


# --------------------------------------------------------------------------- stats


class TestStats:
    def test_stats_counts_increase(self, client, auth_headers):
        before = client.get("/stats", headers=auth_headers).json()
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        client.post(f"/flows/{flow['code']}/start", headers=auth_headers)
        after = client.get("/stats", headers=auth_headers).json()

        assert after["categories"] == before["categories"] + 1
        assert after["flows"] == before["flows"] + 1
        assert after["runs"] == before["runs"] + 1
