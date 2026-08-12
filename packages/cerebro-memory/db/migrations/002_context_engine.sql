-- KnowledgeOS Fase 2 - Context Engine (plan_v2.md seccion 7)
--
-- disambiguation_log: registra cada decision de scoping tomada por el Context Engine,
-- tanto las auto-resueltas (resolved_by='auto') como las que quedaron pendientes de que
-- el agente/usuario elija (chosen_context NULL hasta resolverse via
-- POST /disambiguations/{id}/resolve).
--
-- context_preferences: peso aprendido por (context_id, term). Se alimenta de las
-- resoluciones: al resolverse una ambiguedad, los tokens significativos de la query
-- (normalizados, sin stopwords ES) suman peso hacia el contexto elegido. El Context
-- Engine usa estos pesos como boost adicional en el scoring de la Tarea 2.

CREATE TABLE disambiguation_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query           TEXT NOT NULL,
    candidates      JSONB NOT NULL,           -- [{slug, score}, ...]
    chosen_context  TEXT,                     -- slug elegido; NULL hasta resolverse
    resolved_by     TEXT,                     -- 'auto' | 'agent' | 'user'; NULL hasta resolverse
    agent           TEXT,                     -- identidad del cliente que resolvio (X-Agent-Name)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX disambiguation_log_created_at_idx ON disambiguation_log (created_at);
CREATE INDEX disambiguation_log_resolved_by_idx ON disambiguation_log (resolved_by);

CREATE TABLE context_preferences (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_id  UUID NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    term        TEXT NOT NULL,                -- token normalizado (sin acentos, sin stopwords)
    weight      REAL NOT NULL DEFAULT 1.0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (context_id, term)
);

CREATE INDEX context_preferences_term_idx ON context_preferences (term);
