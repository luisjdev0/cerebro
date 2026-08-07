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
from knowledgeos.context_engine import (
    DisambiguationAlreadyResolvedError,
    DisambiguationNotFoundError,
    ScopeDecision,
    decide_scope,
    resolve_disambiguation,
)
from knowledgeos.db import apply_migrations, check_health, create_pool
from knowledgeos.embeddings import EmbeddingProvider, build_embedding_provider
from knowledgeos.graph import (
    RELATION_VOCAB,
    DuplicateEdgeError,
    EdgeNotFoundError,
    InvalidRelationError,
    MemoryNotFoundError,
    add_edge,
    delete_edge,
    get_related,
    get_search_related,
    get_timeline,
)
from knowledgeos.graph import UnknownContextError as GraphUnknownContextError
from knowledgeos.retrieval import UnknownContextError, hybrid_search
from knowledgeos.security import credential_rejection_message, find_credential_leak

logger = logging.getLogger("knowledgeos.api")

MemoryType = Literal["semantic", "episodic", "procedural", "decision"]
EdgeRelation = Literal["relates_to", "caused_by", "part_of", "contradicts", "follows"]

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


class ScopeDecisionOut(BaseModel):
    mode: str  # "explicit" | "all" | "auto" | "ambiguous"
    context: str | None = None
    candidates: list[dict[str, Any]] | None = None
    disambiguation_id: str | None = None
    results_by_candidate: dict[str, list[MemorySearchResult]] | None = None


class RelatedNeighbor(BaseModel):
    edge_id: UUID | None
    relation: str
    direction: str  # "outgoing" | "incoming"
    note: str | None
    created_by: str | None
    created_at: datetime | None
    virtual: bool  # True only for the derived 'supersedes' chain (never in memory_edges)
    memory: MemoryOut


class SearchRelatedNeighbor(RelatedNeighbor):
    cross_context: bool


class MemorySearchResponse(BaseModel):
    results: list[MemorySearchResult]
    scope_decision: ScopeDecisionOut
    related: list[SearchRelatedNeighbor] | None = None


class DisambiguationResolveIn(BaseModel):
    context: str = Field(min_length=1)


class DisambiguationResolveOut(BaseModel):
    id: str
    query: str
    chosen_context: str
    resolved_by: str
    learned_terms: list[str]


class ContextMemoryCount(BaseModel):
    context: str
    status: str
    count: int


class DisambiguationStats(BaseModel):
    total: int
    auto: int
    agent: int
    user: int
    local_model: int  # Fase 4 (plan_v2.md SS8), OFF por defecto - ver README
    unresolved: int


class PreferenceOut(BaseModel):
    context: str
    term: str
    weight: float


class StatsOut(BaseModel):
    memories_by_context: list[ContextMemoryCount]
    disambiguations: DisambiguationStats
    preferences_learned: list[PreferenceOut]


class DisambiguationExportRow(BaseModel):
    query: str
    candidates: list[dict[str, Any]]
    chosen_context: str | None
    resolved_by: str | None
    created_at: datetime


# --------------------------------------------------------------------------- Fase 3: grafo ligero


class EdgeCreate(BaseModel):
    to_memory: UUID
    relation: EdgeRelation
    note: str | None = None


class EdgeOut(BaseModel):
    id: UUID
    from_memory: UUID
    to_memory: UUID
    relation: str
    note: str | None
    created_by: str
    created_at: datetime


class RelatedResponse(BaseModel):
    memory_id: UUID
    related: list[RelatedNeighbor]


class TimelineItem(MemoryOut):
    effective_date: datetime


class TimelineResponse(BaseModel):
    items: list[TimelineItem]


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

    def get_settings_dep(request: Request) -> Settings:
        return request.app.state.settings

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

    @app.get("/memories/search", response_model=MemorySearchResponse, dependencies=[Depends(require_auth)])
    async def search_memories(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
        settings_dep: Annotated[Settings, Depends(get_settings_dep)],
        agent: Annotated[str, Depends(agent_name)],
        q: Annotated[str, Query(min_length=1)],
        context: str | None = None,
        scope: str = "auto",
        type: str | None = None,  # noqa: A002
        limit: Annotated[int, Query(ge=1, le=50)] = 5,
        include_superseded: bool = False,
        expand: bool = False,
    ):
        """Hybrid retrieval. `scope` controls the Context Engine (plan_v2.md SS7):

        - explicit `context=<slug>` (or `scope=<slug>` not equal to auto/all) filters
          directly, no engine involved - `scope_decision.mode == "explicit"`.
        - `scope=all` (default off) is the Fase 1 behavior: unfiltered hybrid search
          across every context - kept for the benchmark's control arm.
        - `scope=auto` (default) runs the Context Engine: a cheap deterministic
          preliminary scoring decides whether one context clearly dominates
          (`mode == "auto"`, results already filtered to it) or the query is
          ambiguous between 2-4 contexts (`mode == "ambiguous"`, `results` comes back
          empty on purpose - see `results_by_candidate` for a few results per
          candidate instead, and `disambiguation_id` to resolve the choice later via
          POST /disambiguations/{id}/resolve).

        `expand` (Fase 3, default False): when True, adds a separate `related` block
        with the 1-hop neighbors (memory_edges + the virtual supersedence chain) of
        the top 3 `results`, deduplicated, capped at 5. `related` is NEVER merged into
        `results` - it must not perturb retrieval metrics. Neighbors from a different
        context than the one this search resolved to (if any) are included only when
        reached via an explicit edge (`cross_context: true` on that entry) - explicit
        edges are intentional user-made bridges, not contamination.
        """
        explicit_context = context
        if explicit_context is None and scope not in ("auto", "all"):
            explicit_context = scope

        decided_context: str | None = None

        try:
            if explicit_context is not None:
                results = await hybrid_search(
                    pool,
                    provider,
                    query=q,
                    context_slug=explicit_context,
                    type_=type,
                    limit=limit,
                    include_superseded=include_superseded,
                )
                scope_decision = ScopeDecisionOut(mode="explicit", context=explicit_context)
                decided_context = explicit_context

            elif scope == "all":
                results = await hybrid_search(
                    pool,
                    provider,
                    query=q,
                    context_slug=None,
                    type_=type,
                    limit=limit,
                    include_superseded=include_superseded,
                )
                scope_decision = ScopeDecisionOut(mode="all")

            else:  # scope == "auto"
                decision: ScopeDecision = await decide_scope(
                    pool,
                    provider,
                    query=q,
                    type_=type,
                    include_superseded=include_superseded,
                    settings=settings_dep,
                    agent=agent,
                    limit=limit,
                )
                if decision.mode == "auto":
                    if decision.context is not None:
                        results = await hybrid_search(
                            pool,
                            provider,
                            query=q,
                            context_slug=decision.context,
                            type_=type,
                            limit=limit,
                            include_superseded=include_superseded,
                        )
                    else:
                        results = []
                    scope_decision = ScopeDecisionOut(
                        mode="auto",
                        context=decision.context,
                        disambiguation_id=decision.disambiguation_id,
                    )
                    decided_context = decision.context
                else:
                    results = []
                    scope_decision = ScopeDecisionOut(
                        mode="ambiguous",
                        candidates=[c.to_dict() for c in (decision.candidates or [])],
                        disambiguation_id=decision.disambiguation_id,
                        results_by_candidate={
                            slug: [row_to_memory_out(r) for r in rows]
                            for slug, rows in (decision.results_by_candidate or {}).items()
                        },
                    )
        except UnknownContextError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown context '{exc}'",
            ) from exc

        related: list[dict[str, Any]] | None = None
        if expand:
            related = await get_search_related(
                pool,
                top_results=results,
                decided_context=decided_context,
            )

        await log_audit(
            pool,
            agent=agent,
            action="search",
            memory_id=None,
            detail={
                "query": q,
                "context": context,
                "scope": scope,
                "type": type,
                "limit": limit,
                "include_superseded": include_superseded,
                "result_count": len(results),
                "scope_mode": scope_decision.mode,
                "expand": expand,
                "related_count": len(related) if related is not None else None,
            },
        )
        return MemorySearchResponse(
            results=[row_to_memory_out(r) for r in results],
            scope_decision=scope_decision,
            related=related,
        )

    @app.post(
        "/disambiguations/{disambiguation_id}/resolve",
        response_model=DisambiguationResolveOut,
        dependencies=[Depends(require_auth)],
    )
    async def resolve_disambiguation_endpoint(
        disambiguation_id: str,
        body: DisambiguationResolveIn,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        agent: Annotated[str, Depends(agent_name)],
    ):
        try:
            result = await resolve_disambiguation(
                pool,
                disambiguation_id=disambiguation_id,
                context_slug=body.context,
                resolved_by="agent",
                agent=agent,
            )
        except DisambiguationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no disambiguation found with id '{exc}'",
            ) from exc
        except DisambiguationAlreadyResolvedError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"disambiguation '{exc}' was already resolved",
            ) from exc
        except UnknownContextError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown context '{exc}'",
            ) from exc

        await log_audit(
            pool,
            agent=agent,
            action="resolve_disambiguation",
            memory_id=None,
            detail={"disambiguation_id": disambiguation_id, "chosen_context": body.context},
        )
        return DisambiguationResolveOut(**result)

    @app.get("/stats", response_model=StatsOut, dependencies=[Depends(require_auth)])
    async def get_stats(pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        memory_rows = await pool.fetch(
            """
            SELECT c.slug AS context, m.status, count(*) AS count
            FROM memories m JOIN contexts c ON c.id = m.context_id
            GROUP BY c.slug, m.status
            ORDER BY c.slug, m.status
            """
        )
        disamb_row = await pool.fetchrow(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE resolved_by = 'auto') AS auto,
                count(*) FILTER (WHERE resolved_by = 'agent') AS agent,
                count(*) FILTER (WHERE resolved_by = 'user') AS "user",
                count(*) FILTER (WHERE resolved_by = 'local_model') AS local_model,
                count(*) FILTER (WHERE resolved_by IS NULL) AS unresolved
            FROM disambiguation_log
            """
        )
        preference_rows = await pool.fetch(
            """
            SELECT c.slug AS context, cp.term, cp.weight
            FROM context_preferences cp
            JOIN contexts c ON c.id = cp.context_id
            ORDER BY cp.weight DESC, c.slug, cp.term
            LIMIT 100
            """
        )
        return StatsOut(
            memories_by_context=[
                ContextMemoryCount(context=r["context"], status=r["status"], count=r["count"])
                for r in memory_rows
            ],
            disambiguations=DisambiguationStats(**dict(disamb_row)),
            preferences_learned=[
                PreferenceOut(context=r["context"], term=r["term"], weight=r["weight"])
                for r in preference_rows
            ],
        )

    @app.get(
        "/disambiguations/export",
        response_model=list[DisambiguationExportRow],
        dependencies=[Depends(require_auth)],
    )
    async def export_disambiguations(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        resolved_only: bool = False,
    ):
        """Vuelca `disambiguation_log` completo (o solo lo resuelto con
        `resolved_only=true`) como {query, candidates, chosen_context, resolved_by,
        created_at} -- el dataset crudo que `knowledgeos export-disambiguations`
        (CLI) convierte a JSONL para un futuro fine-tuning local (Fase 4,
        plan_v2.md SS8). No filtra por agente ni pagina: es un export completo,
        pensado para correr una vez que hay volumen real.
        """
        import json

        where = "WHERE chosen_context IS NOT NULL" if resolved_only else ""
        rows = await pool.fetch(
            f"""
            SELECT query, candidates, chosen_context, resolved_by, created_at
            FROM disambiguation_log
            {where}
            ORDER BY created_at
            """
        )
        out = []
        for r in rows:
            d = dict(r)
            raw_candidates = d["candidates"]
            d["candidates"] = json.loads(raw_candidates) if isinstance(raw_candidates, str) else raw_candidates
            out.append(d)
        return out

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

    # ---------------------------------------------------------------- Fase 3: grafo ligero

    @app.post(
        "/memories/{memory_id}/edges",
        status_code=status.HTTP_201_CREATED,
        response_model=EdgeOut,
        dependencies=[Depends(require_auth)],
    )
    async def create_edge(
        memory_id: UUID,
        body: EdgeCreate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        agent: Annotated[str, Depends(agent_name)],
    ):
        try:
            row = await add_edge(
                pool,
                from_memory=memory_id,
                to_memory=body.to_memory,
                relation=body.relation,
                note=body.note,
                created_by=agent,
            )
        except MemoryNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"memory '{exc}' not found",
            ) from exc
        except DuplicateEdgeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        await log_audit(
            pool,
            agent=agent,
            action="add_edge",
            memory_id=memory_id,
            detail={
                "edge_id": str(row["id"]),
                "from_memory": str(row["from_memory"]),
                "to_memory": str(row["to_memory"]),
                "relation": row["relation"],
            },
        )
        return row

    @app.delete(
        "/memories/{memory_id}/edges/{edge_id}",
        dependencies=[Depends(require_auth)],
    )
    async def delete_edge_endpoint(
        memory_id: UUID,
        edge_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        agent: Annotated[str, Depends(agent_name)],
    ):
        try:
            row = await delete_edge(pool, memory_id=memory_id, edge_id=edge_id)
        except EdgeNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no edge '{exc}' touching memory '{memory_id}'",
            ) from exc

        await log_audit(
            pool,
            agent=agent,
            action="delete_edge",
            memory_id=memory_id,
            detail={
                "edge_id": str(row["id"]),
                "from_memory": str(row["from_memory"]),
                "to_memory": str(row["to_memory"]),
                "relation": row["relation"],
            },
        )
        return {"id": str(edge_id), "status": "deleted"}

    @app.get(
        "/memories/{memory_id}/related",
        response_model=RelatedResponse,
        dependencies=[Depends(require_auth)],
    )
    async def get_related_endpoint(
        memory_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        relation: str | None = None,
    ):
        """1-hop neighbors of `memory_id`, both directions, plus the derived
        supersedence chain (`relation="supersedes"`) - see `graph.get_related`.
        """
        try:
            neighbors = await get_related(pool, memory_id=memory_id, relation=relation)
        except MemoryNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"memory '{exc}' not found",
            ) from exc
        except InvalidRelationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid relation '{exc}': must be one of {RELATION_VOCAB} or 'supersedes'",
            ) from exc

        return RelatedResponse(
            memory_id=memory_id,
            related=[
                {**item, "memory": row_to_memory_out(item["memory"])} for item in neighbors
            ],
        )

    # ---------------------------------------------------------------- Fase 3: timeline

    @app.get("/timeline", response_model=TimelineResponse, dependencies=[Depends(require_auth)])
    async def timeline(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        context: str | None = None,
        from_: Annotated[datetime | None, Query(alias="from")] = None,
        to: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ):
        """Memorias `episodic`/`decision`, ordenadas por fecha efectiva
        (`occurred_at`, o `created_at` si no hay `occurred_at`) mas reciente primero -
        pensado para "que paso en X las ultimas semanas". Filtros: `context` (slug),
        `from`/`to` (rango de fecha efectiva), `limit` (default 50).
        """
        try:
            rows = await get_timeline(
                pool,
                context=context,
                from_date=from_,
                to_date=to,
                limit=limit,
            )
        except GraphUnknownContextError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown context '{exc}'",
            ) from exc

        return TimelineResponse(items=[row_to_memory_out(r) for r in rows])

    return app


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.10f}" for v in vec) + "]"


app = create_app()
