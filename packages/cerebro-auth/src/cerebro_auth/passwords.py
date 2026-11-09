"""Password hashing and password login for cerebro-auth (Phase 1A).

Hashing is argon2id (`argon2-cffi` defaults, which follow the RFC 9106 low-memory
profile). Plaintext passwords exist only inside the functions of this module for
the time it takes to hash or verify them: they are never logged, never stored,
never returned.

Login mints a *session token*: an ordinary row in `api_tokens` (so memory/docs/
flows validate it exactly like any other token, with no change to their request
path other than the `expires_at` check) that belongs to the user, inherits the
user's access level and scopes, and expires. Session tokens are recognisable by
their name prefix `session:` -- that is how logout and password changes find
them without a schema change.

Brute-force protection is per user: `max_failed_attempts` consecutive failures
lock the account for `lockout_minutes`. A locked account answers 429 *before*
any password check, so a locked user cannot be guessed at during the lock.
Unknown users, revoked users and users with no password all take the same code
path as a wrong password (a dummy hash is verified to equalise timing) so the
login endpoint does not reveal which usernames exist.
"""

from __future__ import annotations

import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from cerebro_auth.auth import create_api_token, hash_token, resolve_effective_scope
from cerebro_auth.config import Settings

SESSION_PREFIX = "session:"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256

_hasher = PasswordHasher()
# Verified against when the user does not exist / has no password, so the response
# time does not depend on whether the username is real.
_DUMMY_HASH = _hasher.hash("cerebro-dummy-password-for-timing-equalisation")


class InvalidCredentialsError(Exception):
    """Wrong username or password (deliberately indistinguishable)."""


class AccountLockedError(Exception):
    """Too many consecutive failures; `retry_after` is the seconds left."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"account locked, retry in {retry_after}s")
        self.retry_after = retry_after


class UserNotFoundError(LookupError):
    """Raised when setting the password of a user that does not exist."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-shape verification: always performs one argon2 verification, even
    when there is no hash to check against."""
    target = password_hash or _DUMMY_HASH
    try:
        ok = _hasher.verify(target, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False
    return ok and password_hash is not None


def _needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


async def _register_failure(pool: asyncpg.Pool, user_id: Any, settings: Settings) -> None:
    await pool.execute(
        """
        UPDATE users SET
            failed_attempts = CASE WHEN failed_attempts + 1 >= $2 THEN 0 ELSE failed_attempts + 1 END,
            locked_until    = CASE WHEN failed_attempts + 1 >= $2
                                   THEN now() + make_interval(mins => $3)
                                   ELSE locked_until END
        WHERE id = $1
        """,
        user_id,
        settings.max_failed_attempts,
        settings.lockout_minutes,
    )


async def check_user_password(
    pool: asyncpg.Pool, settings: Settings, name: str, password: str
) -> asyncpg.Record:
    """Verify `name`'s password applying lockout rules. Returns the user row on
    success; raises `AccountLockedError` / `InvalidCredentialsError` otherwise."""
    row = await pool.fetchrow(
        """
        SELECT id, name, access_level, password_hash, revoked_at, locked_until,
               EXTRACT(EPOCH FROM (locked_until - now())) AS lock_seconds
        FROM users WHERE name = $1
        """,
        name,
    )
    if row is None:
        verify_password(None, password)
        raise InvalidCredentialsError

    if row["locked_until"] is not None and row["lock_seconds"] is not None and row["lock_seconds"] > 0:
        raise AccountLockedError(int(row["lock_seconds"]) + 1)

    usable = row["revoked_at"] is None and row["password_hash"] is not None
    ok = verify_password(row["password_hash"] if usable else None, password)
    if not ok:
        if usable:
            await _register_failure(pool, row["id"], settings)
        raise InvalidCredentialsError

    if row["password_hash"] is not None and _needs_rehash(row["password_hash"]):
        await pool.execute(
            "UPDATE users SET password_hash = $2 WHERE id = $1", row["id"], hash_password(password)
        )
    await pool.execute(
        "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = $1 AND (failed_attempts <> 0 OR locked_until IS NOT NULL)",
        row["id"],
    )
    return row


# --------------------------------------------------------------------------- master user
#
# The deployment's master user (`ADMIN_USER` / `ADMIN_PASSWORD`) is checked against the
# environment, never stored: the same model as the root token. It signs in with the normal
# password login and gets a regular admin session token (no owning user row). Brute force is
# throttled in memory per process (the same threshold/duration as database users); a restart
# resets it, which is acceptable for one account whose password is chosen by the operator.

_master_failures: dict[str, tuple[int, float]] = {}


def master_configured(settings: Settings) -> bool:
    return bool(settings.admin_user.strip()) and len(settings.admin_password.get_secret_value()) >= MIN_PASSWORD_LENGTH


def is_master_name(settings: Settings, name: str) -> bool:
    return master_configured(settings) and name == settings.admin_user.strip()


def _check_master_password(settings: Settings, name: str, password: str) -> None:
    now = time.monotonic()
    count, locked_until = _master_failures.get(name, (0, 0.0))
    if locked_until > now:
        raise AccountLockedError(int(locked_until - now) + 1)
    expected = settings.admin_password.get_secret_value().encode()
    if not hmac.compare_digest(password.encode(), expected):
        count += 1
        if count >= settings.max_failed_attempts:
            _master_failures[name] = (0, now + settings.lockout_minutes * 60)
        else:
            _master_failures[name] = (count, 0.0)
        raise InvalidCredentialsError
    _master_failures.pop(name, None)


async def authenticate_password(
    pool: asyncpg.Pool, settings: Settings, name: str, password: str
) -> tuple[Any, bool]:
    """Check username + password (lockout rules included) without minting anything.
    Returns `(user_row, is_master)`; the master has no database row."""
    if is_master_name(settings, name):
        _check_master_password(settings, name, password)
        return {"id": None, "name": name, "access_level": "admin"}, True
    return await check_user_password(pool, settings, name, password), False


async def mint_session(
    pool: asyncpg.Pool, settings: Settings, row: Any, master: bool, remember: bool = False
) -> dict[str, Any]:
    """Mint a session token for an already authenticated user (see `authenticate_password`)."""
    # Opportunistic housekeeping: drop sessions that expired more than a week ago.
    await pool.execute(
        "DELETE FROM api_tokens WHERE expires_at IS NOT NULL AND expires_at < now() - interval '7 days'"
    )

    # `remember` (the CLI) = a sliding session: it ends after `remember_session_days` without use.
    slide_days = settings.remember_session_days if remember else None
    expires_at = datetime.now(timezone.utc) + (
        timedelta(days=slide_days) if slide_days else timedelta(hours=settings.session_ttl_hours)
    )
    created = await create_api_token(
        pool,
        name=f"{SESSION_PREFIX}{row['name']}:{secrets.token_hex(4)}",
        scopes=["read", "write"],
        user_id=row["id"],
        # The master has no user row, so the token carries its own level and modules.
        access_level="admin" if master else None,
        allowed_modules=["memory", "docs", "flows"] if master else None,
        module_scopes=None,
        expires_at=expires_at,
        slide_days=slide_days,
    )
    resolved = await resolve_effective_scope(pool, hash_token(created["token"]))
    return {
        "name": row["name"],
        "access_level": row["access_level"],
        "allowed_modules": sorted(resolved.allowed_modules)
        if resolved is not None and resolved.allowed_modules is not None
        else None,
        "token": created["token"],
        "expires_at": expires_at,
    }


async def password_login(
    pool: asyncpg.Pool, settings: Settings, name: str, password: str, remember: bool = False
) -> dict[str, Any]:
    """Authenticate with username + password and mint a session token."""
    row, master = await authenticate_password(pool, settings, name, password)
    return await mint_session(pool, settings, row, master, remember)


async def set_user_password(pool: asyncpg.Pool, name: str, new_password: str) -> None:
    """Set (or replace) a user's password, clear any lockout and close every
    session of that user. Authorisation is the caller's job."""
    new_hash = hash_password(new_password)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE users SET password_hash = $2, failed_attempts = 0, locked_until = NULL
                WHERE name = $1 RETURNING id
                """,
                name,
                new_hash,
            )
            if row is None:
                raise UserNotFoundError(name)
            await conn.execute(
                """
                UPDATE api_tokens SET revoked_at = now()
                WHERE user_id = $1 AND name LIKE $2 AND revoked_at IS NULL
                """,
                row["id"],
                f"{SESSION_PREFIX}%",
            )


async def revoke_session(pool: asyncpg.Pool, token: str) -> bool:
    """Revoke the given token if (and only if) it is a session token."""
    row = await pool.fetchrow(
        """
        UPDATE api_tokens SET revoked_at = now()
        WHERE token_hash = $1 AND name LIKE $2 AND revoked_at IS NULL
        RETURNING id
        """,
        hash_token(token),
        f"{SESSION_PREFIX}%",
    )
    return row is not None
