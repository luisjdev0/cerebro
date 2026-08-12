-- cerebro-docs v1 - schema inicial (ecosistema-cerebro.md SS6).
--
-- Todo vive en el schema `cerebro_docs`, nunca en `public` (compartido con el schema
-- hermano `cerebro_memory` en la misma instancia Postgres, SS8/SS14: nunca acceso
-- cruzado de esquemas entre modulos). El runner (db.py) ya ejecuto
-- `CREATE SCHEMA IF NOT EXISTS cerebro_docs` y adquirio esta conexion con
-- search_path = "cerebro_docs, public" ANTES de correr este archivo, asi que las
-- sentencias de aqui no califican el schema explicitamente: en una instalacion
-- fresca, `cerebro_docs` ya es el primer schema existente del search_path, y todo
-- objeto sin calificar aterriza ahi en el mismo arranque.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() (idempotente: si
                                           -- cerebro-memory ya la instalo en `public`
                                           -- para la misma instancia, esto es un no-op)

-- Categorias, como los `contexts` de cerebro-memory: tabla formal con creacion
-- explicita y redistribuible (renombrar una categoria no debe tocar sus documentos -
-- ver documents.category_id como FK, nunca texto copiado).
CREATE TABLE categories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Documentos Markdown completos, SIN truncar ni destilar (a diferencia de las
-- memorias de cerebro-memory). `search_vector` es una columna generada (STORED) para
-- que el indice GIN de full-text quede siempre sincronizado sin trigger propio.
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

-- Snapshot completo del documento justo ANTES de cada update/patch (red de seguridad
-- contra sobrescritura accidental, sin endpoint de restore en v1 - ver
-- ecosistema-cerebro.md SS12). ON DELETE CASCADE: borrar un documento (o, en cascada,
-- su categoria con ?force=true) se lleva su historial completo, nunca deja huerfanos.
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

-- Tokens con nombre y scopes (read/write/admin), espejo de
-- cerebro-memory/db/migrations/004_api_tokens.sql pero con `allowed_categories` en
-- vez de `allowed_contexts`. El token root (.env API_TOKEN) sigue sin fila aqui - se
-- compara directo en auth.py.
CREATE TABLE api_tokens (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash        TEXT UNIQUE NOT NULL,
    name              TEXT NOT NULL,
    scopes            TEXT[] NOT NULL CHECK (scopes <@ ARRAY['read', 'write', 'admin']::text[] AND array_length(scopes, 1) > 0),
    allowed_categories TEXT[],                 -- NULL = todas las categorias
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at        TIMESTAMPTZ
);

CREATE UNIQUE INDEX api_tokens_active_name_idx ON api_tokens (name) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_token_hash_active_idx ON api_tokens (token_hash) WHERE revoked_at IS NULL;
