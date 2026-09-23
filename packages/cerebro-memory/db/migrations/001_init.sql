-- KnowledgeOS Phase 1 - initial schema (plan_v2.md sections 5.1 and 9)
--
-- NOTE: {{EMBEDDING_DIM}} is replaced at startup time (src/knowledgeos/db.py)
-- with the value of EMBEDDING_DIMENSION before running this file, so that the
-- `embedding` column matches the configured embeddings model.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- Contexts: the unit of isolation. First-class citizen.
CREATE TABLE contexts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,        -- "expense-tracker", "finanzas-personales"
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- project | domain | person | org
    description TEXT,                        -- used by the LLM to disambiguate
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE memories (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_id    UUID NOT NULL REFERENCES contexts(id),
    type          TEXT NOT NULL,              -- semantic | episodic | procedural | decision
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    importance    REAL DEFAULT 0.5,           -- 0..1
    confidence    REAL DEFAULT 0.8,           -- 0..1
    source        TEXT,                       -- agent/channel that created it
    status        TEXT DEFAULT 'active',      -- active | superseded | archived
    superseded_by UUID REFERENCES memories(id),
    occurred_at   TIMESTAMPTZ,                -- when the fact occurred (episodic)
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now(),
    embedding     vector({{EMBEDDING_DIM}})
);

CREATE INDEX memories_embedding_hnsw_idx ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX memories_fts_idx ON memories USING gin (to_tsvector('spanish', title || ' ' || content));
CREATE INDEX memories_context_id_idx ON memories (context_id);
CREATE INDEX memories_status_idx ON memories (status);

-- Audit log: which agent did what. Foundation of the security history.
CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    agent      TEXT NOT NULL,
    action     TEXT NOT NULL,                -- search | remember | update | forget
    memory_id  UUID,
    detail     JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
