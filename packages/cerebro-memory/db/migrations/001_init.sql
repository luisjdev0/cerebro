-- KnowledgeOS Fase 1 - schema inicial (plan_v2.md secciones 5.1 y 9)
--
-- NOTA: {{EMBEDDING_DIM}} es reemplazado en tiempo de arranque (src/knowledgeos/db.py)
-- por el valor de EMBEDDING_DIMENSION antes de ejecutar este archivo, para que la
-- columna `embedding` coincida con el modelo de embeddings configurado.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- Contextos: la unidad de aislamiento. Ciudadano de primera clase.
CREATE TABLE contexts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,        -- "expense-tracker", "finanzas-personales"
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- project | domain | person | org
    description TEXT,                        -- usado por el LLM para desambiguar
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
    source        TEXT,                       -- agente/canal que la creo
    status        TEXT DEFAULT 'active',      -- active | superseded | archived
    superseded_by UUID REFERENCES memories(id),
    occurred_at   TIMESTAMPTZ,                -- cuando ocurrio el hecho (episodicas)
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now(),
    embedding     vector({{EMBEDDING_DIM}})
);

CREATE INDEX memories_embedding_hnsw_idx ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX memories_fts_idx ON memories USING gin (to_tsvector('spanish', title || ' ' || content));
CREATE INDEX memories_context_id_idx ON memories (context_id);
CREATE INDEX memories_status_idx ON memories (status);

-- Audit log: que agente hizo que. Base de la historia de seguridad.
CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    agent      TEXT NOT NULL,
    action     TEXT NOT NULL,                -- search | remember | update | forget
    memory_id  UUID,
    detail     JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
