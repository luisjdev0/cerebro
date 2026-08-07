"""FastAPI app: contexts, memories (CRUD + hybrid search), audit log, health.

Auth: every endpoint except /health requires `Authorization: Bearer <API_TOKEN>`.
The optional `X-Agent-Name` header identifies the calling agent for the audit log and
memory.source (default "unknown").
"""

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from knowledgeos.config import Settings, get_settings
from knowledgeos.db import apply_migrations, check_health, create_pool
from knowledgeos.embeddings import EmbeddingProvider, build_embedding_provider
from knowledgeos.retrieval import UnknownContextError, hybrid_search
from knowledgeos.security import credential_rejection_message, find_credential_leak

logger = logging.getLogger("knowledgeos.api")

MemoryType = Literal["semantic", "episodic", "procedural", "decision"]

TITLE_TRUNCATE_LEN = 80


# --------------------------------------------------------------------------- models


class ContextCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    description: str | None = None


class ContextOut(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    description: str | None
    created_at: datetime


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    context: str = Field(min_length=1)
    type: MemoryType
    title: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    source: str | None = None
    occurred_at: datetime | None = None


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1)


class MemoryOut(BaseModel):
    id: UUID
    context: str
    type: str
    title: str
    content: str
    importance: float
    confidence: float
    source: str | None
    status: str
    superseded_by: UUID | None
    occurred_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemorySearchResult(MemoryOut):
    score: float


# --------------------------------------------------------------------------- app wiring


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await create_pool(settings)
        applied = await apply_migrations(pool, settings)
        if applied:
            logger.info("applied migrations: %s", applied)
        provider = build_embedding_provider(settings.embedding_model, settings.embedding_dimension)

        app.state.pool = pool
        app.state.settings = settings
        app.state.embedding_provider = provider
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="KnowledgeOS", version="0.1.0", lifespan=lifespan)

    # ---------------------------------------------------------------- dependencies

    def get_pool(request: Request) -> asyncpg.Pool:
        return request.app.state.pool

    def get_embedding_provider(request: Request) -> EmbeddingProvider:
        return request.app.state.embedding_provider

    def require_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = f"Bearer {settings.api_token}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid Authorization: Bearer <API_TOKEN>",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def agent_name(x_agent_name: Annotated[str | None, Header()] = None) -> str:
        return x_agent_name or "unknown"

    async def log_audit(
        pool: asyncpg.Pool,
        *,
        agent: str,
        action: str,
        memory_id: UUID | None,
        detail: dict[str, Any],
    ) -> None:
        import json

        await pool.execute(
            "INSERT INTO audit_log (agent, action, memory_id, detail) VALUES ($1, $2, $3, $4::jsonb)",
            agent,
            action,
            memory_id,
            json.dumps(detail, default=str),
        )

    def row_to_memory_out(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data.pop("context_id", None)
        return data

    # ---------------------------------------------------------------- health

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        ok = await check_health(request.app.state.pool)
        if not ok:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unreachable")
        return {"status": "ok"}

    # ---------------------------------------------------------------- contexts

    @app.post("/contexts", status_code=status.HTTP_201_CREATED, response_model=ContextOut, dependencies=[Depends(require_auth)])
    async def create_context(body: ContextCreate, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        try:
            row = await pool.fetchrow(
                """
                INSERT INTO contexts (slug, name, kind, description)
                VALUES ($1, $2, $3, $4)
                RETURNING id, slug, name, kind, description, created_at
                """,
                body.slug,
                body.name,
                body.kind,
                body.description,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"context slug '{body.slug}' already exists",
            ) from exc
        return dict(row)

    @app.get("/contexts", response_model=list[ContextOut], dependencies=[Depends(require_auth)])
    async def list_contexts(pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        rows = await pool.fetch(
            "SELECT id, slug, name, kind, description, created_at FROM contexts ORDER BY created_at"
        )
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- memories

    @app.post("/memories", status_code=status.HTTP_201_CREATED, response_model=MemoryOut, dependencies=[Depends(require_auth)])
    async def create_memory(
        body: MemoryCreate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
        agent: Annotated[str, Depends(agent_name)],
    ):
        leak = find_credential_leak(body.content)
        if leak is None and body.title:
            leak = find_credential_leak(body.title)
        if leak is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=credential_rejection_message(leak),
            )

        context_id = await pool.fetchval("SELECT id FROM contexts WHERE slug = $1", body.context)
        if context_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown context '{body.context}': create it first with POST /contexts",
            )

        title = body.title or (
            body.content if len(body.content) <= TITLE_TRUNCATE_LEN else body.content[: TITLE_TRUNCATE_LEN - 1].rstrip() + "…"
        )
        embedding = await provider.embed_passage(body.content)
        importance = body.importance if body.importance is not None else 0.5
        source = body.source or agent

        row = await pool.fetchrow(
            f"""
            WITH ins AS (
                INSERT INTO memories (context_id, type, title, content, importance, source, occurred_at, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)
                RETURNING id, context_id, type, title, content, importance, confidence, source,
                          status, superseded_by, occurred_at, created_at, updated_at
            )
            SELECT ins.*, c.slug AS context FROM ins JOIN contexts c ON c.id = ins.context_id
            """,
            context_id,
            body.type,
            title,
            body.content,
            importance,
            source,
            body.occurred_at,
            _vector_literal(embedding),
        )

        await log_audit(
            pool,
            agent=agent,
            action="remember",
            memory_id=row["id"],
            detail={"context": body.context, "type": body.type, "title": title},
        )
        return row_to_memory_out(row)

    @app.get("/memories/search", response_model=list[MemorySearchResult], dependencies=[Depends(require_auth)])
    async def search_memories(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
        agent: Annotated[str, Depends(agent_name)],
        q: Annotated[str, Query(min_length=1)],
        context: str | None = None,
        type: str | None = None,  # noqa: A002
        limit: Annotated[int, Query(ge=1, le=50)] = 5,
        include_superseded: bool = False,
    ):
        try:
            results = await hybrid_search(
                pool,
                provider,
                query=q,
                context_slug=context,
                type_=type,
                limit=limit,
                include_superseded=include_superseded,
            )
        except UnknownContextError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown context '{exc}'",
            ) from exc

        await log_audit(
            pool,
            agent=agent,
            action="search",
            memory_id=None,
            detail={
                "query": q,
                "context": context,
                "type": type,
                "limit": limit,
                "include_superseded": include_superseded,
                "result_count": len(results),
            },
        )
        return [row_to_memory_out(r) for r in results]

    @app.patch("/memories/{memory_id}", response_model=MemoryOut, dependencies=[Depends(require_auth)])
    async def update_memory(
        memory_id: UUID,
        body: MemoryUpdate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
        agent: Annotated[str, Depends(agent_name)],
    ):
        leak = find_credential_leak(body.content)
        if leak is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=credential_rejection_message(leak),
            )

        async with pool.acquire() as conn:
            async with conn.transaction():
                old = await conn.fetchrow("SELECT * FROM memories WHERE id = $1 FOR UPDATE", memory_id)
                if old is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
                if old["status"] != "active":
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"memory {memory_id} is '{old['status']}', not 'active' - cannot update",
                    )

                embedding = await provider.embed_passage(body.content)

                new_row = await conn.fetchrow(
                    f"""
                    WITH ins AS (
                        INSERT INTO memories (context_id, type, title, content, importance, confidence, source, occurred_at, embedding)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::vector)
                        RETURNING id, context_id, type, title, content, importance, confidence, source,
                                  status, superseded_by, occurred_at, created_at, updated_at
                    )
                    SELECT ins.*, c.slug AS context FROM ins JOIN contexts c ON c.id = ins.context_id
                    """,
                    old["context_id"],
                    old["type"],
                    old["title"],
                    body.content,
                    old["importance"],
                    old["confidence"],
                    old["source"],
                    old["occurred_at"],
                    _vector_literal(embedding),
                )

                await conn.execute(
                    "UPDATE memories SET status = 'superseded', superseded_by = $1, updated_at = now() WHERE id = $2",
                    new_row["id"],
                    memory_id,
                )

        await log_audit(
            pool,
            agent=agent,
            action="update",
            memory_id=new_row["id"],
            detail={"superseded": str(memory_id)},
        )
        return row_to_memory_out(new_row)

    @app.delete("/memories/{memory_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(require_auth)])
    async def delete_memory(
        memory_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        agent: Annotated[str, Depends(agent_name)],
        hard: bool = False,
    ):
        if hard:
            try:
                result = await pool.execute("DELETE FROM memories WHERE id = $1", memory_id)
            except asyncpg.ForeignKeyViolationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="cannot hard-delete: another memory's superseded_by still points to this one",
                ) from exc
            if result == "DELETE 0":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
        else:
            result = await pool.execute(
                "UPDATE memories SET status = 'archived', updated_at = now() WHERE id = $1", memory_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")

        await log_audit(
            pool,
            agent=agent,
            action="forget",
            memory_id=memory_id,
            detail={"hard": hard},
        )
        return {"id": str(memory_id), "hard": hard, "status": "deleted" if hard else "archived"}

    return app


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.10f}" for v in vec) + "]"


app = create_app()
