-- KnowledgeOS Fase 3 - Relaciones / grafo ligero (plan_v2.md seccion 8, Fase 3)
--
-- memory_edges: aristas explicitas entre memorias, EN POSTGRES -- nada de base de
-- grafos dedicada (regla del proyecto). Vocabulario de relaciones controlado (CHECK)
-- en vez de TEXT libre, para que el grafo se mantenga consultable/consistente:
--   relates_to   - asociacion generica sin direccion causal/temporal fuerte
--   caused_by    - `from_memory` fue causado por `to_memory` (p.ej. decision -> causa)
--   part_of      - `from_memory` es parte de `to_memory` (p.ej. procedimiento -> proyecto)
--   contradicts  - `from_memory` contradice a `to_memory`
--   follows      - `from_memory` ocurrio despues de / como consecuencia de `to_memory`
--                  (p.ej. episodio -> consecuencia)
--
-- Notas de diseno:
-- - UNIQUE (from_memory, to_memory, relation): misma arista (misma direccion, misma
--   relacion) no se duplica; la API devuelve 409 si se intenta.
-- - CHECK from_memory <> to_memory: una memoria no puede enlazarse a si misma.
-- - ON DELETE CASCADE en ambas FKs: si una memoria se borra en duro (hard delete),
--   sus aristas (en cualquiera de las dos direcciones) mueren con ella -- no quedan
--   aristas huerfanas apuntando a un id que ya no existe.
-- - `superseded_by` (memories) sigue siendo la unica relacion de versionado; no se
--   materializa como edge (ver GET /memories/{id}/related, que la expone como
--   relacion virtual 'supersedes' sin escribirla aqui).

CREATE TABLE memory_edges (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_memory  UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    to_memory    UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation     TEXT NOT NULL CHECK (relation IN ('relates_to', 'caused_by', 'part_of', 'contradicts', 'follows')),
    note         TEXT,
    created_by   TEXT NOT NULL,               -- agente que creo la arista (X-Agent-Name)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT memory_edges_no_self_link CHECK (from_memory <> to_memory),
    CONSTRAINT memory_edges_unique_triple UNIQUE (from_memory, to_memory, relation)
);

CREATE INDEX memory_edges_from_idx ON memory_edges (from_memory);
CREATE INDEX memory_edges_to_idx ON memory_edges (to_memory);
