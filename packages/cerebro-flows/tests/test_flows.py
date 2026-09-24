"""Integration tests: category/flow definition CRUD and the execution engine
end-to-end (luisjdev-pendientes/cerebro-flows). They're skipped automatically if
DATABASE_URL/REDIS_URL don't respond (same pattern as
cerebro-docs/tests/test_documents.py).
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from cerebro_flows.api import create_app
from cerebro_flows.config import get_settings

from .cerebro_auth_helpers import add_user_to_group, insert_group, insert_token, insert_user, set_group_scopes
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
        """Walks the INC-22 flow from the design document end to end: start with
        prerequisites -> analyze -> investigate -> decision (sufficient) -> resolve
        (blocked by checkpoint until approved) -> end (completed)."""
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

        # invalid decision
        bad = client.post(f"/runs/{run_id}/next", json={"decision": "no-existe"}, headers=auth_headers)
        assert bad.status_code == 422, bad.text

        step = client.post(f"/runs/{run_id}/next", json={"decision": "sufficient"}, headers=auth_headers).json()
        assert step["step"]["id"] == "resolve"
        assert step["step"]["checkpoint"]["required"] is True

        # flow_next blocked until the checkpoint is approved
        blocked = client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers)
        assert blocked.status_code == 409, blocked.text

        approved = client.post(f"/runs/{run_id}/approve-checkpoint", headers=auth_headers)
        assert approved.status_code == 200, approved.text

        finished = client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers)
        assert finished.status_code == 200, finished.text
        assert finished.json() == {"run_id": run_id, "status": "completed", "step": None}

        # once completed, it no longer accepts any more transitions
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
        # checkpoint.on_reject of "resolve" in INC_22 is "analyze"
        assert rejected.json()["step"]["id"] == "analyze"

    def test_flow_without_tools_and_terminal_entry_completes_immediately(self, client, auth_headers):
        """MINIMAL_FLOW has no 'tools:' (no prerequisites) and its only step
        ('only') is already terminal -- start_run must complete it right away,
        not leave it stuck 'in_progress' on a step that can never be advanced."""
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


# --------------------------------------------------------------------------- ownership filter


class TestOwnershipFilter:
    """`principal.owner_filter` (cerebro_flows.auth): who can list/get/start a flow
    DEFINITION they didn't necessarily create. Fixtures are inserted directly into
    the shared `cerebro_auth` schema (see cerebro_auth_helpers) since token/user/
    group management no longer lives in this service."""

    @pytest.fixture()
    def scenario(self, client, auth_headers):
        """One category, two 'user'-level tokens (each the owner of one flow) that
        share a group, plus one 'owner'-level token for that same group."""
        dsn = get_settings().database_url

        async def setup():
            group_id = await insert_group(dsn)
            user1 = await insert_user(dsn, access_level="user")
            user2 = await insert_user(dsn, access_level="user")
            owner_user = await insert_user(dsn, access_level="owner")
            for uid in (user1, user2, owner_user):
                await add_user_to_group(dsn, uid, group_id)
            await set_group_scopes(dsn, group_id, allowed_modules=["flows"], module_scopes=None)

            token1 = await insert_token(dsn, name=f"owner-t1-{uuid.uuid4().hex[:8]}", user_id=user1, allowed_modules=["flows"])
            token2 = await insert_token(dsn, name=f"owner-t2-{uuid.uuid4().hex[:8]}", user_id=user2, allowed_modules=["flows"])
            owner_token = await insert_token(
                dsn, name=f"owner-t3-{uuid.uuid4().hex[:8]}", user_id=owner_user, allowed_modules=None
            )
            return token1, token2, owner_token

        token1, token2, owner_token = asyncio.run(setup())

        cat = _make_category(client, auth_headers)
        flow1 = _make_flow(client, {"Authorization": f"Bearer {token1}"}, cat)
        flow2 = _make_flow(client, {"Authorization": f"Bearer {token2}"}, cat)

        return {
            "category": cat,
            "flow1": flow1,
            "flow2": flow2,
            "headers1": {"Authorization": f"Bearer {token1}"},
            "headers2": {"Authorization": f"Bearer {token2}"},
            "owner_headers": {"Authorization": f"Bearer {owner_token}"},
        }

    def test_user_level_token_only_lists_its_own_flow(self, client, scenario):
        resp = client.get("/flows", params={"category": scenario["category"]}, headers=scenario["headers1"])
        assert resp.status_code == 200, resp.text
        codes = {f["code"] for f in resp.json()}
        assert scenario["flow1"]["code"] in codes
        assert scenario["flow2"]["code"] not in codes

    def test_user_level_token_cannot_get_or_start_someone_elses_flow(self, client, scenario):
        other_code = scenario["flow2"]["code"]
        get_resp = client.get(f"/flows/{other_code}", headers=scenario["headers1"])
        assert get_resp.status_code == 404, get_resp.text

        start_resp = client.post(f"/flows/{other_code}/start", headers=scenario["headers1"])
        assert start_resp.status_code == 404, start_resp.text

    def test_owner_level_token_sees_group_mates_flows(self, client, scenario):
        resp = client.get("/flows", params={"category": scenario["category"]}, headers=scenario["owner_headers"])
        assert resp.status_code == 200, resp.text
        codes = {f["code"] for f in resp.json()}
        assert scenario["flow1"]["code"] in codes
        assert scenario["flow2"]["code"] in codes

        get_resp = client.get(f"/flows/{scenario['flow2']['code']}", headers=scenario["owner_headers"])
        assert get_resp.status_code == 200, get_resp.text

        start_resp = client.post(f"/flows/{scenario['flow2']['code']}/start", headers=scenario["owner_headers"])
        assert start_resp.status_code == 200, start_resp.text

    def test_root_sees_everything(self, client, scenario, auth_headers):
        resp = client.get("/flows", params={"category": scenario["category"]}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        codes = {f["code"] for f in resp.json()}
        assert scenario["flow1"]["code"] in codes
        assert scenario["flow2"]["code"] in codes

        assert client.get(f"/flows/{scenario['flow1']['code']}", headers=auth_headers).status_code == 200
        assert client.get(f"/flows/{scenario['flow2']['code']}", headers=auth_headers).status_code == 200
