"""FastAPI app: categories, flow definitions (CRUD + versioning) and the "traffic
light" execution engine (luisjdev-pendientes/cerebro-flows).

Auth: every endpoint except /health requires `Authorization: Bearer <API_TOKEN>`.
Tokens are resolved against the shared `cerebro_auth` schema (owned by the
`cerebro-auth` service) - see `auth.py` for the full resolution algorithm. Token
management itself (create/list/revoke) no longer lives in this service.
"""

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from cerebro_flows import engine
from cerebro_flows.diagram import flow_references, flow_to_mermaid
from cerebro_flows.auth import (
    Principal,
    get_principal,
    require_scope,
)
from cerebro_flows.config import Settings, get_settings
from cerebro_flows.db import apply_migrations, check_health, create_pool, create_redis
from cerebro_flows.schema import InvalidFlowSchemaError, validate_flow_yaml

logger = logging.getLogger("cerebro_flows.api")

_CODE_SUFFIX_RE = re.compile(r"-(\d+)$")


# --------------------------------------------------------------------------- models


class StrictIn(BaseModel):
    """Same criterion as cerebro-docs: an unknown field is a 422, it is never
    silently ignored."""

    model_config = ConfigDict(extra="forbid")


class CategoryCreate(StrictIn):
    slug: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1)
    description: str | None = None


class CategoryOut(BaseModel):
    id: UUID
    slug: str
    code: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class FlowValidateIn(StrictIn):
    yaml_content: str = Field(min_length=1)


class FlowCreate(StrictIn):
    category: str = Field(min_length=1)
    yaml_content: str = Field(min_length=1)
    code: str | None = None


class FlowUpdate(StrictIn):
    yaml_content: str = Field(min_length=1)


class FlowOut(BaseModel):
    id: UUID
    code: str
    category: str
    name: str
    description: str | None
    status: str
    current_version: int
    yaml_content: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class ActivityDay(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    created: int  # flow definitions created
    updated: int  # flow runs started


class ActivityResponse(BaseModel):
    days: list[ActivityDay]
    years: list[int] = []  # calendar years (UTC) with visible activity, newest first


class CategoryCount(BaseModel):
    slug: str
    name: str
    code: str  # the category's flow-code prefix, e.g. "OPS" in OPS-1
    count: int


class StatsOut(BaseModel):
    categories: int
    flows: int
    runs: int
    flows_by_category: list[CategoryCount] = []


class FlowSummary(BaseModel):
    id: UUID
    code: str
    category: str
    name: str
    description: str | None
    status: str
    current_version: int
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class FlowBrowseResponse(BaseModel):
    items: list[FlowSummary]
    total: int


class FlowVersionOut(BaseModel):
    version_number: int
    yaml_content: str
    created_at: datetime


class FlowDiagramOut(BaseModel):
    code: str
    version: int
    format: str
    diagram: str


class FlowReferenceOut(BaseModel):
    ref: str
    kind: Literal["metadata", "prompt"]
    step_id: str | None = None


class RunSummary(BaseModel):
    run_id: UUID
    flow_code: str
    flow_name: str
    category: str
    definition_version: int
    status: str  # in_progress | completed | aborted
    started_at: datetime
    completed_at: datetime | None
    current_step_id: str | None = None  # only while in progress and not expired
    awaiting_approval: bool = False  # current step has a required checkpoint nobody approved
    expired: bool = False  # still in_progress in the DB, but its Redis pointer expired


class RunBrowseResponse(BaseModel):
    items: list[RunSummary]
    total: int


class RunEventOut(BaseModel):
    event_type: str
    step_id: str | None
    payload: Any = None
    created_at: datetime


class DecisionIn(StrictIn):
    decision: str | None = None


class ReasonIn(StrictIn):
    reason: str = Field(min_length=1)


class OptionalReasonIn(StrictIn):
    reason: str | None = None


class RunStateOut(BaseModel):
    run_id: str
    status: str
    step: Any = None


# --------------------------------------------------------------------------- app wiring


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await create_pool(settings)
        redis_client = create_redis(settings)
        applied = await apply_migrations(pool, settings)
        if applied:
            logger.info("applied migrations: %s", applied)

        app.state.pool = pool
        app.state.redis = redis_client
        app.state.settings = settings
        try:
            yield
        finally:
            await redis_client.aclose()
            await pool.close()

    app = FastAPI(title="cerebro-flows", version="0.1.0", lifespan=lifespan)

    # ---------------------------------------------------------------- dependencies

    def get_pool(request: Request) -> asyncpg.Pool:
        return request.app.state.pool

    def get_redis(request: Request):
        return request.app.state.redis

    def created_by_name(
        principal: Annotated[Principal, Depends(get_principal)],
        x_agent_name: Annotated[str | None, Header()] = None,
    ) -> str:
        if not principal.is_root:
            return principal.name
        return x_agent_name or "unknown"

    def require_category_allowed(principal: Principal, slug: str) -> None:
        if not principal.category_allowed(slug):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token '{principal.name}' is not allowed to access category '{slug}'",
            )

    def require_owner_allowed(principal: Principal, owner_user_id: UUID | None) -> None:
        """The ownership gate (`principal.owner_filter`), separate from and
        additional to `require_category_allowed`. A definition outside the
        principal's ownership scope is treated as not found (same as excluding it
        from GET /flows), so its existence isn't leaked to callers who can't list it."""
        owner_filter = principal.owner_filter
        if owner_filter is None:
            return
        if "user_id" in owner_filter:
            allowed = owner_user_id == owner_filter["user_id"]
        else:
            allowed = owner_user_id in owner_filter["user_id_in"]
        if not allowed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="flow not found")

    def row_to_flow_out(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data.pop("category_id", None)
        return data

    # ---------------------------------------------------------------- health / stats

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        ok = await check_health(request.app.state.pool, request.app.state.redis)
        if not ok:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database or redis unreachable")
        return {"status": "ok"}

    @app.get("/stats", response_model=StatsOut)
    async def get_stats(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        if principal.allowed_categories is None:
            categories = await pool.fetchval("SELECT count(*) FROM flow_categories")
            flows = await pool.fetchval("SELECT count(*) FROM flow_definitions")
            runs = await pool.fetchval("SELECT count(*) FROM flow_runs")
        else:
            allowed = list(principal.allowed_categories)
            categories = await pool.fetchval(
                "SELECT count(*) FROM flow_categories WHERE slug = ANY($1::text[])", allowed
            )
            flows = await pool.fetchval(
                """
                SELECT count(*) FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
                WHERE c.slug = ANY($1::text[])
                """,
                allowed,
            )
            runs = await pool.fetchval(
                """
                SELECT count(*) FROM flow_runs r
                JOIN flow_definitions f ON f.id = r.definition_id
                JOIN flow_categories c ON c.id = f.category_id
                WHERE c.slug = ANY($1::text[])
                """,
                allowed,
            )
        # Per-category breakdown (portal pie chart): same category scope and ownership
        # gate as /activity.
        params: list[Any] = []
        filters: list[str] = []
        if principal.allowed_categories is not None:
            params.append(list(principal.allowed_categories))
            filters.append(f"c.slug = ANY(${len(params)}::text[])")
        if principal.owner_filter is not None:
            if "user_id" in principal.owner_filter:
                params.append(principal.owner_filter["user_id"])
                filters.append(f"f.owner_user_id = ${len(params)}")
            else:
                params.append(principal.owner_filter["user_id_in"])
                filters.append(f"f.owner_user_id = ANY(${len(params)}::uuid[])")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = await pool.fetch(
            f"""
            SELECT c.slug, c.name, c.code, count(*)::int AS count
            FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
            {where} GROUP BY c.slug, c.name, c.code ORDER BY count(*) DESC, c.name
            """,
            *params,
        )
        return StatsOut(
            categories=categories,
            flows=flows,
            runs=runs,
            flows_by_category=[CategoryCount(**dict(r)) for r in rows],
        )

    # ---------------------------------------------------------------- activity

    @app.get("/activity", response_model=ActivityResponse)
    async def activity(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
        days: Annotated[int, Query(ge=1, le=730)] = 365,
        year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    ):
        """Per-day counts of flow definitions created (`created`) and runs started
        (`updated`), for the last `days` days or for the calendar `year` (days without
        activity are omitted), plus the years that have any visible activity. Honors
        the token's category scope and the ownership gate on definitions."""
        # `year` (calendar year, UTC) overrides the rolling `days` window.
        if year is not None:
            start, end = datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime.now(timezone.utc) + timedelta(days=1)
            start = end - timedelta(days=days + 1)

        def scope(params: list[Any]) -> str:
            filters: list[str] = []
            if principal.allowed_categories is not None:
                params.append(list(principal.allowed_categories))
                filters.append(f"c.slug = ANY(${len(params)}::text[])")
            if principal.owner_filter is not None:
                if "user_id" in principal.owner_filter:
                    params.append(principal.owner_filter["user_id"])
                    filters.append(f"f.owner_user_id = ${len(params)}")
                else:
                    params.append(principal.owner_filter["user_id_in"])
                    filters.append(f"f.owner_user_id = ANY(${len(params)}::uuid[])")
            return "".join(f" AND {f}" for f in filters)

        params: list[Any] = [start, end]
        extra = scope(params)
        rows = await pool.fetch(
            f"""
            WITH ev AS (
                SELECT (f.created_at AT TIME ZONE 'UTC')::date AS day, 1 AS created, 0 AS updated
                FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
                WHERE f.created_at >= $1 AND f.created_at < $2{extra}
                UNION ALL
                SELECT (r.started_at AT TIME ZONE 'UTC')::date, 0, 1
                FROM flow_runs r
                JOIN flow_definitions f ON f.id = r.definition_id
                JOIN flow_categories c ON c.id = f.category_id
                WHERE r.started_at >= $1 AND r.started_at < $2{extra}
            )
            SELECT day, sum(created)::int AS created, sum(updated)::int AS updated
            FROM ev GROUP BY day ORDER BY day
            """,
            *params,
        )
        year_params: list[Any] = []
        year_extra = scope(year_params)
        year_rows = await pool.fetch(
            f"""
            SELECT DISTINCT y FROM (
                SELECT extract(year FROM f.created_at AT TIME ZONE 'UTC')::int AS y
                FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id WHERE true{year_extra}
                UNION
                SELECT extract(year FROM r.started_at AT TIME ZONE 'UTC')::int
                FROM flow_runs r
                JOIN flow_definitions f ON f.id = r.definition_id
                JOIN flow_categories c ON c.id = f.category_id WHERE true{year_extra}
            ) t ORDER BY y DESC
            """,
            *year_params,
        )
        return ActivityResponse(
            days=[ActivityDay(date=r["day"].isoformat(), created=r["created"], updated=r["updated"]) for r in rows],
            years=[r["y"] for r in year_rows],
        )

    # ---------------------------------------------------------------- categories

    @app.post("/categories", status_code=status.HTTP_201_CREATED, response_model=CategoryOut)
    async def create_category(
        body: CategoryCreate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        require_category_allowed(principal, body.slug)
        try:
            row = await pool.fetchrow(
                """
                INSERT INTO flow_categories (slug, code, name, description)
                VALUES ($1, $2, $3, $4)
                RETURNING id, slug, code, name, description, created_at, updated_at
                """,
                body.slug,
                body.code.upper(),
                body.name,
                body.description,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"category slug '{body.slug}' or code '{body.code}' already exists",
            ) from exc
        return dict(row)

    @app.get("/categories", response_model=list[CategoryOut])
    async def list_categories(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        rows = await pool.fetch(
            "SELECT id, slug, code, name, description, created_at, updated_at FROM flow_categories ORDER BY created_at"
        )
        return [dict(r) for r in rows if principal.category_allowed(r["slug"])]

    # ---------------------------------------------------------------- flow definitions: validate/create/read/list

    @app.post("/flows/validate")
    async def validate_flow(body: FlowValidateIn, principal: Annotated[Principal, Depends(require_scope("write"))]):
        try:
            validate_flow_yaml(body.yaml_content)
        except InvalidFlowSchemaError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return {"valid": True}

    @app.post("/flows", status_code=status.HTTP_201_CREATED, response_model=FlowOut)
    async def create_flow(
        body: FlowCreate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        creator: Annotated[str, Depends(created_by_name)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        require_category_allowed(principal, body.category)
        try:
            parsed = validate_flow_yaml(body.yaml_content)
        except InvalidFlowSchemaError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        async with pool.acquire() as conn:
            async with conn.transaction():
                category = await conn.fetchrow(
                    "SELECT id, slug, code FROM flow_categories WHERE slug = $1 FOR UPDATE", body.category
                )
                if category is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"category '{body.category}' does not exist - create it first with flow_create_category",
                    )

                if body.code:
                    code = body.code
                else:
                    existing_codes = await conn.fetch(
                        "SELECT code FROM flow_definitions WHERE category_id = $1", category["id"]
                    )
                    next_seq = 1
                    for row in existing_codes:
                        m = _CODE_SUFFIX_RE.search(row["code"])
                        if m:
                            next_seq = max(next_seq, int(m.group(1)) + 1)
                    code = f"{category['code']}-{next_seq}"

                try:
                    def_row = await conn.fetchrow(
                        """
                        INSERT INTO flow_definitions (category_id, code, name, description, created_by, owner_user_id)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        RETURNING id, category_id, code, name, description, status, current_version, created_by, created_at, updated_at
                        """,
                        category["id"],
                        code,
                        parsed.metadata.name,
                        parsed.metadata.description,
                        creator,
                        principal.user_id,
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"a flow with code '{code}' already exists (GET /flows/{code}) - use PATCH /flows/{{code}} (flow_update) to edit it",
                    ) from exc

                await conn.execute(
                    "INSERT INTO flow_definition_versions (definition_id, version_number, yaml_content) VALUES ($1, 1, $2)",
                    def_row["id"],
                    body.yaml_content,
                )

        return row_to_flow_out({**dict(def_row), "category": category["slug"], "yaml_content": body.yaml_content})

    async def _fetch_flow(pool: asyncpg.Pool, code: str) -> asyncpg.Record | None:
        return await pool.fetchrow(
            """
            SELECT f.id, f.code, c.slug AS category, f.name, f.description, f.status, f.current_version,
                   f.created_by, f.owner_user_id, f.created_at, f.updated_at, v.yaml_content
            FROM flow_definitions f
            JOIN flow_categories c ON c.id = f.category_id
            JOIN flow_definition_versions v ON v.definition_id = f.id AND v.version_number = f.current_version
            WHERE f.code = $1
            """,
            code,
        )

    # ---------------------------------------------------------------- portal listings
    # Consumed by cerebro-portal only (paginated/sortable/searchable, with a total), so
    # there is deliberately no client/MCP/CLI mirror -- same stance as cerebro-docs'
    # `GET /documents/browse`. Registered before `/flows/{code}` so `browse` is not
    # taken for a flow code.

    def _scope_filters(principal: Principal, params: list[Any]) -> list[str]:
        """Category scope + ownership gate on the flow definition (`f`) joined to its category (`c`)."""
        filters: list[str] = []
        if principal.allowed_categories is not None:
            params.append(list(principal.allowed_categories))
            filters.append(f"c.slug = ANY(${len(params)}::text[])")
        if principal.owner_filter is not None:
            if "user_id" in principal.owner_filter:
                params.append(principal.owner_filter["user_id"])
                filters.append(f"f.owner_user_id = ${len(params)}")
            else:
                params.append(principal.owner_filter["user_id_in"])
                filters.append(f"f.owner_user_id = ANY(${len(params)}::uuid[])")
        return filters

    @app.get("/flows/browse", response_model=FlowBrowseResponse)
    async def browse_flows(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
        category: str | None = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
        sort: Literal["updated_at", "created_at", "code", "name", "category"] = "updated_at",
        order: Literal["asc", "desc"] = "desc",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if category is not None:
            require_category_allowed(principal, category)
        params: list[Any] = []
        filters = _scope_filters(principal, params)
        if category is not None:
            params.append(category)
            filters.append(f"c.slug = ${len(params)}")
        if q and q.strip():
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
            n = len(params)
            filters.append(f"(f.name ILIKE ${n} OR f.code ILIKE ${n} OR coalesce(f.description, '') ILIKE ${n})")
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        column = {
            "updated_at": "f.updated_at",
            "created_at": "f.created_at",
            "code": "f.code",
            "name": "lower(f.name)",
            "category": "c.slug",
        }[sort]
        direction = "ASC" if order == "asc" else "DESC"
        total = await pool.fetchval(
            f"SELECT count(*) FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id{where}", *params
        )
        rows = await pool.fetch(
            f"""
            SELECT f.id, f.code, c.slug AS category, f.name, f.description, f.status, f.current_version,
                   f.created_by, f.created_at, f.updated_at
            FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
            {where}
            ORDER BY {column} {direction}, f.id
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            limit,
            offset,
        )
        return {"items": [dict(r) for r in rows], "total": total}

    @app.get("/runs", response_model=RunBrowseResponse)
    async def browse_runs(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        redis_client: Annotated[Any, Depends(get_redis)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
        run_status: Annotated[Literal["in_progress", "completed", "aborted", "all"], Query(alias="status")] = "all",
        awaiting: bool = False,
        flow: str | None = None,
        category: str | None = None,
        sort: Literal["started_at", "completed_at", "status", "flow_code"] = "started_at",
        order: Literal["asc", "desc"] = "desc",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        """Runs of the flows the caller can see. `awaiting=true` keeps only in-progress runs
        stopped at a checkpoint nobody approved (the approval queue); that position lives in
        Redis, so that mode is filtered in memory (capped to the 1000 most recent in-progress runs)."""
        if category is not None:
            require_category_allowed(principal, category)
        params: list[Any] = []
        filters = _scope_filters(principal, params)
        if category is not None:
            params.append(category)
            filters.append(f"c.slug = ${len(params)}")
        if flow is not None:
            params.append(flow)
            filters.append(f"f.code = ${len(params)}")
        if awaiting:
            run_status = "in_progress"
        if run_status != "all":
            params.append(run_status)
            filters.append(f"r.status = ${len(params)}")
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        base = """
            FROM flow_runs r
            JOIN flow_definitions f ON f.id = r.definition_id
            JOIN flow_categories c ON c.id = f.category_id
        """
        columns = """
            r.run_id, r.definition_id, r.definition_version, r.status, r.started_at, r.completed_at,
            f.code AS flow_code, f.name AS flow_name, c.slug AS category
        """
        sort_col = {
            "started_at": "r.started_at",
            "completed_at": "r.completed_at",
            "status": "r.status",
            "flow_code": "f.code",
        }[sort]
        direction = "ASC" if order == "asc" else "DESC"

        def shape(row: asyncpg.Record, live: dict[str, dict[str, Any]]) -> dict[str, Any]:
            item = {k: row[k] for k in ("run_id", "definition_version", "status", "started_at", "completed_at", "flow_code", "flow_name", "category")}
            item.update(live.get(str(row["run_id"]), {}))
            return item

        if awaiting:
            rows = await pool.fetch(
                f"SELECT {columns} {base}{where} ORDER BY r.started_at DESC LIMIT 1000", *params
            )
            live = await engine.describe_in_progress(pool, redis_client, [dict(r) for r in rows])
            kept = [r for r in rows if live[str(r["run_id"])]["awaiting_approval"]]
            return {"items": [shape(r, live) for r in kept[offset : offset + limit]], "total": len(kept)}

        total = await pool.fetchval(f"SELECT count(*) {base}{where}", *params)
        rows = await pool.fetch(
            f"""
            SELECT {columns} {base}{where}
            ORDER BY {sort_col} {direction} NULLS LAST, r.run_id
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            limit,
            offset,
        )
        live = await engine.describe_in_progress(pool, redis_client, [dict(r) for r in rows if r["status"] == "in_progress"])
        return {"items": [shape(r, live) for r in rows], "total": total}

    @app.get("/runs/{run_id}/events", response_model=list[RunEventOut])
    async def run_events(
        run_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        """The run's audit trail, oldest first (`flow_run_events`)."""
        await _require_run_access(pool, principal, run_id)
        rows = await pool.fetch(
            "SELECT event_type, step_id, payload, created_at FROM flow_run_events WHERE run_id = $1 ORDER BY created_at, id",
            run_id,
        )
        out = []
        for r in rows:
            payload = r["payload"]
            if isinstance(payload, str):  # asyncpg does not decode jsonb by default
                payload = json.loads(payload)
            out.append({"event_type": r["event_type"], "step_id": r["step_id"], "payload": payload, "created_at": r["created_at"]})
        return out

    @app.get("/flows/{code}", response_model=FlowOut)
    async def get_flow(
        code: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        row = await _fetch_flow(pool, code)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="flow not found")
        require_category_allowed(principal, row["category"])
        require_owner_allowed(principal, row["owner_user_id"])
        return dict(row)

    async def _flow_yaml(
        pool: asyncpg.Pool, principal: Principal, code: str, version: int | None
    ) -> tuple[int, str]:
        """(version, yaml) of a flow the caller may read; the current version when `version` is None."""
        row = await _flow_category_and_owner(pool, code)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="flow not found")
        require_category_allowed(principal, row["slug"])
        require_owner_allowed(principal, row["owner_user_id"])
        found = await pool.fetchrow(
            """
            SELECT v.version_number, v.yaml_content
            FROM flow_definition_versions v JOIN flow_definitions f ON f.id = v.definition_id
            WHERE f.code = $1 AND v.version_number = coalesce($2::int, f.current_version)
            """,
            code,
            version,
        )
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"version {version} not found")
        return found["version_number"], found["yaml_content"]

    @app.get("/flows/{code}/diagram", response_model=FlowDiagramOut)
    async def flow_diagram(
        code: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
        format: Literal["mermaid"] = "mermaid",
        version: Annotated[int | None, Query(ge=1)] = None,
    ):
        """Mermaid flowchart of the COMPLETE definition (for people; execution still reveals one step at a time)."""
        number, text = await _flow_yaml(pool, principal, code, version)
        return {"code": code, "version": number, "format": format, "diagram": flow_to_mermaid(validate_flow_yaml(text))}

    @app.get("/flows/{code}/references", response_model=list[FlowReferenceOut])
    async def flow_refs(
        code: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        """cerebro-docs URIs the current definition points at (metadata references and delegate prompts)."""
        _, text = await _flow_yaml(pool, principal, code, None)
        return flow_references(validate_flow_yaml(text))

    @app.get("/flows/{code}/versions", response_model=list[FlowVersionOut])
    async def flow_versions(
        code: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        """Every saved version of the definition, newest first."""
        row = await _flow_category_and_owner(pool, code)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="flow not found")
        require_category_allowed(principal, row["slug"])
        require_owner_allowed(principal, row["owner_user_id"])
        rows = await pool.fetch(
            """
            SELECT v.version_number, v.yaml_content, v.created_at
            FROM flow_definition_versions v JOIN flow_definitions f ON f.id = v.definition_id
            WHERE f.code = $1 ORDER BY v.version_number DESC
            """,
            code,
        )
        return [dict(r) for r in rows]

    @app.get("/flows", response_model=list[FlowOut])
    async def list_flows(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
        category: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if category is not None:
            require_category_allowed(principal, category)

        filters: list[str] = []
        params: list[Any] = []
        if category is not None:
            params.append(category)
            filters.append(f"c.slug = ${len(params)}")
        elif principal.allowed_categories is not None:
            params.append(list(principal.allowed_categories))
            filters.append(f"c.slug = ANY(${len(params)}::text[])")

        if principal.owner_filter is not None:
            if "user_id" in principal.owner_filter:
                params.append(principal.owner_filter["user_id"])
                filters.append(f"f.owner_user_id = ${len(params)}")
            else:
                params.append(principal.owner_filter["user_id_in"])
                filters.append(f"f.owner_user_id = ANY(${len(params)}::uuid[])")

        where_clause = (" WHERE " + " AND ".join(filters)) if filters else ""

        params.append(limit)
        limit_param = len(params)
        params.append(offset)
        offset_param = len(params)

        rows = await pool.fetch(
            f"""
            SELECT f.id, f.code, c.slug AS category, f.name, f.description, f.status, f.current_version,
                   f.created_by, f.created_at, f.updated_at, v.yaml_content
            FROM flow_definitions f
            JOIN flow_categories c ON c.id = f.category_id
            JOIN flow_definition_versions v ON v.definition_id = f.id AND v.version_number = f.current_version
            {where_clause}
            ORDER BY f.updated_at DESC
            LIMIT ${limit_param} OFFSET ${offset_param}
            """,
            *params,
        )
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- flow definitions: write (versioned)

    @app.patch("/flows/{code}", response_model=FlowOut)
    async def update_flow(
        code: str,
        body: FlowUpdate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        try:
            parsed = validate_flow_yaml(body.yaml_content)
        except InvalidFlowSchemaError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        async with pool.acquire() as conn:
            async with conn.transaction():
                old = await conn.fetchrow(
                    """
                    SELECT f.id, c.slug AS category, f.current_version, f.owner_user_id
                    FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
                    WHERE f.code = $1
                    FOR UPDATE OF f
                    """,
                    code,
                )
                if old is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="flow not found")
                require_category_allowed(principal, old["category"])
                require_owner_allowed(principal, old["owner_user_id"])

                next_version = old["current_version"] + 1
                await conn.execute(
                    "INSERT INTO flow_definition_versions (definition_id, version_number, yaml_content) VALUES ($1, $2, $3)",
                    old["id"],
                    next_version,
                    body.yaml_content,
                )
                await conn.execute(
                    """
                    UPDATE flow_definitions
                    SET current_version = $1, name = $2, description = $3, updated_at = now()
                    WHERE id = $4
                    """,
                    next_version,
                    parsed.metadata.name,
                    parsed.metadata.description,
                    old["id"],
                )

        row = await _fetch_flow(pool, code)
        return dict(row)

    @app.delete("/flows/{code}")
    async def delete_flow(
        code: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT f.id, c.slug AS category, f.owner_user_id
                    FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
                    WHERE f.code = $1
                    FOR UPDATE OF f
                    """,
                    code,
                )
                if row is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="flow not found")
                require_category_allowed(principal, row["category"])
                require_owner_allowed(principal, row["owner_user_id"])
                await conn.execute("DELETE FROM flow_definitions WHERE id = $1", row["id"])
        return {"code": code, "status": "deleted"}

    # ---------------------------------------------------------------- execution engine

    async def _flow_category_and_owner(pool: asyncpg.Pool, code: str) -> asyncpg.Record | None:
        return await pool.fetchrow(
            """
            SELECT c.slug, f.owner_user_id
            FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
            WHERE f.code = $1
            """,
            code,
        )

    async def _require_run_access(pool: asyncpg.Pool, principal: Principal, run_id: UUID) -> None:
        """Category gate + ownership gate of the run's flow definition (same rules as
        the definition itself). A run outside the principal's ownership scope is
        reported as not found, so its existence is not leaked."""
        row = await pool.fetchrow(
            """
            SELECT c.slug, f.owner_user_id FROM flow_runs r
            JOIN flow_definitions f ON f.id = r.definition_id
            JOIN flow_categories c ON c.id = f.category_id
            WHERE r.run_id = $1
            """,
            run_id,
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"run_id '{run_id}' not found")
        require_category_allowed(principal, row["slug"])
        try:
            require_owner_allowed(principal, row["owner_user_id"])
        except HTTPException:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"run_id '{run_id}' not found") from None

    @app.post("/flows/{code}/start", response_model=RunStateOut)
    async def start_flow(
        code: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        redis_client: Annotated[Any, Depends(get_redis)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        info = await _flow_category_and_owner(pool, code)
        if info is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"flow '{code}' not found")
        require_category_allowed(principal, info["slug"])
        require_owner_allowed(principal, info["owner_user_id"])
        try:
            return await engine.start_run(pool, redis_client, settings, code)
        except engine.FlowNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/runs/{run_id}/next", response_model=RunStateOut)
    async def next_step(
        run_id: UUID,
        body: DecisionIn,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        redis_client: Annotated[Any, Depends(get_redis)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        await _require_run_access(pool, principal, run_id)
        try:
            return await engine.advance(pool, redis_client, settings, str(run_id), decision=body.decision)
        except engine.RunNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except engine.RunAlreadyFinishedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except engine.CheckpointPendingError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except engine.InvalidDecisionError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.post("/runs/{run_id}/approve-checkpoint", response_model=RunStateOut)
    async def approve_checkpoint(
        run_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        redis_client: Annotated[Any, Depends(get_redis)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        await _require_run_access(pool, principal, run_id)
        try:
            return await engine.approve_checkpoint(pool, redis_client, settings, str(run_id))
        except engine.RunNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except engine.RunAlreadyFinishedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except engine.NotAtCheckpointError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @app.post("/runs/{run_id}/reject-checkpoint", response_model=RunStateOut)
    async def reject_checkpoint(
        run_id: UUID,
        body: ReasonIn,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        redis_client: Annotated[Any, Depends(get_redis)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        await _require_run_access(pool, principal, run_id)
        try:
            return await engine.reject_checkpoint(pool, redis_client, settings, str(run_id), body.reason)
        except engine.RunNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except engine.RunAlreadyFinishedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except engine.NotAtCheckpointError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @app.post("/runs/{run_id}/abort", response_model=RunStateOut)
    async def abort_run(
        run_id: UUID,
        body: OptionalReasonIn,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        redis_client: Annotated[Any, Depends(get_redis)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        await _require_run_access(pool, principal, run_id)
        try:
            return await engine.abort_run(pool, redis_client, str(run_id), body.reason)
        except engine.RunNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except engine.RunAlreadyFinishedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @app.get("/runs/{run_id}", response_model=RunStateOut)
    async def get_run(
        run_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        redis_client: Annotated[Any, Depends(get_redis)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        await _require_run_access(pool, principal, run_id)
        try:
            return await engine.get_run_state(pool, redis_client, str(run_id))
        except engine.RunNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return app


app = create_app()
