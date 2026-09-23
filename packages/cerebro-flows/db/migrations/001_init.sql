-- cerebro-flows v1 - schema inicial (luisjdev-pendientes/cerebro-flows).
--
-- Todo vive en el schema `cerebro_flows` (nunca en `public`, mismo criterio que
-- cerebro_docs/cerebro_memory: modulos hermanos en la misma instancia Postgres,
-- nunca acceso cruzado de esquemas). El runner (db.py) ya creo el schema y su propia
-- `schema_migrations` calificada ANTES de correr este archivo (mismo patron que
-- cerebro_docs/db.py).

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() (idempotente si otro
                                           -- modulo ya la instalo en `public`)

-- Categorias: namespace de un flujo Y prefijo de su id correlativo (`code`, p.ej.
-- "INC" para la categoria "incident" -> "INC-22"). NO es la tabla `categories` de
-- cerebro-docs -- cerebro-flows es un modulo independiente a proposito (SS1 del
-- documento de diseno), sin FK ni join entre schemas.
CREATE TABLE flow_categories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Definiciones de flujo. `code` es el id correlativo humano ("INC-22"), autogenerado
-- por categoria al guardar (SS6) -- `id` (UUID) es el identificador interno real,
-- para que renombrar/mover nunca rompa FKs (mismo principio que documents.id en
-- cerebro-docs). El YAML en si NUNCA vive aqui -- vive versionado en
-- flow_definition_versions, esta tabla solo apunta a la version vigente.
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

-- Snapshot completo del YAML en cada version -- mismo patron que document_versions
-- de cerebro-docs. Un flow_run queda anclado a definition_version (ver abajo), asi
-- que editar la definicion nunca cambia el comportamiento de una ejecucion en curso.
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

-- Cabecera de una ejecucion. El puntero MUTABLE (current_step_id, etc.) vive en
-- Redis, no aqui (SS5 del documento de diseno) -- esta fila es solo el ancla
-- inmutable: que definicion/version arranco, y como termino.
CREATE TABLE flow_runs (
    run_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    definition_id      UUID NOT NULL REFERENCES flow_definitions(id),
    definition_version INT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'in_progress'
                            CHECK (status IN ('in_progress', 'completed', 'aborted')),
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);

-- Append-only, una fila por transicion real -- escrita EN EL MOMENTO en que ocurre
-- (no solo al finalizar), para que el rastro de auditoria sobreviva aunque el run se
-- abandone y expire por TTL en Redis (SS5 del documento de diseno).
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

-- Tokens con nombre y scopes -- espejo exacto de cerebro_docs/db/migrations/001_init.sql
-- (api_tokens), con `allowed_categories` en vez de contexts/categorias de otro modulo.
CREATE TABLE api_tokens (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash         TEXT UNIQUE NOT NULL,
    name               TEXT NOT NULL,
    scopes             TEXT[] NOT NULL CHECK (scopes <@ ARRAY['read', 'write', 'admin']::text[] AND array_length(scopes, 1) > 0),
    allowed_categories TEXT[],                 -- NULL = todas las categorias
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX api_tokens_active_name_idx ON api_tokens (name) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_token_hash_active_idx ON api_tokens (token_hash) WHERE revoked_at IS NULL;
