"""FastAPI app: users, groups (+ group module scopes/membership), and API
tokens -- cerebro-auth is the ecosystem's source of truth for identity
(ecosistema-cerebro, step 5).

Auth: every endpoint except `/login` and `/health` requires `Authorization:
Bearer <token>` AND that the resolved principal have `access_level == 'admin'`
(see `cerebro_auth.auth.require_admin`) -- unlike the other three services,
cerebro-auth has no read/write/admin *scope* gate of its own to enforce here;
the `scopes` (read/write) carried by a token are data this service manages on
behalf of the other services, not something that gates cerebro-auth's own
routes.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from cerebro_auth.auth import (
    DuplicateTokenNameError,
    InvalidScopesError,
    TokenNotFoundError,
    create_api_token,
    list_api_tokens,
    require_admin,
    resolve_login,
    revoke_api_token,
)
from cerebro_auth.config import Settings, get_settings
from cerebro_auth.db import apply_migrations, check_health, create_pool

logger = logging.getLogger("cerebro_auth.api")

Module = Literal["memory", "docs", "flows"]


# --------------------------------------------------------------------------- models


class StrictIn(BaseModel):
    """Same criterion as the other three services: an unknown field is a 422, it
    is never silently ignored."""

    model_config = ConfigDict(extra="forbid")


class UserCreate(StrictIn):
    name: str = Field(min_length=1, max_length=100)
    email: str | None = None
    access_level: Literal["user", "owner", "admin"] = "user"


class UserOut(BaseModel):
    id: UUID
    name: str
    email: str | None
    access_level: str
    created_at: datetime
    revoked_at: datetime | None


class GroupCreate(StrictIn):
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1)


class GroupOut(BaseModel):
    id: UUID
    slug: str
    name: str
    created_at: datetime


class GroupScopesIn(StrictIn):
    allowed_modules: list[Module] = Field(min_length=1)
    module_scopes: dict[str, Any] | None = None


class GroupScopesOut(BaseModel):
    group_id: UUID
    allowed_modules: list[str]
    module_scopes: dict[str, Any]


class GroupMemberIn(StrictIn):
    user: str = Field(min_length=1)


class TokenCreate(StrictIn):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Literal["read", "write"]] = Field(min_length=1)
    allowed_modules: list[Module] | None = None
    module_scopes: dict[str, Any] | None = None
    user: str | None = None
    access_level: Literal["user", "owner", "admin"] | None = None
    value: str | None = Field(default=None, min_length=1)


class TokenOut(BaseModel):
    id: UUID
    name: str
    user_id: UUID | None
    scopes: list[str]
    access_level: str | None
    allowed_modules: list[str] | None
    module_scopes: dict[str, Any]
    created_at: datetime
    revoked_at: datetime | None


class TokenCreateOut(TokenOut):
    token: str


class LoginIn(StrictIn):
    token: str = Field(min_length=1)


class LoginOut(BaseModel):
    name: str
    access_level: str
    allowed_modules: list[str] | None


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

    app = FastAPI(title="cerebro-auth", version="0.1.0", lifespan=lifespan)

    # ---------------------------------------------------------------- dependencies

    def get_pool(request: Request) -> asyncpg.Pool:
        return request.app.state.pool

    # ---------------------------------------------------------------- health

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        ok = await check_health(request.app.state.pool)
        if not ok:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unreachable")
        return {"status": "ok"}

    # ---------------------------------------------------------------- login

    @app.post("/login", response_model=LoginOut)
    async def login(body: LoginIn, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        resolved = await resolve_login(pool, settings, body.token)
        if resolved is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or revoked token")
        return LoginOut(
            name=resolved.name,
            access_level=resolved.access_level,
            allowed_modules=sorted(resolved.allowed_modules) if resolved.allowed_modules is not None else None,
        )

    # ---------------------------------------------------------------- users

    @app.post(
        "/users",
        status_code=status.HTTP_201_CREATED,
        response_model=UserOut,
        dependencies=[Depends(require_admin)],
    )
    async def create_user(body: UserCreate, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        try:
            row = await pool.fetchrow(
                """
                INSERT INTO users (name, email, access_level)
                VALUES ($1, $2, $3)
                RETURNING id, name, email, access_level, created_at, revoked_at
                """,
                body.name,
                body.email,
                body.access_level,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a user named '{body.name}' already exists",
            ) from exc
        return dict(row)

    @app.get("/users", response_model=list[UserOut], dependencies=[Depends(require_admin)])
    async def list_users(pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        rows = await pool.fetch(
            "SELECT id, name, email, access_level, created_at, revoked_at FROM users ORDER BY created_at"
        )
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- groups

    @app.post(
        "/groups",
        status_code=status.HTTP_201_CREATED,
        response_model=GroupOut,
        dependencies=[Depends(require_admin)],
    )
    async def create_group(body: GroupCreate, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        try:
            row = await pool.fetchrow(
                "INSERT INTO groups (slug, name) VALUES ($1, $2) RETURNING id, slug, name, created_at",
                body.slug,
                body.name,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a group with slug '{body.slug}' already exists",
            ) from exc
        return dict(row)

    async def _get_group_id(pool: asyncpg.Pool, slug: str) -> UUID:
        group = await pool.fetchrow("SELECT id FROM groups WHERE slug = $1", slug)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no group with slug '{slug}'")
        return group["id"]

    @app.post(
        "/groups/{slug}/scopes",
        response_model=GroupScopesOut,
        dependencies=[Depends(require_admin)],
    )
    async def set_group_scopes(
        slug: str,
        body: GroupScopesIn,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    ):
        group_id = await _get_group_id(pool, slug)
        row = await pool.fetchrow(
            """
            INSERT INTO group_scopes (group_id, allowed_modules, module_scopes)
            VALUES ($1, $2, $3)
            ON CONFLICT (group_id) DO UPDATE
                SET allowed_modules = EXCLUDED.allowed_modules,
                    module_scopes = EXCLUDED.module_scopes
            RETURNING group_id, allowed_modules, module_scopes
            """,
            group_id,
            list(body.allowed_modules),
            json.dumps(body.module_scopes or {}),
        )
        data = dict(row)
        data["module_scopes"] = json.loads(data["module_scopes"]) if isinstance(data["module_scopes"], str) else data["module_scopes"]
        return data

    @app.post(
        "/groups/{slug}/members",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    async def add_group_member(
        slug: str,
        body: GroupMemberIn,
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    ):
        group_id = await _get_group_id(pool, slug)
        user = await pool.fetchrow("SELECT id FROM users WHERE name = $1", body.user)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no user named '{body.user}'")

        await pool.execute(
            """
            INSERT INTO user_groups (user_id, group_id) VALUES ($1, $2)
            ON CONFLICT (user_id, group_id) DO NOTHING
            """,
            user["id"],
            group_id,
        )
        return {"group": slug, "user": body.user, "status": "member"}

    # ---------------------------------------------------------------- tokens

    @app.post(
        "/tokens",
        status_code=status.HTTP_201_CREATED,
        response_model=TokenCreateOut,
        dependencies=[Depends(require_admin)],
    )
    async def create_token(body: TokenCreate, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        if body.user is not None:
            if body.access_level is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"access_level is inherited from user '{body.user}' and must not be set on the "
                        "token itself - omit it, or omit 'user' to create a service/root-adjacent token "
                        "whose access_level is set directly"
                    ),
                )
            user_row = await pool.fetchrow("SELECT id FROM users WHERE name = $1", body.user)
            if user_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no user named '{body.user}'")
            user_id = user_row["id"]
            access_level = None
        else:
            if body.access_level is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "access_level is required when creating a token with no 'user' - a "
                        "service/root-adjacent token has no user to inherit it from"
                    ),
                )
            if body.allowed_modules is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "allowed_modules is required when creating a token with no 'user' - a "
                        "service/root-adjacent token is the sole source of its own module scope"
                    ),
                )
            user_id = None
            access_level = body.access_level

        try:
            created = await create_api_token(
                pool,
                name=body.name,
                scopes=body.scopes,
                user_id=user_id,
                access_level=access_level,
                allowed_modules=body.allowed_modules,
                module_scopes=body.module_scopes,
                value=body.value,
            )
        except InvalidScopesError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except DuplicateTokenNameError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"an active token named '{exc}' already exists - revoke it first",
            ) from exc
        return TokenCreateOut(**created)

    @app.get("/tokens", response_model=list[TokenOut], dependencies=[Depends(require_admin)])
    async def list_tokens(pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        return await list_api_tokens(pool)

    @app.delete("/tokens/{name}", response_model=TokenOut, dependencies=[Depends(require_admin)])
    async def revoke_token(name: str, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        try:
            return await revoke_api_token(pool, name)
        except TokenNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no active token named '{exc}'") from exc

    # ---------------------------------------------------------------- backup

    @app.post("/backup", dependencies=[Depends(require_admin)])
    async def backup(request: Request) -> StreamingResponse:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pg_dump",
                request.app.state.settings.database_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="pg_dump is not available in this deployment",
            ) from exc

        async def stream() -> AsyncIterator[bytes]:
            assert proc.stdout is not None
            try:
                while chunk := await proc.stdout.read(65536):
                    yield chunk
            finally:
                await proc.wait()
                if proc.returncode != 0:
                    assert proc.stderr is not None
                    stderr = await proc.stderr.read()
                    logger.error(
                        "pg_dump exited with status %s: %s",
                        proc.returncode,
                        stderr.decode(errors="replace"),
                    )

        filename = f"cerebro-backup-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sql"
        return StreamingResponse(
            stream(),
            media_type="application/sql",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return app


app = create_app()
