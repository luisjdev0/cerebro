-- cerebro-docs v1 - initial schema (ecosistema-cerebro.md SS6).
--
-- Everything lives in the `cerebro_docs` schema, never in `public` (shared with the
-- sibling schema `cerebro_memory` in the same Postgres instance, SS8/SS14: never
-- cross-schema access between modules). The runner (db.py) has already run
-- `CREATE SCHEMA IF NOT EXISTS cerebro_docs` and acquired this connection with
-- search_path = "cerebro_docs, public" BEFORE running this file, so the
-- statements here don't qualify the schema explicitly: on a fresh install,
-- `cerebro_docs` is already the first existing schema in the search_path, and every
-- unqualified object lands there in the same boot.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() (idempotent: if
                                           -- cerebro-memory already installed it in `public`
                                           -- for the same instance, this is a no-op)

-- Categories, like cerebro-memory's `contexts`: a formal table with explicit,
-- redistributable creation (renaming a category must not touch its documents -
-- see documents.category_id as an FK, never copied text).
CREATE TABLE categories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Full Markdown documents, NOT truncated or distilled (unlike cerebro-memory's
-- memories). `search_vector` is a generated (STORED) column so the full-text
-- GIN index always stays in sync without a dedicated trigger.
CREATE TABLE documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id   UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    slug          TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', title || ' ' || content)) STORED,
    UNIQUE (category_id, slug)
);

CREATE INDEX documents_search_vector_idx ON documents USING gin (search_vector);
CREATE INDEX documents_category_id_idx ON documents (category_id);
CREATE INDEX documents_updated_at_idx ON documents (updated_at DESC);

-- Full snapshot of the document right BEFORE each update/patch (a safety net
-- against accidental overwrites, with no restore endpoint in v1 - see
-- ecosistema-cerebro.md SS12). ON DELETE CASCADE: deleting a document (or, in cascade,
-- its category with ?force=true) takes its full history with it, never leaving orphans.
CREATE TABLE document_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content        TEXT NOT NULL,
    title          TEXT NOT NULL,
    category_id    UUID NOT NULL,
    version_number INT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_number)
);

CREATE INDEX document_versions_document_id_idx ON document_versions (document_id);

-- Named tokens with scopes (read/write/admin), mirroring
-- cerebro-memory/db/migrations/004_api_tokens.sql but with `allowed_categories`
-- instead of `allowed_contexts`. The root token (.env API_TOKEN) still has no row here - it's
-- compared directly in auth.py.
CREATE TABLE api_tokens (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash        TEXT UNIQUE NOT NULL,
    name              TEXT NOT NULL,
    scopes            TEXT[] NOT NULL CHECK (scopes <@ ARRAY['read', 'write', 'admin']::text[] AND array_length(scopes, 1) > 0),
    allowed_categories TEXT[],                 -- NULL = all categories
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at        TIMESTAMPTZ
);

CREATE UNIQUE INDEX api_tokens_active_name_idx ON api_tokens (name) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_token_hash_active_idx ON api_tokens (token_hash) WHERE revoked_at IS NULL;
