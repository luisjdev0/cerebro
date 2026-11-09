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

from cerebro_flows import engine
from cerebro_flows.api import create_app
from cerebro_flows.config import get_settings
from cerebro_flows.db import create_redis

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
        assert "does not exist in 'procedure'" in resp.json()["detail"]


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

    def test_delete_flow_with_recorded_runs_cascades_instead_of_500(self, client, auth_headers):
        # Regression test: found live in production (2026-09-27) -- deleting a
        # flow that had at least one run in flow_runs raised an unhandled
        # asyncpg.exceptions.ForeignKeyViolationError (500), because
        # flow_runs.definition_id's FK was missing ON DELETE CASCADE even though
        # this was already documented/promised behavior everywhere else (the
        # CLI's confirmation prompt, the MCP flow_delete docstring). Fixed in
        # 003_flow_runs_cascade_delete.sql.
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        client.post(f"/flows/{flow['code']}/start", headers=auth_headers)

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
        by_cat = {c["slug"]: c["count"] for c in after["flows_by_category"]}
        assert by_cat[cat] == 1
        assert all(c["code"] for c in after["flows_by_category"])


class TestActivity:
    def test_activity_counts_definitions_and_runs(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        client.post(f"/flows/{flow['code']}/start", headers=auth_headers)

        resp = client.get("/activity", params={"days": 7}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        days = resp.json()["days"]
        assert sum(d["created"] for d in days) >= 1  # definitions
        assert sum(d["updated"] for d in days) >= 1  # runs started

    def test_activity_years_and_year_filter(self, client, auth_headers):
        _make_flow(client, auth_headers, _make_category(client, auth_headers))
        resp = client.get("/activity", params={"days": 7}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        year = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).year
        assert year in resp.json()["years"]
        # a calendar year overrides the rolling window; a year with no data is empty
        assert client.get("/activity", params={"year": year}, headers=auth_headers).json()["days"]
        assert client.get("/activity", params={"year": 2001}, headers=auth_headers).json()["days"] == []
        assert client.get("/activity", params={"year": 1999}, headers=auth_headers).status_code == 422

    def test_activity_rejects_out_of_range_days(self, client, auth_headers):
        assert client.get("/activity", params={"days": 0}, headers=auth_headers).status_code == 422


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


# --------------------------------------------------------------------------- portal listings


GATED_FLOW = """
metadata:
  name: Flujo con aprobacion
  category: test
entry: first
procedure:
  - id: first
    type: task
    checkpoint:
      required: true
      prompt: Aprobar antes de continuar
      on_reject: first
    next: last
  - id: last
    type: task
    terminal: true
"""


class TestPortalListings:
    """`/flows/browse`, `/flows/{code}/versions`, `/runs`, `/runs/{id}/events`
    (portal-only endpoints) and the ownership gate on run endpoints."""

    def test_browse_flows_search_sort_and_pagination(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        codes = [_make_flow(client, auth_headers, cat)["code"] for _ in range(3)]

        resp = client.get("/flows/browse", params={"category": cat, "limit": 2, "sort": "code", "order": "asc"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        assert [f["code"] for f in body["items"]] == sorted(codes)[:2]
        assert body["items"][0]["category"] == cat

        page2 = client.get("/flows/browse", params={"category": cat, "limit": 2, "offset": 2, "sort": "code", "order": "asc"}, headers=auth_headers).json()
        assert [f["code"] for f in page2["items"]] == sorted(codes)[2:]

        hit = client.get("/flows/browse", params={"category": cat, "q": codes[0].lower()}, headers=auth_headers).json()
        assert [f["code"] for f in hit["items"]] == [codes[0]]
        miss = client.get("/flows/browse", params={"category": cat, "q": "zzz-no-existe"}, headers=auth_headers).json()
        assert miss == {"items": [], "total": 0}
        # LIKE wildcards in the search text are literal
        assert client.get("/flows/browse", params={"category": cat, "q": "%"}, headers=auth_headers).json()["total"] == 0

    def test_browse_is_not_shadowed_by_flow_code_route(self, client, auth_headers):
        assert client.get("/flows/browse", headers=auth_headers).status_code == 200

    def test_flow_versions_newest_first(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        upd = client.patch(
            f"/flows/{flow['code']}", json={"yaml_content": MINIMAL_FLOW.replace("minimo", "editado")}, headers=auth_headers
        )
        assert upd.status_code == 200, upd.text
        versions = client.get(f"/flows/{flow['code']}/versions", headers=auth_headers)
        assert versions.status_code == 200, versions.text
        assert [v["version_number"] for v in versions.json()] == [2, 1]
        assert "editado" in versions.json()[0]["yaml_content"]
        assert client.get("/flows/NO-EXISTE/versions", headers=auth_headers).status_code == 404

    def test_runs_listing_live_position_and_awaiting_queue(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        gated = _make_flow(client, auth_headers, cat, yaml_content=GATED_FLOW)
        run_id = client.post(f"/flows/{gated['code']}/start", headers=auth_headers).json()["run_id"]

        def row(params=None):
            items = client.get("/runs", params={"category": cat, **(params or {})}, headers=auth_headers).json()["items"]
            return next(i for i in items if i["run_id"] == run_id)

        # no tools required, so the run starts straight at the gated step
        r = row()
        assert (r["status"], r["current_step_id"], r["awaiting_approval"], r["expired"]) == ("in_progress", "first", True, False)
        assert r["flow_code"] == gated["code"] and r["category"] == cat
        queue = client.get("/runs", params={"category": cat, "awaiting": True}, headers=auth_headers).json()
        assert [i["run_id"] for i in queue["items"]] == [run_id] and queue["total"] == 1

        client.post(f"/runs/{run_id}/approve-checkpoint", headers=auth_headers)
        assert row()["awaiting_approval"] is False
        assert client.get("/runs", params={"category": cat, "awaiting": True}, headers=auth_headers).json()["total"] == 0

        client.post(f"/runs/{run_id}/abort", json={}, headers=auth_headers)
        r = row({"status": "aborted"})
        assert r["status"] == "aborted" and r["completed_at"] is not None and r["current_step_id"] is None
        assert client.get("/runs", params={"category": cat, "status": "in_progress"}, headers=auth_headers).json()["total"] == 0

    def test_runs_listing_flags_expired_pointer(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat, yaml_content=GATED_FLOW)
        run_id = client.post(f"/flows/{flow['code']}/start", headers=auth_headers).json()["run_id"]

        async def expire():
            client_ = create_redis(get_settings())
            try:
                await client_.delete(engine._redis_key(run_id))
            finally:
                await client_.aclose()

        asyncio.run(expire())
        items = client.get("/runs", params={"category": cat}, headers=auth_headers).json()["items"]
        r = next(i for i in items if i["run_id"] == run_id)
        assert r["expired"] is True and r["awaiting_approval"] is False and r["current_step_id"] is None

    def test_run_events_in_order(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat, yaml_content=GATED_FLOW)
        run_id = client.post(f"/flows/{flow['code']}/start", headers=auth_headers).json()["run_id"]
        client.post(f"/runs/{run_id}/next", json={}, headers=auth_headers)
        client.post(f"/runs/{run_id}/approve-checkpoint", headers=auth_headers)
        events = client.get(f"/runs/{run_id}/events", headers=auth_headers)
        assert events.status_code == 200, events.text
        kinds = [e["event_type"] for e in events.json()]
        assert kinds[-1] == "checkpoint_approved" and "step_entered" in kinds
        assert client.get(f"/runs/{uuid.uuid4()}/events", headers=auth_headers).status_code == 404

    def test_run_endpoints_enforce_ownership(self, client, auth_headers):
        dsn = get_settings().database_url

        async def setup():
            u1 = await insert_user(dsn, access_level="user")
            u2 = await insert_user(dsn, access_level="user")
            t1 = await insert_token(dsn, name=f"run-t1-{uuid.uuid4().hex[:8]}", user_id=u1, allowed_modules=["flows"])
            t2 = await insert_token(dsn, name=f"run-t2-{uuid.uuid4().hex[:8]}", user_id=u2, allowed_modules=["flows"])
            return t1, t2

        t1, t2 = asyncio.run(setup())
        h1, h2 = {"Authorization": f"Bearer {t1}"}, {"Authorization": f"Bearer {t2}"}
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, h1, cat, yaml_content=GATED_FLOW)
        run_id = client.post(f"/flows/{flow['code']}/start", headers=h1).json()["run_id"]

        # someone else's run is reported as not found on every run endpoint
        assert client.get(f"/runs/{run_id}", headers=h2).status_code == 404
        assert client.get(f"/runs/{run_id}/events", headers=h2).status_code == 404
        assert client.post(f"/runs/{run_id}/next", json={}, headers=h2).status_code == 404
        assert client.post(f"/runs/{run_id}/approve-checkpoint", headers=h2).status_code == 404
        assert client.post(f"/runs/{run_id}/abort", json={}, headers=h2).status_code == 404
        # ...is not listed...
        listed = client.get("/runs", params={"category": cat}, headers=h2).json()
        assert listed == {"items": [], "total": 0}
        # ...while the owner still has full access
        assert client.get(f"/runs/{run_id}", headers=h1).status_code == 200
        assert client.get("/runs", params={"category": cat}, headers=h1).json()["total"] == 1


class TestCategoryManagement:
    def test_patch_name_and_description_keeps_slug_and_code(self, client, auth_headers):
        cat = _make_category(client, auth_headers, code="CMG")
        resp = client.patch(f"/categories/{cat}", json={"name": "New name", "description": "Description"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["slug"], body["code"], body["name"], body["description"]) == (cat, "CMG", "New name", "Description")
        cleared = client.patch(f"/categories/{cat}", json={"description": "  "}, headers=auth_headers).json()
        assert cleared["description"] is None and cleared["name"] == "New name"

    def test_patch_rejects_empty_unknown_fields_and_missing_category(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        assert client.patch(f"/categories/{cat}", json={}, headers=auth_headers).status_code == 422
        assert client.patch(f"/categories/{cat}", json={"code": "ZZZ"}, headers=auth_headers).status_code == 422
        assert client.patch("/categories/no-existe", json={"name": "x"}, headers=auth_headers).status_code == 404

    def test_delete_empty_category(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        resp = client.delete(f"/categories/{cat}", headers=auth_headers)
        assert resp.status_code == 200 and resp.json() == {"slug": cat, "status": "deleted", "flows_deleted": 0}
        assert client.delete(f"/categories/{cat}", headers=auth_headers).status_code == 404

    def test_delete_with_flows_needs_force_and_cascades(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        run_id = client.post(f"/flows/{flow['code']}/start", headers=auth_headers).json()["run_id"]
        blocked = client.delete(f"/categories/{cat}", headers=auth_headers)
        assert blocked.status_code == 409 and "1 flows" in blocked.text
        assert client.get(f"/flows/{flow['code']}", headers=auth_headers).status_code == 200
        done = client.delete(f"/categories/{cat}", params={"force": True}, headers=auth_headers)
        assert done.status_code == 200 and done.json()["flows_deleted"] == 1
        assert client.get(f"/flows/{flow['code']}", headers=auth_headers).status_code == 404
        assert client.get(f"/runs/{run_id}/events", headers=auth_headers).status_code == 404

    def test_delete_requires_admin_scope(self, client, auth_headers):
        dsn = get_settings().database_url

        async def setup():
            uid = await insert_user(dsn, access_level="user")
            return await insert_token(dsn, name=f"cat-t-{uuid.uuid4().hex[:8]}", user_id=uid, allowed_modules=["flows"])

        token = asyncio.run(setup())
        cat = _make_category(client, auth_headers)
        resp = client.delete(f"/categories/{cat}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403), resp.text

    def test_delete_allowed_for_admin_access_level_without_admin_scope(self, client, auth_headers):
        """The `admin` scope only exists on the root token; an `admin` user with a read/write
        token (a portal session) must still be able to delete a category."""
        dsn = get_settings().database_url

        async def setup():
            uid = await insert_user(dsn, access_level="admin")
            return await insert_token(dsn, name=f"cat-a-{uuid.uuid4().hex[:8]}", user_id=uid, allowed_modules=["flows"])

        token = asyncio.run(setup())
        cat = _make_category(client, auth_headers)
        resp = client.delete(f"/categories/{cat}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text


class TestDisabledTokens:
    def test_a_disabled_token_is_rejected_until_switched_back_on(self, client):
        dsn = get_settings().database_url

        async def make():
            uid = await insert_user(dsn, access_level="user")
            return await insert_token(dsn, name=f"dis-{uuid.uuid4().hex[:8]}", user_id=uid, allowed_modules=["flows"])

        async def set_disabled(token, disabled):
            from cerebro_flows.auth import hash_token

            conn = await asyncpg.connect(dsn=dsn, timeout=8)
            try:
                await conn.execute(
                    f"UPDATE cerebro_auth.api_tokens SET disabled_at = {'now()' if disabled else 'NULL'} WHERE token_hash = $1",
                    hash_token(token),
                )
            finally:
                await conn.close()

        token = asyncio.run(make())
        h = {"Authorization": f"Bearer {token}"}
        assert client.get("/categories", headers=h).status_code == 200
        asyncio.run(set_disabled(token, True))
        assert client.get("/categories", headers=h).status_code == 401
        asyncio.run(set_disabled(token, False))
        assert client.get("/categories", headers=h).status_code == 200


class TestLogs:
    def test_logs_lists_run_events_newest_first_and_paginates(self, client, auth_headers):
        cat = _make_category(client, auth_headers)
        flow = _make_flow(client, auth_headers, cat)
        run = client.post(f"/flows/{flow['code']}/start", headers=auth_headers).json()

        r = client.get("/logs", params={"flow": flow["code"]}, headers=auth_headers)
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) >= 2 and all(i["flow_code"] == flow["code"] and i["run_id"] == run["run_id"] for i in items)
        types = [i["event_type"] for i in items]
        assert "step_entered" in types and types.index("step_entered") > 0  # newest first: what came later is listed first
        seqs = [i["seq"] for i in items]
        assert seqs == sorted(seqs, reverse=True)

        first = client.get("/logs", params={"flow": flow["code"], "limit": 1}, headers=auth_headers).json()
        assert len(first["items"]) == 1 and first["next"] is not None
        second = client.get("/logs", params={"flow": flow["code"], "limit": 1, "before": first["next"]}, headers=auth_headers).json()
        assert second["items"] and second["items"][0]["seq"] < first["items"][0]["seq"]

        only = client.get("/logs", params={"flow": flow["code"], "event_type": "step_entered"}, headers=auth_headers).json()["items"]
        assert only and all(i["event_type"] == "step_entered" for i in only)

    def test_logs_are_admin_only(self, client):
        dsn = get_settings().database_url

        async def setup(level):
            uid = await insert_user(dsn, access_level=level)
            return await insert_token(dsn, name=f"log-{uuid.uuid4().hex[:8]}", user_id=uid, allowed_modules=["flows"])

        for level, expected in (("user", 403), ("owner", 403), ("admin", 200)):
            token = asyncio.run(setup(level))
            assert client.get("/logs", headers={"Authorization": f"Bearer {token}"}).status_code == expected, level
