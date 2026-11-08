-- cerebro-auth v2 - password login (Phase 1A).
--
-- * `users.password_hash` already exists (001, nullable) and now holds an argon2id
--   hash for users that can log in with a password. NULL = token-only user.
-- * `users.failed_attempts` / `users.locked_until`: brute-force protection for
--   `POST /login/password` (N consecutive failures lock the account for a while).
-- * `api_tokens.expires_at`: session tokens minted by a password login expire.
--   NULL = never expires (every pre-existing token). Every service that reads
--   `cerebro_auth.api_tokens` must treat an expired token like a revoked one.

ALTER TABLE cerebro_auth.users
    ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN locked_until    TIMESTAMPTZ;

ALTER TABLE cerebro_auth.api_tokens
    ADD COLUMN expires_at TIMESTAMPTZ;

-- Session tokens (named "session:<user>:<random>") are looked up by hash on every
-- request; the existing partial index on token_hash already covers that path.
