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
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from cerebro_auth.auth import (
    DuplicateTokenNameError,
    InvalidScopesError,
    TokenNotFoundError,
    Principal,
    create_api_token,
    get_principal,
    list_api_tokens,
    require_admin,
    resolve_login,
    revoke_api_token,
)
from cerebro_auth.config import Settings, get_settings
from cerebro_auth.db import apply_migrations, check_health, create_pool
from cerebro_auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    AccountLockedError,
    InvalidCredentialsError,
    UserNotFoundError,
    check_user_password,
    hash_password,
    password_login,
    revoke_session,
    set_user_password,
)

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
    # Optional: when given, the user can log in with username + password.
    password: SecretStr | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class UserOut(BaseModel):
    id: UUID
    name: str
    email: str | None
    access_level: str
    created_at: datetime
    revoked_at: datetime | None
    has_password: bool = False
    groups: list[str] = []


class UserUpdate(StrictIn):
    """The name is the user's identity (tokens, audit trail) and never changes. `active=false`
    deactivates the account (every token it owns stops working); `true` reactivates it."""

    email: str | None = None  # an empty string clears it
    access_level: Literal["user", "owner", "admin"] | None = None
    active: bool | None = None


class GroupCreate(StrictIn):
    slug: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1)


class GroupOut(BaseModel):
    id: UUID
    slug: str
    name: str
    created_at: datetime


class GroupUpdate(StrictIn):
    name: str = Field(min_length=1)


class GroupDetailOut(GroupOut):
    members: list[str]
    allowed_modules: list[str] | None
    module_scopes: dict[str, Any]


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
    expires_at: datetime | None = None


class TokenToggle(StrictIn):
    active: bool


class TokenCreateOut(TokenOut):
    token: str


class LoginIn(StrictIn):
    token: str = Field(min_length=1)


class LoginOut(BaseModel):
    name: str
    access_level: str
    allowed_modules: list[str] | None


class PasswordLoginIn(StrictIn):
    name: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class PasswordLoginOut(LoginOut):
    token: str
    expires_at: datetime


class PasswordSetIn(StrictIn):
    password: SecretStr = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    # Required when a non-admin user changes their own password.
    current_password: SecretStr | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH)


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

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body echoes the rejected `input` back. On this service
        # that can be a password, so the echo is dropped (`input`/`ctx`/`url`).
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

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

    @app.post("/login/password", response_model=PasswordLoginOut)
    async def login_password(body: PasswordLoginIn, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        """Username + password login. Returns a session token that expires after
        `session_ttl_hours`. Any failure (unknown user, wrong password, revoked
        user, user with no password) answers the same 401; a locked account
        answers 429 with `Retry-After`."""
        try:
            result = await password_login(pool, settings, body.name, body.password.get_secret_value())
        except AccountLockedError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"too many failed attempts, try again in {exc.retry_after} seconds",
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc
        except InvalidCredentialsError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid username or password"
            ) from exc
        return PasswordLoginOut(**result)

    @app.post("/logout")
    async def logout(
        principal: Annotated[Principal, Depends(get_principal)],
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
        authorization: Annotated[str | None, Header()] = None,
    ):
        """Revoke the session token used for this request. Only session tokens
        (minted by a password login) can be logged out; any other token is
        revoked by name with `DELETE /tokens/{name}`."""
        token = (authorization or "")[len("Bearer ") :]
        if principal.is_root or not await revoke_session(pool, token):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="this token is not a session token; revoke it by name with DELETE /tokens/{name}",
            )
        return {"status": "logged_out"}

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
                INSERT INTO users (name, email, access_level, password_hash)
                VALUES ($1, $2, $3, $4)
                RETURNING id, name, email, access_level, created_at, revoked_at,
                          (password_hash IS NOT NULL) AS has_password
                """,
                body.name,
                body.email,
                body.access_level,
                hash_password(body.password.get_secret_value()) if body.password is not None else None,
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
            """
            SELECT u.id, u.name, u.email, u.access_level, u.created_at, u.revoked_at,
                   (u.password_hash IS NOT NULL) AS has_password,
                   COALESCE((SELECT array_agg(g.slug ORDER BY g.slug) FROM user_groups ug
                             JOIN groups g ON g.id = ug.group_id WHERE ug.user_id = u.id), '{}') AS groups
            FROM users u ORDER BY u.created_at
            """
        )
        return [dict(r) for r in rows]

    @app.patch("/users/{name}", response_model=UserOut, dependencies=[Depends(require_admin)])
    async def update_user(name: str, body: UserUpdate, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        """Portal-only (no client/MCP/CLI mirror). Refuses to leave the system without an
        active admin: demoting or deactivating the last one is a 409."""
        if body.email is None and body.access_level is None and body.active is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="nothing to update")
        async with pool.acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow(
                    "SELECT id, access_level, revoked_at FROM users WHERE name = $1 FOR UPDATE", name
                )
                if user is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no user named '{name}'")
                is_active_admin = user["access_level"] == "admin" and user["revoked_at"] is None
                stays_active_admin = (
                    (body.access_level or user["access_level"]) == "admin"
                    and (body.active if body.active is not None else user["revoked_at"] is None)
                )
                if is_active_admin and not stays_active_admin:
                    others = await conn.fetchval(
                        "SELECT count(*) FROM users WHERE access_level = 'admin' AND revoked_at IS NULL AND id <> $1",
                        user["id"],
                    )
                    if others == 0:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="this is the last active admin - promote another user first",
                        )
                sets: list[str] = []
                params: list[Any] = []
                if body.email is not None:
                    params.append(body.email.strip() or None)
                    sets.append(f"email = ${len(params)}")
                if body.access_level is not None:
                    params.append(body.access_level)
                    sets.append(f"access_level = ${len(params)}")
                if body.active is not None:
                    sets.append("revoked_at = NULL" if body.active else "revoked_at = COALESCE(revoked_at, now())")
                params.append(user["id"])
                await conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ${len(params)}", *params)
                row = await conn.fetchrow(
                    """
                    SELECT u.id, u.name, u.email, u.access_level, u.created_at, u.revoked_at,
                           (u.password_hash IS NOT NULL) AS has_password,
                           COALESCE((SELECT array_agg(g.slug ORDER BY g.slug) FROM user_groups ug
                                     JOIN groups g ON g.id = ug.group_id WHERE ug.user_id = u.id), '{}') AS groups
                    FROM users u WHERE u.id = $1
                    """,
                    user["id"],
                )
        return dict(row)

    @app.post("/users/{name}/password", status_code=status.HTTP_204_NO_CONTENT)
    async def set_password(
        name: str,
        body: PasswordSetIn,
        principal: Annotated[Principal, Depends(get_principal)],
        pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    ):
        """Set a user's password. An admin may set anyone's. A non-admin may only
        change their own, and must prove the current one (which counts toward the
        same lockout as a login). Every session of the user is closed."""
        if not principal.is_admin:
            if principal.name != name:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="only an admin can set another user's password"
                )
            if body.current_password is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="current_password is required to change your own password",
                )
            try:
                await check_user_password(pool, settings, name, body.current_password.get_secret_value())
            except AccountLockedError as exc:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"too many failed attempts, try again in {exc.retry_after} seconds",
                    headers={"Retry-After": str(exc.retry_after)},
                ) from exc
            except InvalidCredentialsError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="current password is incorrect"
                ) from exc
        try:
            await set_user_password(pool, name, body.password.get_secret_value())
        except UserNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no user named '{name}'") from exc

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

    @app.get("/groups", response_model=list[GroupDetailOut], dependencies=[Depends(require_admin)])
    async def list_groups(pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        """Portal-only: every group with its members and the modules/scopes it grants."""
        rows = await pool.fetch(
            """
            SELECT g.id, g.slug, g.name, g.created_at, gs.allowed_modules, gs.module_scopes,
                   COALESCE((SELECT array_agg(u.name ORDER BY u.name) FROM user_groups ug
                             JOIN users u ON u.id = ug.user_id WHERE ug.group_id = g.id), '{}') AS members
            FROM groups g LEFT JOIN group_scopes gs ON gs.group_id = g.id
            ORDER BY g.created_at
            """
        )
        out = []
        for r in rows:
            d = dict(r)
            ms = d["module_scopes"]
            d["module_scopes"] = (json.loads(ms) if isinstance(ms, str) else ms) or {}
            out.append(d)
        return out

    @app.patch("/groups/{slug}", response_model=GroupOut, dependencies=[Depends(require_admin)])
    async def update_group(slug: str, body: GroupUpdate, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        row = await pool.fetchrow(
            "UPDATE groups SET name = $1 WHERE slug = $2 RETURNING id, slug, name, created_at", body.name, slug
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no group with slug '{slug}'")
        return dict(row)

    @app.delete("/groups/{slug}", dependencies=[Depends(require_admin)])
    async def delete_group(slug: str, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        """Memberships and the group's scopes go with it (FK cascade); the users stay."""
        deleted = await pool.fetchval("DELETE FROM groups WHERE slug = $1 RETURNING id", slug)
        if deleted is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no group with slug '{slug}'")
        return {"slug": slug, "status": "deleted"}

    @app.delete("/groups/{slug}/members/{user}", dependencies=[Depends(require_admin)])
    async def remove_group_member(slug: str, user: str, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        group_id = await _get_group_id(pool, slug)
        removed = await pool.fetchval(
            "DELETE FROM user_groups WHERE group_id = $1 AND user_id = (SELECT id FROM users WHERE name = $2) "
            "RETURNING user_id",
            group_id,
            user,
        )
        if removed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"'{user}' is not a member of '{slug}'")
        return {"group": slug, "user": user, "status": "removed"}

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

    @app.patch("/tokens/{token_id}", response_model=TokenOut, dependencies=[Depends(require_admin)])
    async def set_token_active(token_id: UUID, body: TokenToggle, pool: Annotated[asyncpg.Pool, Depends(get_pool)]):
        """Portal-only (no client/MCP/CLI mirror): switch a token off (`revoked_at = now()`) or back
        on (`revoked_at = NULL`). The secret never changes. Turning one on is a 409 if another
        active token already has its name (names are unique among active tokens)."""
        try:
            row = await pool.fetchrow(
                f"""
                UPDATE api_tokens SET revoked_at = {"NULL" if body.active else "COALESCE(revoked_at, now())"}
                WHERE id = $1
                RETURNING id, name, user_id, scopes, access_level, allowed_modules, module_scopes,
                          created_at, revoked_at, expires_at
                """,
                token_id,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="another active token already has this name - switch it off or revoke it first",
            ) from exc
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such token")
        data = dict(row)
        ms = data["module_scopes"]
        data["module_scopes"] = (json.loads(ms) if isinstance(ms, str) else ms) or {}
        return data

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
