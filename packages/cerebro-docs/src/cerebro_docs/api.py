"""FastAPI app: categories, documents (CRUD + full-text search + versioning), health.

Auth: every endpoint except /health requires `Authorization: Bearer <API_TOKEN>`. The
optional `X-Agent-Name` header identifies the calling agent for `documents.created_by`
(default "unknown") - a named token's `name` always wins over it, same rule as
cerebro-memory's `agent_name()`.

NOTA (ecosistema-cerebro.md SS2): a diferencia de `POST /memories` en cerebro-memory
(que rechaza credenciales via `security.py`), cerebro-docs NO filtra contenido. Es una
decision explicita de Jose Luis, no un descuido - por eso `security.py` no se
reutiliza aqui.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from cerebro_docs.auth import (
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


class CategoryCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1)
    description: str | None = None


class CategoryUpdate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None


class CategoryOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class CategoryDeleteOut(BaseModel):
    slug: str
    status: str
    documents_deleted: int


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    category: str = Field(min_length=1)
    slug: str | None = None


class DocumentReplace(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    category: str = Field(min_length=1)
    slug: str | None = None  # None conserva el slug actual del documento


class DocumentOut(BaseModel):
    id: UUID
    category: str
    slug: str
    title: str
    content: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    score: float | None = None  # solo poblado por GET /documents?q=...


class SectionPatchIn(BaseModel):
    heading: str = Field(min_length=1)
    operation: Operation
    body: str = ""
    create_if_missing: bool = False
    new_heading_level: int = Field(default=2, ge=1, le=6)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1)
    allowed_categories: list[str] | None = None


class TokenOut(BaseModel):
    id: UUID
    name: str
    scopes: list[str]
    allowed_categories: list[str] | None
    created_at: datetime
    revoked_at: datetime | None


class TokenCreateOut(TokenOut):
    token: str  # plaintext - solo en ESTA respuesta, nunca de nuevo


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
        """Identidad usada para `documents.created_by`. Un token con nombre siempre
        pisa el header (mismo criterio que `agent_name()` en cerebro-memory); el
        token root no tiene identidad propia y cae al header (default "unknown")."""
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

    # ---------------------------------------------------------------- health

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        ok = await check_health(request.app.state.pool)
        if not ok:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unreachable")
        return {"status": "ok"}

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
                pool, name=body.name, scopes=body.scopes, allowed_categories=body.allowed_categories
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
        rows = await list_api_tokens(pool)
        return [dict(r) for r in rows]

    @app.delete("/tokens/{name}", response_model=TokenOut, dependencies=[Depends(require_scope("admin"))])
    async def revoke_token(name: str, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        try:
            row = await revoke_api_token(pool, name)
        except TokenNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no active token named '{exc}'",
            ) from exc
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
                INSERT INTO categories (slug, name, description)
                VALUES ($1, $2, $3)
                RETURNING id, slug, name, description, created_at, updated_at
                """,
                body.slug,
                body.name,
                body.description,
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
        rows = await pool.fetch(
            "SELECT id, slug, name, description, created_at, updated_at FROM categories ORDER BY created_at"
        )
        return [dict(r) for r in rows if principal.category_allowed(r["slug"])]

    @app.patch("/categories/{slug}", response_model=CategoryOut)
    async def update_category(
        slug: str,
        body: CategoryUpdate,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        """Renombra/edita una categoria SIN tocar `documents` - las rutas
        `/{categoria}/{slug}` de sus documentos cambian gratis al resolver el join,
        porque `category_id` es un FK, nunca texto copiado (ecosistema-cerebro.md
        SS6)."""
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

        if not fields:
            row = await pool.fetchrow(
                "SELECT id, slug, name, description, created_at, updated_at FROM categories WHERE slug = $1", slug
            )
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"category '{slug}' not found")
            return dict(row)

        fields.append("updated_at = now()")
        params.append(slug)
        sql = (
            f"UPDATE categories SET {', '.join(fields)} WHERE slug = ${len(params)} "
            "RETURNING id, slug, name, description, created_at, updated_at"
        )
        try:
            row = await pool.fetchrow(sql, *params)
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"category slug '{body.slug}' already exists",
            ) from exc
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"category '{slug}' not found")
        return dict(row)

    @app.delete("/categories/{slug}", response_model=CategoryDeleteOut)
    async def delete_category(
        slug: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("admin"))],
        force: bool = False,
    ):
        """409 si la categoria todavia tiene documentos, salvo `?force=true` - en cuyo
        caso el `ON DELETE CASCADE` de `documents.category_id` (y de
        `document_versions.document_id` sobre esos documentos) hace la cascada
        completa con un solo DELETE. `admin`-only, igual criterio que
        `DELETE /contexts/{slug}` en cerebro-memory (destructivo)."""
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
                INSERT INTO documents (category_id, slug, title, content, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, category_id, slug, title, content, created_by, created_at, updated_at
                """,
                category_row["id"],
                slug,
                body.title,
                body.content,
                creator,
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

    @app.get("/documents/{category}/{slug}", response_model=DocumentOut)
    async def get_document(
        category: str,
        slug: str,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
    ):
        require_category_allowed(principal, category)
        row = await pool.fetchrow(
            """
            SELECT d.id, d.slug, d.title, d.content, d.created_by, d.created_at, d.updated_at, c.slug AS category
            FROM documents d JOIN categories c ON c.id = d.category_id
            WHERE c.slug = $1 AND d.slug = $2
            """,
            category,
            slug,
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
        return row_to_document_out(row)

    @app.get("/documents", response_model=list[DocumentOut])
    async def list_documents(
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("read"))],
        category: str | None = None,
        q: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        """Sin `q`: listado por `updated_at desc`. Con `q`: full-text simple
        (`websearch_to_tsquery('simple', ...)` + `ts_rank`), SIEMPRE parametrizado -
        `q` viaja como bind param de asyncpg, nunca interpolado en el texto del SQL
        (ecosistema-cerebro.md SS15, criterio de auditoria)."""
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

        where_clause = (" WHERE " + " AND ".join(filters)) if filters else ""

        params.append(limit)
        limit_param = len(params)
        params.append(offset)
        offset_param = len(params)

        sql = f"""
            SELECT d.id, d.slug, d.title, d.content, d.created_by, d.created_at, d.updated_at,
                   c.slug AS category, {select_score}
            FROM documents d JOIN categories c ON c.id = d.category_id
            {where_clause}
            {order_clause}
            LIMIT ${limit_param} OFFSET ${offset_param}
        """
        rows = await pool.fetch(sql, *params)
        return [row_to_document_out(r) for r in rows]

    # ---------------------------------------------------------------- documents: write (versioned)

    @app.patch("/documents/{document_id}", response_model=DocumentOut)
    async def replace_document(
        document_id: UUID,
        body: DocumentReplace,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        """Reemplazo completo (incluye mover de categoria). Transaccionalidad
        obligatoria (ecosistema-cerebro.md SS12): `SELECT ... FOR UPDATE` de la fila,
        INSERT del snapshot previo en `document_versions`, y el `UPDATE`, todo en UNA
        sola transaccion - nunca se lee el contenido en una query separada sin lock."""
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
                        RETURNING id, category_id, slug, title, content, created_by, created_at, updated_at
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

        return row_to_document_out({**dict(new_row), "category": new_category["slug"]})

    @app.patch("/documents/{document_id}/section", response_model=DocumentOut)
    async def patch_document_section(
        document_id: UUID,
        body: SectionPatchIn,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        principal: Annotated[Principal, Depends(require_scope("write"))],
    ):
        """Parche parcial por heading (ecosistema-cerebro.md SS12): seccion = desde el
        heading hasta el siguiente del mismo nivel o superior. Misma transaccionalidad
        obligatoria que `replace_document` - `FOR UPDATE` + snapshot + UPDATE en una
        sola transaccion."""
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
                    RETURNING id, category_id, slug, title, content, created_by, created_at, updated_at
                    """,
                    new_content,
                    document_id,
                )

        return row_to_document_out({**dict(new_row), "category": old["category"]})

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
                await conn.execute("DELETE FROM documents WHERE id = $1", document_id)  # cascada a document_versions

        return {"id": str(document_id), "status": "deleted"}

    return app


app = create_app()
