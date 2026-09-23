-- cerebro-flows v1 - initial schema (luisjdev-pendientes/cerebro-flows).
--
-- Everything lives in the `cerebro_flows` schema (never in `public`, the same
-- criterion as cerebro_docs/cerebro_memory: sibling modules in the same Postgres
-- instance, never cross-schema access). The runner (db.py) already created the
-- schema and its own qualified `schema_migrations` table BEFORE running this file
-- (same pattern as cerebro_docs/db.py).

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() (idempotent if another
                                           -- module already installed it in `public`)

-- Categories: namespace for a flow AND the prefix of its sequential id (`code`,
-- e.g. "INC" for category "incident" -> "INC-22"). This is NOT the `categories`
-- table from cerebro-docs -- cerebro-flows is intentionally an independent module
-- (SS1 of the design document), with no FK or join across schemas.
CREATE TABLE flow_categories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Flow definitions. `code` is the human-readable sequential id ("INC-22"),
-- auto-generated per category on save (SS6) -- `id` (UUID) is the real internal
-- identifier, so that renaming/moving never breaks FKs (same principle as
-- documents.id in cerebro-docs). The YAML itself NEVER lives here -- it lives
-- versioned in flow_definition_versions, this table only points to the current
-- version.
CREATE TABLE flow_definitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id     UUID NOT NULL REFERENCES flow_categories(id) ON DELETE CASCADE,
    code            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    current_version INT NOT NULL DEFAULT 1,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A complete snapshot of the YAML on every version -- same pattern as
-- document_versions in cerebro-docs. A flow_run stays anchored to
-- definition_version (see below), so editing the definition never changes the
-- behavior of a run already in progress.
CREATE TABLE flow_definition_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    definition_id  UUID NOT NULL REFERENCES flow_definitions(id) ON DELETE CASCADE,
    version_number INT NOT NULL,
    yaml_content   TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (definition_id, version_number)
);

CREATE INDEX flow_definition_versions_definition_id_idx ON flow_definition_versions (definition_id);
CREATE INDEX flow_definitions_category_id_idx ON flow_definitions (category_id);

-- Header of an execution. The MUTABLE pointer (current_step_id, etc.) lives in
-- Redis, not here (SS5 of the design document) -- this row is only the immutable
-- anchor: which definition/version it started with, and how it ended.
CREATE TABLE flow_runs (
    run_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    definition_id      UUID NOT NULL REFERENCES flow_definitions(id),
    definition_version INT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'in_progress'
                            CHECK (status IN ('in_progress', 'completed', 'aborted')),
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);

-- Append-only, one row per real transition -- written AT THE MOMENT it happens
-- (not only at the end), so that the audit trail survives even if the run is
-- abandoned and its Redis TTL expires (SS5 of the design document).
CREATE TABLE flow_run_events (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id     UUID NOT NULL REFERENCES flow_runs(run_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,  -- step_entered | checkpoint_approved | checkpoint_rejected
                                -- | decision_taken | completed | aborted
    step_id    TEXT,
    payload    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX flow_run_events_run_id_idx ON flow_run_events (run_id, created_at);

-- Named tokens with scopes -- an exact mirror of
-- cerebro_docs/db/migrations/001_init.sql (api_tokens), with `allowed_categories`
-- instead of another module's contexts/categories.
CREATE TABLE api_tokens (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash         TEXT UNIQUE NOT NULL,
    name               TEXT NOT NULL,
    scopes             TEXT[] NOT NULL CHECK (scopes <@ ARRAY['read', 'write', 'admin']::text[] AND array_length(scopes, 1) > 0),
    allowed_categories TEXT[],                 -- NULL = all categories
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX api_tokens_active_name_idx ON api_tokens (name) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_token_hash_active_idx ON api_tokens (token_hash) WHERE revoked_at IS NULL;
