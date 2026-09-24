-- cerebro-auth v1 - initial schema (ecosistema-cerebro auth unification, step 5).
--
-- Everything lives in the `cerebro_auth` schema (never in `public`, same criterion
-- as cerebro_memory/cerebro_docs/cerebro_flows: sibling modules in the same
-- Postgres instance, never cross-schema FKs). The runner (db.py) already created
-- the schema and its own qualified `schema_migrations` table BEFORE running this
-- file (same pattern as the other three packages' db.py).
--
-- gen_random_uuid() is used without CREATE EXTENSION pgcrypto: since PostgreSQL 13
-- it is a core builtin (the image used, pgvector/pgvector:pg17, is well past that),
-- unlike the other three packages' migrations which still carry the pgcrypto
-- extension statement for historical reasons.
--
-- cerebro-auth is the ecosystem's source of truth for identity: users, groups,
-- group-level module scopes, and API tokens. The other three services keep their
-- own local api_tokens table for now (existing tokens), but going forward validate
-- new/rotated tokens against this schema's tables via a read-only replica of the
-- resolution algorithm documented in `auth.resolve_effective_scope`.

CREATE SCHEMA IF NOT EXISTS cerebro_auth;

CREATE TABLE cerebro_auth.users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           TEXT UNIQUE NOT NULL,
    email          TEXT,
    access_level   TEXT NOT NULL DEFAULT 'user'
        CHECK (access_level IN ('user','owner','admin')),
    password_hash  TEXT,                    -- NULL until password login exists (future iteration)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at     TIMESTAMPTZ              -- deactivated account
);

CREATE TABLE cerebro_auth.groups (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cerebro_auth.user_groups (
    user_id   UUID NOT NULL REFERENCES cerebro_auth.users(id) ON DELETE CASCADE,
    group_id  UUID NOT NULL REFERENCES cerebro_auth.groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE cerebro_auth.group_scopes (
    group_id         UUID PRIMARY KEY REFERENCES cerebro_auth.groups(id) ON DELETE CASCADE,
    allowed_modules  TEXT[] NOT NULL
        CHECK (allowed_modules <@ ARRAY['memory','docs','flows']::text[]
               AND array_length(allowed_modules,1) > 0),
    module_scopes    JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE cerebro_auth.api_tokens (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash       TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    user_id          UUID REFERENCES cerebro_auth.users(id) ON DELETE CASCADE,  -- NULL = service/root-adjacent token, no owning user
    scopes           TEXT[] NOT NULL
        CHECK (scopes <@ ARRAY['read','write']::text[] AND array_length(scopes,1) > 0),
    access_level     TEXT
        CHECK (access_level IN ('user','owner','admin')),
    allowed_modules  TEXT[]
        CHECK (allowed_modules IS NULL
               OR (allowed_modules <@ ARRAY['memory','docs','flows']::text[]
                   AND array_length(allowed_modules,1) > 0)),
    module_scopes    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at       TIMESTAMPTZ,
    -- No real redundancy: if the token has a user, the level is defined by
    -- users.access_level and this column must be NULL; if it has no user
    -- (service/root-adjacent token), this column is the sole source and is
    -- mandatory. Never both values at once.
    CHECK ((user_id IS NULL) = (access_level IS NOT NULL)),
    -- Same criterion for allowed_modules: a service/root-adjacent token (no
    -- user) is the sole source of its own scope, so it's mandatory. A user's
    -- token can omit it (inherits from the user/their groups) or provide it
    -- (a narrower cut than what's inherited) -- never wider.
    CHECK (user_id IS NOT NULL OR allowed_modules IS NOT NULL)
);
CREATE UNIQUE INDEX api_tokens_active_name_idx
    ON cerebro_auth.api_tokens (name) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_token_hash_active_idx
    ON cerebro_auth.api_tokens (token_hash) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_user_id_idx ON cerebro_auth.api_tokens (user_id);
