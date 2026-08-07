-- KnowledgeOS v1.0 - Autorizacion por token con scopes (plan_v2.md SS9 "Autorizacion v1.0")
--
-- api_tokens: tokens con nombre (identidad real del agente, pisa X-Agent-Name en el
-- audit log), scopes explicitos (read/write/admin) y restriccion opcional de
-- contextos permitidos (NULL = todos). El valor en claro del token NUNCA se guarda:
-- solo su SHA-256 (`token_hash`) -- ver src/knowledgeos/auth.py.
--
-- El token legado de .env (API_TOKEN) sigue funcionando aparte, como token "root"
-- implicito con todos los scopes y todos los contextos (compatibilidad hacia atras,
-- ver README "Seguridad") -- no tiene fila aqui, se compara directo contra el valor
-- de settings.api_token.
--
-- Revocacion: soft (revoked_at), nunca se borra la fila (rastro para el audit log /
-- para poder ver historicos de tokens). El indice unico parcial permite reusar un
-- `name` despues de revocar el token anterior con ese nombre.

CREATE TABLE api_tokens (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash       TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    scopes           TEXT[] NOT NULL CHECK (scopes <@ ARRAY['read', 'write', 'admin']::text[] AND array_length(scopes, 1) > 0),
    allowed_contexts TEXT[],                  -- NULL = todos los contextos
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at       TIMESTAMPTZ
);

-- Solo un token ACTIVO por nombre a la vez (permite recrear un nombre revocado).
CREATE UNIQUE INDEX api_tokens_active_name_idx ON api_tokens (name) WHERE revoked_at IS NULL;
CREATE INDEX api_tokens_token_hash_active_idx ON api_tokens (token_hash) WHERE revoked_at IS NULL;
