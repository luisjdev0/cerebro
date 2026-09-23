"""FastAPI app: categorias, definiciones de flujo (CRUD + versionado) y el motor de
ejecucion "semaforo" (luisjdev-pendientes/cerebro-flows).

Auth: cada endpoint salvo /health requiere `Authorization: Bearer <API_TOKEN>` -
mismo mecanismo exacto que cerebro-docs (`auth.py`, token root + tokens con scopes y
`allowed_categories`).
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from cerebro_flows import engine
from cerebro_flows.auth import (
    DuplicateTokenNameError,
    InvalidScopesError,
    Principal,
    TokenNotFoundError,
    create_api_token,
    get_principal,
    list_api_tokens,
    require_scope,
    revoke_api_token,
)
from cerebro_flows.config import Settings, get_settings
from cerebro_flows.db import apply_migrations, check_health, create_pool, create_redis
from cerebro_flows.schema import InvalidFlowSchemaError, validate_flow_yaml

logger = logging.getLogger("cerebro_flows.api")

_CODE_SUFFIX_RE = re.compile(r"-(\d+)$")


# --------------------------------------------------------------------------- models


class StrictIn(BaseModel):
    """Mismo criterio que cerebro-docs: un campo desconocido es 422, nunca se
    ignora en silencio."""

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


class TokenCreate(StrictIn):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1)
    allowed_categories: list[str] | None = None
    value: str | None = Field(default=None, min_length=1)


class TokenOut(BaseModel):
    id: UUID
    name: str
    scopes: list[str]
    allowed_categories: list[str] | None
    created_at: datetime
    revoked_at: datetime | None


class TokenCreateOut(TokenOut):
    token: str


class StatsOut(BaseModel):
    categories: int
    flows: int
    runs: int


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
        return StatsOut(categories=categories, flows=flows, runs=runs)

    # ---------------------------------------------------------------- tokens

    @app.post(
        "/tokens",
        status_code=status.HTTP_201_CREATED,
        response_model=TokenCreateOut,
        dependencies=[Depends(require_scope("admin"))],
    )
    async def create_token(body: TokenCreate, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        try:
            created = await create_api_token(
                pool, name=body.name, scopes=body.scopes, allowed_categories=body.allowed_categories, value=body.value
            )
        except InvalidScopesError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except DuplicateTokenNameError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"an active token named '{exc}' already exists - revoke it first",
            ) from exc
        return TokenCreateOut(**created)

    @app.get("/tokens", response_model=list[TokenOut], dependencies=[Depends(require_scope("admin"))])
    async def list_tokens(pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        return [dict(r) for r in await list_api_tokens(pool)]

    @app.delete("/tokens/{name}", response_model=TokenOut, dependencies=[Depends(require_scope("admin"))])
    async def revoke_token(name: str, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        try:
            row = await revoke_api_token(pool, name)
        except TokenNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no active token named '{exc}'") from exc
        return dict(row)

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
                        detail=f"categoria '{body.category}' inexistente - creala primero con flow_create_category",
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
                        INSERT INTO flow_definitions (category_id, code, name, description, created_by)
                        VALUES ($1, $2, $3, $4, $5)
                        RETURNING id, category_id, code, name, description, status, current_version, created_by, created_at, updated_at
                        """,
                        category["id"],
                        code,
                        parsed.metadata.name,
                        parsed.metadata.description,
                        creator,
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"ya existe un flujo con code '{code}' (GET /flows/{code}) - usa PATCH /flows/{{code}} (flow_update) para editarlo",
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
                   f.created_by, f.created_at, f.updated_at, v.yaml_content
            FROM flow_definitions f
            JOIN flow_categories c ON c.id = f.category_id
            JOIN flow_definition_versions v ON v.definition_id = f.id AND v.version_number = f.current_version
            WHERE f.code = $1
            """,
            code,
        )

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
        return dict(row)

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
                    SELECT f.id, c.slug AS category, f.current_version
                    FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
                    WHERE f.code = $1
                    FOR UPDATE OF f
                    """,
                    code,
                )
                if old is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="flow not found")
                require_category_allowed(principal, old["category"])

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
                    SELECT f.id, c.slug AS category
                    FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id
                    WHERE f.code = $1
                    FOR UPDATE OF f
                    """,
                    code,
                )
                if row is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="flow not found")
                require_category_allowed(principal, row["category"])
                await conn.execute("DELETE FROM flow_definitions WHERE id = $1", row["id"])
        return {"code": code, "status": "deleted"}

    # ---------------------------------------------------------------- execution engine

    async def _flow_category(pool: asyncpg.Pool, code: str) -> str | None:
        row = await pool.fetchrow(
            "SELECT c.slug FROM flow_definitions f JOIN flow_categories c ON c.id = f.category_id WHERE f.code = $1",
            code,
        )
        return row["slug"] if row else None

    async def _run_category(pool: asyncpg.Pool, run_id: UUID) -> str | None:
        row = await pool.fetchrow(
            """
            SELECT c.slug FROM flow_runs r
            JOIN flow_definitions f ON f.id = r.definition_id
            JOIN flow_categories c ON c.id = f.category_id
            WHERE r.run_id = $1
            """,
            run_id,
        )
        return row["slug"] if row else None

    @app.post("/flows/{code}/start", response_model=RunStateOut)
    async def start_flow(
        code: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        redis_client: Annotated[Any, Depends(get_redis)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        category = await _flow_category(pool, code)
        if category is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"flow '{code}' not found")
        require_category_allowed(principal, category)
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
        category = await _run_category(pool, run_id)
        if category is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"run_id '{run_id}' not found")
        require_category_allowed(principal, category)
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
        category = await _run_category(pool, run_id)
        if category is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"run_id '{run_id}' not found")
        require_category_allowed(principal, category)
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
        category = await _run_category(pool, run_id)
        if category is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"run_id '{run_id}' not found")
        require_category_allowed(principal, category)
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
        category = await _run_category(pool, run_id)
        if category is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"run_id '{run_id}' not found")
        require_category_allowed(principal, category)
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
        category = await _run_category(pool, run_id)
        if category is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"run_id '{run_id}' not found")
        require_category_allowed(principal, category)
        try:
            return await engine.get_run_state(pool, redis_client, str(run_id))
        except engine.RunNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return app


app = create_app()
