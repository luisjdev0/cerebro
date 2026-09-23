-- KnowledgeOS v1.0 - Token-based authorization with scopes (plan_v2.md SS9 "Authorization v1.0")
--
-- api_tokens: named tokens (the agent's real identity, overrides X-Agent-Name in the
-- audit log), explicit scopes (read/write/admin) and an optional restriction of
-- allowed contexts (NULL = all). The plaintext token value is NEVER stored:
-- only its SHA-256 (`token_hash`) -- see src/knowledgeos/auth.py.
--
-- The legacy .env token (API_TOKEN) keeps working separately, as an implicit
-- "root" token with all scopes and all contexts (backward compatibility,
-- see README "Security") -- it has no row here, it's compared directly against
-- the value of settings.api_token.
--
-- Revocation: soft (revoked_at), the row is never deleted (trail for the audit log /
-- so historical tokens can be viewed). The partial unique index allows reusing a
-- `name` after revoking the previous token with that name.

CREATE TABLE api_tokens (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash       TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    scopes           TEXT[] NOT NULL CHECK (scopes <@ ARRAY['read', 'write', 'admin']::text[] AND array_length(scopes, 1) > 0),
    allowed_contexts TEXT[],                  -- NULL = all contexts
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at       TIMESTAMPTZ
);

-- Only one ACTIVE token per name at a time (allows recreating a revoked name).
CREATE UNIQUE INDEX api_tokens_active_name_idx ON api_tokens (name) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_token_hash_active_idx ON api_tokens (token_hash) WHERE revoked_at IS NULL;
