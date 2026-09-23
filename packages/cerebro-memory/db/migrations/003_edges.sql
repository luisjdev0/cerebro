-- KnowledgeOS Phase 3 - Relationships / lightweight graph (plan_v2.md section 8, Phase 3)
--
-- memory_edges: explicit edges between memories, IN POSTGRES -- no dedicated
-- graph database (project rule). Controlled relationship vocabulary (CHECK)
-- instead of free TEXT, so the graph stays queryable/consistent:
--   relates_to   - generic association with no strong causal/temporal direction
--   caused_by    - `from_memory` was caused by `to_memory` (e.g. decision -> cause)
--   part_of      - `from_memory` is part of `to_memory` (e.g. procedure -> project)
--   contradicts  - `from_memory` contradicts `to_memory`
--   follows      - `from_memory` occurred after / as a consequence of `to_memory`
--                  (e.g. episode -> consequence)
--
-- Design notes:
-- - UNIQUE (from_memory, to_memory, relation): the same edge (same direction, same
--   relation) is not duplicated; the API returns 409 if one is attempted.
-- - CHECK from_memory <> to_memory: a memory cannot link to itself.
-- - ON DELETE CASCADE on both FKs: if a memory is hard-deleted, its edges (in
--   either direction) die with it -- no orphaned edges are left pointing to an id
--   that no longer exists.
-- - `superseded_by` (memories) remains the only versioning relationship; it is not
--   materialized as an edge (see GET /memories/{id}/related, which exposes it as a
--   virtual 'supersedes' relationship without writing it here).

CREATE TABLE memory_edges (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_memory  UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    to_memory    UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation     TEXT NOT NULL CHECK (relation IN ('relates_to', 'caused_by', 'part_of', 'contradicts', 'follows')),
    note         TEXT,
    created_by   TEXT NOT NULL,               -- agent that created the edge (X-Agent-Name)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT memory_edges_no_self_link CHECK (from_memory <> to_memory),
    CONSTRAINT memory_edges_unique_triple UNIQUE (from_memory, to_memory, relation)
);

CREATE INDEX memory_edges_from_idx ON memory_edges (from_memory);
CREATE INDEX memory_edges_to_idx ON memory_edges (to_memory);
