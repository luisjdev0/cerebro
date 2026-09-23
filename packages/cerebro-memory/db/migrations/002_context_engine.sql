-- KnowledgeOS Phase 2 - Context Engine (plan_v2.md section 7)
--
-- disambiguation_log: records every scoping decision made by the Context Engine,
-- both the auto-resolved ones (resolved_by='auto') and the ones left pending for
-- the agent/user to choose (chosen_context NULL until resolved via
-- POST /disambiguations/{id}/resolve).
--
-- context_preferences: weight learned per (context_id, term). Fed by the
-- resolutions: when an ambiguity is resolved, the significant tokens of the query
-- (normalized, ES stopwords removed) add weight toward the chosen context. The Context
-- Engine uses these weights as an additional boost in Task 2's scoring.

CREATE TABLE disambiguation_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query           TEXT NOT NULL,
    candidates      JSONB NOT NULL,           -- [{slug, score}, ...]
    chosen_context  TEXT,                     -- chosen slug; NULL until resolved
    resolved_by     TEXT,                     -- 'auto' | 'agent' | 'user'; NULL until resolved
    agent           TEXT,                     -- identity of the client that resolved it (X-Agent-Name)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX disambiguation_log_created_at_idx ON disambiguation_log (created_at);
CREATE INDEX disambiguation_log_resolved_by_idx ON disambiguation_log (resolved_by);

CREATE TABLE context_preferences (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_id  UUID NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    term        TEXT NOT NULL,                -- normalized token (no accents, no stopwords)
    weight      REAL NOT NULL DEFAULT 1.0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (context_id, term)
);

CREATE INDEX context_preferences_term_idx ON context_preferences (term);
