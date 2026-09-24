"""FastAPI app: categories, documents (CRUD + full-text search + versioning), health.

Auth: every endpoint except /health requires `Authorization: Bearer <API_TOKEN>`. The
optional `X-Agent-Name` header identifies the calling agent for `documents.created_by`
(default "unknown") - a named token's `name` always wins over it, same rule as
cerebro-memory's `agent_name()`.

NOTE (ecosistema-cerebro.md SS2): unlike `POST /memories` in cerebro-memory
(which rejects credentials via `security.py`), cerebro-docs does NOT filter content. This is
an explicit decision by Jose Luis, not an oversight - that's why `security.py` isn't
reused here.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cerebro_docs.auth import Principal, get_principal, require_scope
from cerebro_docs.config import Settings, get_settings
from cerebro_docs.db import apply_migrations, check_health, create_pool
from cerebro_docs.sections import (
    VALID_OPERATIONS,
    AmbiguousHeadingError,
    HeadingNotFoundError,
    InvalidOperationError,
    Operation,
    apply_section_patch,
)
from cerebro_docs.slugs import slugify

logger = logging.getLogger("cerebro_docs.api")


# --------------------------------------------------------------------------- models


class StrictIn(BaseModel):
    """Base for INPUT models: an unknown field is a 422, never silently ignored.

    Without this, a client typo (e.g. sending `content` instead of `body` in a
    section patch) would silently fall through to the real field's default and could
    wipe out content - same conservative criterion as ambiguous headings: an explicit
    error, never guessing (ecosistema-cerebro.md SS12)."""

    model_config = ConfigDict(extra="forbid")


class CategoryCreate(StrictIn):
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1)
    description: str | None = None
    hidden: bool = False
    # `locked` is only set here, at creation -- it's not part of CategoryUpdate and has no
    # endpoint of its own: if it could be unlocked later, "locked" wouldn't mean
    # anything. For categories that must never be able to be revealed (e.g. cerebro-flows'
    # reference categories), see luisjdev-pendientes/ecosistema-cerebro.
    locked: bool = False

    @model_validator(mode="after")
    def _locked_requires_hidden(self) -> "CategoryCreate":
        if self.locked and not self.hidden:
            raise ValueError("una categoria 'locked' debe ser 'hidden' (bloquear solo tiene sentido para ocultar)")
        return self


class CategoryUpdate(StrictIn):
    slug: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    hidden: bool | None = None


class CategoryOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    hidden: bool
    locked: bool
    created_at: datetime
    updated_at: datetime


class CategoryDeleteOut(BaseModel):
    slug: str
    status: str
    documents_deleted: int


class DocumentCreate(StrictIn):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    category: str = Field(min_length=1)
    slug: str | None = None


class DocumentReplace(StrictIn):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    category: str = Field(min_length=1)
    slug: str | None = None  # None keeps the document's current slug


class DocumentOut(BaseModel):
    id: UUID
    category: str
    slug: str
    title: str
    content: str
    status: str
    created_by: str | None
    owner_user_id: UUID | None = None  # the human owner (cerebro_auth.users.id), distinct from created_by
    created_at: datetime
    updated_at: datetime
    score: float | None = None  # only populated by GET /documents?q=...
    # only populated when GET /documents/{category}/{slug} resolved via slug_redirects
    # (the requested route is no longer the current one) -- see docs_get in cerebro-mcp/server.py,
    # which uses this to alert the model to stop referencing the old route.
    redirected_from: dict[str, str] | None = None


class DocumentVersionOut(BaseModel):
    version_number: int
    category: str
    title: str
    content: str
    created_at: datetime


class SectionPatchIn(StrictIn):
    heading: str = Field(min_length=1)
    operation: Operation
    body: str = ""
    create_if_missing: bool = False
    new_heading_level: int = Field(default=2, ge=1, le=6)


class StatsOut(BaseModel):
    """Minimal mirror of cerebro-memory's GET /stats (ecosistema-cerebro.md SS11):
    cerebro-docs has no disambiguations or learned preferences, so only these
    three counts make sense here."""

    categories: int
    documents: int
    versions: int


# --------------------------------------------------------------------------- app wiring


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await create_pool(settings)
        applied = await apply_migrations(pool, settings)
        if applied:
            logger.info("applied migrations: %s", applied)

        app.state.pool = pool
        app.state.settings = settings
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="cerebro-docs", version="0.1.0", lifespan=lifespan)

    # ---------------------------------------------------------------- dependencies

    def get_pool(request: Request) -> asyncpg.Pool:
        return request.app.state.pool

    def created_by_name(
        principal: Annotated[Principal, Depends(get_principal)],
        x_agent_name: Annotated[str | None, Header()] = None,
    ) -> str:
        """Identity used for `documents.created_by`. A named token always
        overrides the header (same rule as `agent_name()` in cerebro-memory); the
        root token has no identity of its own and falls back to the header (default "unknown")."""
        if not principal.is_root:
            return principal.name
        return x_agent_name or "unknown"

    def row_to_document_out(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data.pop("category_id", None)
        data.setdefault("score", None)
        return data

    def require_category_allowed(principal: Principal, slug: str) -> None:
        if not principal.category_allowed(slug):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token '{principal.name}' is not allowed to access category '{slug}'",
            )

    def apply_owner_filter(filters: list[str], params: list[Any], owner_filter: dict[str, Any] | None) -> None:
        """Appends the ownership gate (separate from and additional to the
        category gate above) to an in-progress `WHERE` clause being built as
        `filters`/`params`. Always filters on the `documents` table aliased `d`."""
        if owner_filter is None:
            return
        if "user_id" in owner_filter:
            params.append(owner_filter["user_id"])
            filters.append(f"d.owner_user_id = ${len(params)}")
        elif "user_id_in" in owner_filter:
            params.append(owner_filter["user_id_in"])
            filters.append(f"d.owner_user_id = ANY(${len(params)}::uuid[])")

    # ---------------------------------------------------------------- health

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        ok = await check_health(request.app.state.pool)
        if not ok:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unreachable")
        return {"status": "ok"}

    # ---------------------------------------------------------------- stats

    @app.get("/stats", response_model=StatsOut)
    async def get_stats(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        """Counts of categories/documents/versions (minimal mirror of cerebro-memory's
        GET /stats). With `allowed_categories` restricted, counts only what's visible
        to that token -- same silent-filtering criterion as the rest of cerebro-docs'
        listings (never a 403 on an aggregate, it's just narrowed)."""
        if principal.allowed_categories is None:
            categories = await pool.fetchval("SELECT count(*) FROM categories")
            documents = await pool.fetchval("SELECT count(*) FROM documents")
            versions = await pool.fetchval("SELECT count(*) FROM document_versions")
        else:
            allowed = list(principal.allowed_categories)
            categories = await pool.fetchval(
                "SELECT count(*) FROM categories WHERE slug = ANY($1::text[])", allowed
            )
            documents = await pool.fetchval(
                """
                SELECT count(*) FROM documents d JOIN categories c ON c.id = d.category_id
                WHERE c.slug = ANY($1::text[])
                """,
                allowed,
            )
            versions = await pool.fetchval(
                """
                SELECT count(*) FROM document_versions dv
                JOIN documents d ON d.id = dv.document_id
                JOIN categories c ON c.id = d.category_id
                WHERE c.slug = ANY($1::text[])
                """,
                allowed,
            )
        return StatsOut(categories=categories, documents=documents, versions=versions)

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
                INSERT INTO categories (slug, name, description, hidden, locked)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, slug, name, description, hidden, locked, created_at, updated_at
                """,
                body.slug,
                body.name,
                body.description,
                body.hidden,
                body.locked,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"category slug '{body.slug}' already exists",
            ) from exc
        return dict(row)

    @app.get("/categories", response_model=list[CategoryOut])
    async def list_categories(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        """`hidden=true` never shows up here (no parameter to turn it off -- see
        luisjdev-pendientes/ecosistema-cerebro): a hidden category is only
        reachable by knowing its exact slug in advance (create_document/get_document
        don't filter by `hidden`, only this listing does)."""
        rows = await pool.fetch(
            """
            SELECT id, slug, name, description, hidden, locked, created_at, updated_at
            FROM categories WHERE hidden = false ORDER BY created_at
            """
        )
        return [dict(r) for r in rows if principal.category_allowed(r["slug"])]

    @app.patch("/categories/{slug}", response_model=CategoryOut)
    async def update_category(
        slug: str,
        body: CategoryUpdate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        """Renames/edits a category WITHOUT touching `documents` - its documents'
        `/{category}/{slug}` routes change for free when the join resolves,
        because `category_id` is an FK, never copied text (ecosistema-cerebro.md
        SS6). If the slug changes, registers a redirect for each document in the
        category (its external route moves even though `category_id` doesn't change) - see
        luisjdev-pendientes/ecosistema-cerebro, "Slug redirects"."""
        require_category_allowed(principal, slug)
        if body.slug is not None:
            require_category_allowed(principal, body.slug)

        fields: list[str] = []
        params: list[Any] = []
        if body.slug is not None:
            params.append(body.slug)
            fields.append(f"slug = ${len(params)}")
        if body.name is not None:
            params.append(body.name)
            fields.append(f"name = ${len(params)}")
        if body.description is not None:
            params.append(body.description)
            fields.append(f"description = ${len(params)}")
        if body.hidden is not None:
            params.append(body.hidden)
            fields.append(f"hidden = ${len(params)}")

        async with pool.acquire() as conn:
            async with conn.transaction():
                old = await conn.fetchrow(
                    "SELECT id, slug, hidden, locked FROM categories WHERE slug = $1 FOR UPDATE", slug
                )
                if old is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"category '{slug}' not found")

                if old["locked"] and body.hidden is False:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"category '{slug}' is locked - it can never be revealed (hidden=false)",
                    )

                if not fields:
                    row = await conn.fetchrow(
                        "SELECT id, slug, name, description, hidden, locked, created_at, updated_at "
                        "FROM categories WHERE id = $1",
                        old["id"],
                    )
                    return dict(row)

                fields.append("updated_at = now()")
                params.append(old["id"])
                sql = (
                    f"UPDATE categories SET {', '.join(fields)} WHERE id = ${len(params)} "
                    "RETURNING id, slug, name, description, hidden, locked, created_at, updated_at"
                )
                try:
                    row = await conn.fetchrow(sql, *params)
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"category slug '{body.slug}' already exists",
                    ) from exc

                if body.slug is not None and body.slug != old["slug"]:
                    await conn.execute(
                        """
                        INSERT INTO slug_redirects (old_category, old_slug, document_id)
                        SELECT $1, d.slug, d.id FROM documents d WHERE d.category_id = $2
                        ON CONFLICT (old_category, old_slug)
                        DO UPDATE SET document_id = EXCLUDED.document_id, created_at = now()
                        """,
                        old["slug"],
                        old["id"],
                    )

        return dict(row)

    @app.delete("/categories/{slug}", response_model=CategoryDeleteOut)
    async def delete_category(
        slug: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("admin"))],
        force: bool = False,
    ):
        """409 if the category still has documents, unless `?force=true` - in which
        case `documents.category_id`'s `ON DELETE CASCADE` (and
        `document_versions.document_id`'s over those documents) does the full
        cascade with a single DELETE. `admin`-only, same criterion as
        `DELETE /contexts/{slug}` in cerebro-memory (destructive)."""
        require_category_allowed(principal, slug)
        async with pool.acquire() as conn:
            async with conn.transaction():
                category_id = await conn.fetchval("SELECT id FROM categories WHERE slug = $1 FOR UPDATE", slug)
                if category_id is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"category '{slug}' not found")

                doc_count = await conn.fetchval("SELECT count(*) FROM documents WHERE category_id = $1", category_id)
                if doc_count > 0 and not force:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"category '{slug}' still has {doc_count} documents - "
                            "pass ?force=true to hard-delete them (and their version history) along with the category"
                        ),
                    )
                await conn.execute("DELETE FROM categories WHERE id = $1", category_id)

        return CategoryDeleteOut(slug=slug, status="deleted", documents_deleted=doc_count)

    # ---------------------------------------------------------------- documents: create/read/list

    @app.post("/documents", status_code=status.HTTP_201_CREATED, response_model=DocumentOut)
    async def create_document(
        body: DocumentCreate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        creator: Annotated[str, Depends(created_by_name)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        require_category_allowed(principal, body.category)

        category_row = await pool.fetchrow("SELECT id, slug FROM categories WHERE slug = $1", body.category)
        if category_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"categoria '{body.category}' inexistente - creala primero con docs_create_category",
            )

        slug = body.slug or slugify(body.title)

        try:
            row = await pool.fetchrow(
                """
                INSERT INTO documents (category_id, slug, title, content, created_by, owner_user_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, category_id, slug, title, content, status, created_by, owner_user_id, created_at, updated_at
                """,
                category_row["id"],
                slug,
                body.title,
                body.content,
                creator,
                principal.user_id,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"ya existe un documento con slug '{slug}' en la categoria '{body.category}' "
                    f"(GET /documents/{body.category}/{slug}) - usa PATCH /documents/{{id}} (docs_update) para editarlo"
                ),
            ) from exc

        return row_to_document_out({**dict(row), "category": category_row["slug"]})

    @app.get("/documents/{document_id}/versions", response_model=list[DocumentVersionOut])
    async def get_document_versions(
        document_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        """Registered BEFORE `GET /documents/{category}/{slug}` on purpose: both
        routes have 2 segments after `/documents/`, and this one must win over that
        (more generic) one so `/documents/<uuid>/versions` doesn't get interpreted as
        category='<uuid>', slug='versions'. Read-only -- no restore endpoint,
        see luisjdev-pendientes/ecosistema-cerebro, "document_versions with no
        way to read it"."""
        doc = await pool.fetchrow(
            "SELECT d.id, c.slug AS category FROM documents d JOIN categories c ON c.id = d.category_id WHERE d.id = $1",
            document_id,
        )
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
        require_category_allowed(principal, doc["category"])

        rows = await pool.fetch(
            """
            SELECT dv.version_number, c.slug AS category, dv.title, dv.content, dv.created_at
            FROM document_versions dv JOIN categories c ON c.id = dv.category_id
            WHERE dv.document_id = $1
            ORDER BY dv.version_number DESC
            """,
            document_id,
        )
        return [dict(r) for r in rows]

    @app.get("/documents/{category}/{slug}", response_model=DocumentOut)
    async def get_document(
        category: str,
        slug: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        """Exact route: works the same for archived documents and for `hidden`
        categories (neither is filtered here, only in listings) -- but it DOES
        respect `principal.owner_filter` (a separate, additional gate from the
        category one): reading a document by its exact known path must not bypass
        ownership. If there's no direct match, falls back to `slug_redirects` --
        the real document at its current route ALWAYS wins over a redirect; the
        fallback only runs when the direct lookup already returned a 404 (see
        luisjdev-pendientes/ecosistema-cerebro) -- and a redirected document must
        ALSO pass the ownership check before being returned."""
        require_category_allowed(principal, category)

        direct_filters = ["c.slug = $1", "d.slug = $2"]
        direct_params: list[Any] = [category, slug]
        apply_owner_filter(direct_filters, direct_params, principal.owner_filter)
        row = await pool.fetchrow(
            f"""
            SELECT d.id, d.slug, d.title, d.content, d.status, d.created_by, d.owner_user_id,
                   d.created_at, d.updated_at, c.slug AS category
            FROM documents d JOIN categories c ON c.id = d.category_id
            WHERE {' AND '.join(direct_filters)}
            """,
            *direct_params,
        )
        if row is not None:
            return row_to_document_out(row)

        redirect_filters = ["r.old_category = $1", "r.old_slug = $2"]
        redirect_params: list[Any] = [category, slug]
        apply_owner_filter(redirect_filters, redirect_params, principal.owner_filter)
        redirected = await pool.fetchrow(
            f"""
            SELECT d.id, d.slug, d.title, d.content, d.status, d.created_by, d.owner_user_id,
                   d.created_at, d.updated_at, c.slug AS category
            FROM slug_redirects r
            JOIN documents d ON d.id = r.document_id
            JOIN categories c ON c.id = d.category_id
            WHERE {' AND '.join(redirect_filters)}
            """,
            *redirect_params,
        )
        if redirected is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
        require_category_allowed(principal, redirected["category"])
        return row_to_document_out(
            {**dict(redirected), "redirected_from": {"category": category, "slug": slug}}
        )

    async def _query_documents(
        pool: asyncpg.Pool,
        principal: Principal,
        *,
        doc_status: str,
        category: str | None,
        q: str | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Shared between `GET /documents` (`doc_status='active'`) and
        `GET /documents/archived` (`doc_status='archived'`). Without `q`: listed by
        `updated_at desc`. With `q`: simple full-text (`websearch_to_tsquery('simple',
        ...)` + `ts_rank`), ALWAYS parameterized - `q` travels as an asyncpg bind
        param, never interpolated into the SQL text (ecosistema-cerebro.md SS15).

        An explicit `category` is taken as-is (a `hidden` category is still
        reachable by knowing its exact slug in advance); without `category`, `hidden`
        categories are also excluded from the unfiltered listing ("browse everything").

        `principal.owner_filter`, when set, is applied as an ADDITIONAL filter on
        top of the category one above -- a separate "who owns this" gate, never a
        substitute for it."""
        filters: list[str] = ["d.status = $1"]
        params: list[Any] = [doc_status]

        if category is not None:
            params.append(category)
            filters.append(f"c.slug = ${len(params)}")
        else:
            filters.append("c.hidden = false")
            if principal.allowed_categories is not None:
                params.append(list(principal.allowed_categories))
                filters.append(f"c.slug = ANY(${len(params)}::text[])")

        apply_owner_filter(filters, params, principal.owner_filter)

        if q:
            params.append(q)
            q_param = len(params)
            score_expr = f"ts_rank(d.search_vector, websearch_to_tsquery('simple', ${q_param}))"
            filters.append(f"d.search_vector @@ websearch_to_tsquery('simple', ${q_param})")
            select_score = f"{score_expr} AS score"
            order_clause = "ORDER BY score DESC, d.updated_at DESC"
        else:
            select_score = "NULL::real AS score"
            order_clause = "ORDER BY d.updated_at DESC"

        where_clause = " WHERE " + " AND ".join(filters)

        params.append(limit)
        limit_param = len(params)
        params.append(offset)
        offset_param = len(params)

        sql = f"""
            SELECT d.id, d.slug, d.title, d.content, d.status, d.created_by, d.owner_user_id,
                   d.created_at, d.updated_at, c.slug AS category, {select_score}
            FROM documents d JOIN categories c ON c.id = d.category_id
            {where_clause}
            {order_clause}
            LIMIT ${limit_param} OFFSET ${offset_param}
        """
        rows = await pool.fetch(sql, *params)
        return [row_to_document_out(r) for r in rows]

    @app.get("/documents", response_model=list[DocumentOut])
    async def list_documents(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
        category: str | None = None,
        q: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if category is not None:
            require_category_allowed(principal, category)
        return await _query_documents(
            pool, principal, doc_status="active", category=category, q=q, limit=limit, offset=offset
        )

    @app.get("/documents/archived", response_model=list[DocumentOut])
    async def list_archived_documents(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
        category: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        """Dedicated listing of archived documents (never mixed with
        `GET /documents`) - see luisjdev-pendientes/ecosistema-cerebro, "Archiving"."""
        if category is not None:
            require_category_allowed(principal, category)
        return await _query_documents(
            pool, principal, doc_status="archived", category=category, q=None, limit=limit, offset=offset
        )

    # ---------------------------------------------------------------- documents: write (versioned)

    @app.patch("/documents/{document_id}", response_model=DocumentOut)
    async def replace_document(
        document_id: UUID,
        body: DocumentReplace,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        """Full replacement (includes moving categories). Mandatory transactionality
        (ecosistema-cerebro.md SS12): `SELECT ... FOR UPDATE` of the row,
        INSERT of the prior snapshot into `document_versions`, and the `UPDATE`, all in ONE
        single transaction - content is never read in a separate query without a lock."""
        async with pool.acquire() as conn:
            async with conn.transaction():
                old = await conn.fetchrow(
                    """
                    SELECT d.*, c.slug AS category
                    FROM documents d JOIN categories c ON c.id = d.category_id
                    WHERE d.id = $1
                    FOR UPDATE OF d
                    """,
                    document_id,
                )
                if old is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

                require_category_allowed(principal, old["category"])
                require_category_allowed(principal, body.category)

                new_category = await conn.fetchrow("SELECT id, slug FROM categories WHERE slug = $1", body.category)
                if new_category is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"categoria '{body.category}' inexistente - creala primero con docs_create_category",
                    )

                new_slug = body.slug or old["slug"]

                next_version = await conn.fetchval(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM document_versions WHERE document_id = $1",
                    document_id,
                )
                await conn.execute(
                    """
                    INSERT INTO document_versions (document_id, content, title, category_id, version_number)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    document_id,
                    old["content"],
                    old["title"],
                    old["category_id"],
                    next_version,
                )

                try:
                    new_row = await conn.fetchrow(
                        """
                        UPDATE documents
                        SET title = $1, content = $2, category_id = $3, slug = $4, updated_at = now()
                        WHERE id = $5
                        RETURNING id, category_id, slug, title, content, status, created_by, owner_user_id, created_at, updated_at
                        """,
                        body.title,
                        body.content,
                        new_category["id"],
                        new_slug,
                        document_id,
                    )
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"ya existe un documento con slug '{new_slug}' en la categoria '{body.category}'",
                    ) from exc

                # Slug redirect (luisjdev-pendientes/ecosistema-cerebro): if the
                # external route changed (slug and/or category), registers the OLD
                # coordinate -> this document_id, so GET /documents/{cat}/{slug} with the
                # old route keeps resolving (with a warning) instead of silently 404ing.
                if new_slug != old["slug"] or body.category != old["category"]:
                    await conn.execute(
                        """
                        INSERT INTO slug_redirects (old_category, old_slug, document_id)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (old_category, old_slug)
                        DO UPDATE SET document_id = EXCLUDED.document_id, created_at = now()
                        """,
                        old["category"],
                        old["slug"],
                        document_id,
                    )

        return row_to_document_out({**dict(new_row), "category": new_category["slug"]})

    @app.patch("/documents/{document_id}/section", response_model=DocumentOut)
    async def patch_document_section(
        document_id: UUID,
        body: SectionPatchIn,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        """Partial patch by heading (ecosistema-cerebro.md SS12): section = from the
        heading to the next one at the same level or higher. Same mandatory
        transactionality as `replace_document` - `FOR UPDATE` + snapshot + UPDATE in one
        single transaction."""
        async with pool.acquire() as conn:
            async with conn.transaction():
                old = await conn.fetchrow(
                    """
                    SELECT d.*, c.slug AS category
                    FROM documents d JOIN categories c ON c.id = d.category_id
                    WHERE d.id = $1
                    FOR UPDATE OF d
                    """,
                    document_id,
                )
                if old is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

                require_category_allowed(principal, old["category"])

                try:
                    new_content = apply_section_patch(
                        old["content"],
                        heading=body.heading,
                        operation=body.operation,
                        body=body.body,
                        create_if_missing=body.create_if_missing,
                        new_heading_level=body.new_heading_level,
                    )
                except InvalidOperationError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"invalid operation '{exc}': must be one of {VALID_OPERATIONS}",
                    ) from exc
                except AmbiguousHeadingError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"heading '{exc}' is ambiguous (appears more than once) - cannot patch without a unique target",
                    ) from exc
                except HeadingNotFoundError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"heading '{exc}' not found - pass create_if_missing=true to create it",
                    ) from exc

                next_version = await conn.fetchval(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM document_versions WHERE document_id = $1",
                    document_id,
                )
                await conn.execute(
                    """
                    INSERT INTO document_versions (document_id, content, title, category_id, version_number)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    document_id,
                    old["content"],
                    old["title"],
                    old["category_id"],
                    next_version,
                )
                new_row = await conn.fetchrow(
                    """
                    UPDATE documents SET content = $1, updated_at = now() WHERE id = $2
                    RETURNING id, category_id, slug, title, content, status, created_by, owner_user_id, created_at, updated_at
                    """,
                    new_content,
                    document_id,
                )

        return row_to_document_out({**dict(new_row), "category": old["category"]})

    async def _set_document_status(
        document_id: UUID, new_status: str, pool: asyncpg.Pool, principal: Principal
    ) -> dict[str, Any]:
        """Shared by `docs_archive`/`docs_unarchive`: changing `status` isn't a
        content edit, so -unlike replace_document/
        patch_document_section- it doesn't touch `document_versions`."""
        row = await pool.fetchrow(
            """
            SELECT d.id, c.slug AS category
            FROM documents d JOIN categories c ON c.id = d.category_id
            WHERE d.id = $1
            """,
            document_id,
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
        require_category_allowed(principal, row["category"])

        new_row = await pool.fetchrow(
            """
            UPDATE documents SET status = $1, updated_at = now() WHERE id = $2
            RETURNING id, category_id, slug, title, content, status, created_by, owner_user_id, created_at, updated_at
            """,
            new_status,
            document_id,
        )
        return row_to_document_out({**dict(new_row), "category": row["category"]})

    @app.post("/documents/{document_id}/archive", response_model=DocumentOut)
    async def archive_document(
        document_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        return await _set_document_status(document_id, "archived", pool, principal)

    @app.post("/documents/{document_id}/unarchive", response_model=DocumentOut)
    async def unarchive_document(
        document_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        return await _set_document_status(document_id, "active", pool, principal)

    @app.delete("/documents/{document_id}")
    async def delete_document(
        document_id: UUID,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT d.id, c.slug AS category
                    FROM documents d JOIN categories c ON c.id = d.category_id
                    WHERE d.id = $1
                    FOR UPDATE OF d
                    """,
                    document_id,
                )
                if row is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
                require_category_allowed(principal, row["category"])
                await conn.execute("DELETE FROM documents WHERE id = $1", document_id)  # cascades to document_versions

        return {"id": str(document_id), "status": "deleted"}

    return app


app = create_app()
