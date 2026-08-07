"""Relaciones / grafo ligero (Fase 3, plan_v2.md SS8): aristas explicitas entre
memorias, EN POSTGRES -- nada de base de grafos dedicada (regla del proyecto).

`memory_edges` (db/migrations/003_edges.sql) guarda aristas dirigidas con un
vocabulario de relacion controlado (RELATION_VOCAB, reflejado en el CHECK de la
migracion). Este modulo tambien deriva, en lectura, la cadena de supersedencia ya
existente desde Fase 1 (`memories.superseded_by`) como una relacion VIRTUAL
'supersedes' -- nunca se materializa en `memory_edges`.

Tres piezas:
    - add_edge / delete_edge: CRUD de aristas explicitas.
    - get_related: vecinos a 1 salto (ambas direcciones) + cadena de supersedencia.
    - get_timeline: memorias episodic/decision ordenadas por fecha efectiva.
    - get_search_related: vecinos de los top-N resultados de una busqueda
      (GET /memories/search?expand=true), con la regla de contaminacion cruzada
      entre contextos.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

RELATION_VOCAB = ("relates_to", "caused_by", "part_of", "contradicts", "follows")

# Nombre de la relacion virtual derivada de memories.superseded_by - nunca aparece en
# memory_edges.relation (el CHECK de la migracion la excluye a proposito), pero es un
# valor valido para filtrar en get_related(relation=...).
SUPERSEDES = "supersedes"

_MEMORY_COLUMNS = """
    m.id, c.slug AS context, m.type, m.title, m.content,
    m.importance, m.confidence, m.source, m.status, m.superseded_by,
    m.occurred_at, m.created_at, m.updated_at
"""


class InvalidRelationError(ValueError):
    """Raised when `relation` is not part of RELATION_VOCAB (or, where accepted,
    the virtual `SUPERSEDES` value)."""


class MemoryNotFoundError(LookupError):
    """Raised when a referenced memory id does not exist in `memories`."""


class DuplicateEdgeError(ValueError):
    """Raised when (from_memory, to_memory, relation) already exists (unique triple)."""


class EdgeNotFoundError(LookupError):
    """Raised when an edge id does not exist, or does not touch the given memory."""


class UnknownContextError(LookupError):
    """Raised when a `context` filter slug (GET /timeline) does not exist."""


def validate_relation(relation: str, *, allow_supersedes: bool = False) -> None:
    """Pure validation, no I/O - see tests/test_graph.py."""
    allowed = RELATION_VOCAB + ((SUPERSEDES,) if allow_supersedes else ())
    if relation not in allowed:
        raise InvalidRelationError(relation)


async def _fetch_memory_row(conn: asyncpg.Connection, memory_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {_MEMORY_COLUMNS} FROM memories m JOIN contexts c ON c.id = m.context_id WHERE m.id = $1",
        memory_id,
    )


# --------------------------------------------------------------------------- edges


async def add_edge(
    pool: asyncpg.Pool,
    *,
    from_memory: UUID,
    to_memory: UUID,
    relation: str,
    note: str | None,
    created_by: str,
) -> dict[str, Any]:
    """Create an explicit edge from_memory -RELATION-> to_memory.

    Raises InvalidRelationError (bad vocab), ValueError (from == to),
    MemoryNotFoundError (either endpoint missing), or DuplicateEdgeError (409 at the
    API layer: the (from, to, relation) triple already exists).
    """
    validate_relation(relation)
    if from_memory == to_memory:
        raise ValueError("from_memory and to_memory must differ")

    async with pool.acquire() as conn:
        async with conn.transaction():
            from_exists = await conn.fetchval("SELECT 1 FROM memories WHERE id = $1", from_memory)
            if from_exists is None:
                raise MemoryNotFoundError(str(from_memory))
            to_exists = await conn.fetchval("SELECT 1 FROM memories WHERE id = $1", to_memory)
            if to_exists is None:
                raise MemoryNotFoundError(str(to_memory))

            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory_edges (from_memory, to_memory, relation, note, created_by)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id, from_memory, to_memory, relation, note, created_by, created_at
                    """,
                    from_memory,
                    to_memory,
                    relation,
                    note,
                    created_by,
                )
            except asyncpg.UniqueViolationError as exc:
                raise DuplicateEdgeError(
                    f"edge ({from_memory}, {to_memory}, {relation}) already exists"
                ) from exc

    return dict(row)


async def delete_edge(pool: asyncpg.Pool, *, memory_id: UUID, edge_id: UUID) -> dict[str, Any]:
    """Delete an edge, but only if `memory_id` is one of its two endpoints - prevents
    deleting an edge you found through an unrelated memory's id."""
    row = await pool.fetchrow(
        """
        DELETE FROM memory_edges
        WHERE id = $1 AND (from_memory = $2 OR to_memory = $2)
        RETURNING id, from_memory, to_memory, relation, note, created_by, created_at
        """,
        edge_id,
        memory_id,
    )
    if row is None:
        raise EdgeNotFoundError(str(edge_id))
    return dict(row)


# --------------------------------------------------------------------------- related


async def get_related(
    pool: asyncpg.Pool,
    *,
    memory_id: UUID,
    relation: str | None = None,
) -> list[dict[str, Any]]:
    """1-hop neighbors of `memory_id`, both directions, plus the derived supersedence
    chain (see module docstring).

    Returns a list of dicts:
        {edge_id, relation, direction, note, created_by, created_at, virtual, memory}

    `direction` is "outgoing" when `memory_id` is the source of the relation (edge's
    from_memory, or - for the virtual chain - when this memory is the newer version
    that supersedes the neighbor), "incoming" otherwise.

    `relation=None` (default) returns everything: explicit edges of any relation, plus
    the virtual 'supersedes' entries. `relation="supersedes"` returns only the virtual
    chain; any other value must be in RELATION_VOCAB and returns only explicit edges
    of that relation (no virtual entries).

    Raises MemoryNotFoundError if `memory_id` does not exist, InvalidRelationError if
    `relation` is neither None, RELATION_VOCAB nor "supersedes".
    """
    if relation is not None:
        validate_relation(relation, allow_supersedes=True)

    async with pool.acquire() as conn:
        self_row = await _fetch_memory_row(conn, memory_id)
        if self_row is None:
            raise MemoryNotFoundError(str(memory_id))

        results: list[dict[str, Any]] = []

        if relation != SUPERSEDES:
            edge_rows = await conn.fetch(
                """
                SELECT id AS edge_id, from_memory, to_memory, relation, note, created_by, created_at
                FROM memory_edges
                WHERE (from_memory = $1 OR to_memory = $1) AND ($2::text IS NULL OR relation = $2)
                ORDER BY created_at
                """,
                memory_id,
                relation,
            )
            for r in edge_rows:
                neighbor_id = r["to_memory"] if r["from_memory"] == memory_id else r["from_memory"]
                neighbor_row = await _fetch_memory_row(conn, neighbor_id)
                if neighbor_row is None:
                    continue  # shouldn't happen (FK), guard against races anyway
                direction = "outgoing" if r["from_memory"] == memory_id else "incoming"
                results.append(
                    {
                        "edge_id": r["edge_id"],
                        "relation": r["relation"],
                        "direction": direction,
                        "note": r["note"],
                        "created_by": r["created_by"],
                        "created_at": r["created_at"],
                        "virtual": False,
                        "memory": dict(neighbor_row),
                    }
                )

        if relation is None or relation == SUPERSEDES:
            if self_row["superseded_by"] is not None:
                newer = await _fetch_memory_row(conn, self_row["superseded_by"])
                if newer is not None:
                    results.append(
                        {
                            "edge_id": None,
                            "relation": SUPERSEDES,
                            "direction": "incoming",  # neighbor (newer) supersedes this one
                            "note": None,
                            "created_by": None,
                            "created_at": None,
                            "virtual": True,
                            "memory": dict(newer),
                        }
                    )
            older_rows = await conn.fetch(
                f"SELECT {_MEMORY_COLUMNS} FROM memories m JOIN contexts c ON c.id = m.context_id "
                "WHERE m.superseded_by = $1",
                memory_id,
            )
            for older in older_rows:
                results.append(
                    {
                        "edge_id": None,
                        "relation": SUPERSEDES,
                        "direction": "outgoing",  # this memory supersedes the (older) neighbor
                        "note": None,
                        "created_by": None,
                        "created_at": None,
                        "virtual": True,
                        "memory": dict(older),
                    }
                )

    return results


# --------------------------------------------------------------------------- timeline


async def get_timeline(
    pool: asyncpg.Pool,
    *,
    context: str | None,
    from_date: datetime | None,
    to_date: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Episodic (and decision) memories ordered by "fecha efectiva"
    (occurred_at, falling back to created_at when occurred_at is NULL), most recent
    first - built for "que paso en X las ultimas semanas". Only `status='active'`.

    Raises UnknownContextError if `context` is given and does not match any context.
    """
    async with pool.acquire() as conn:
        if context is not None:
            exists = await conn.fetchval("SELECT 1 FROM contexts WHERE slug = $1", context)
            if exists is None:
                raise UnknownContextError(context)

        filters = ["m.type IN ('episodic', 'decision')", "m.status = 'active'"]
        params: list[Any] = []
        if context is not None:
            params.append(context)
            filters.append(f"c.slug = ${len(params)}")
        if from_date is not None:
            params.append(from_date)
            filters.append(f"COALESCE(m.occurred_at, m.created_at) >= ${len(params)}")
        if to_date is not None:
            params.append(to_date)
            filters.append(f"COALESCE(m.occurred_at, m.created_at) <= ${len(params)}")
        where_clause = " AND ".join(filters)
        params.append(limit)

        rows = await conn.fetch(
            f"""
            SELECT {_MEMORY_COLUMNS}, COALESCE(m.occurred_at, m.created_at) AS effective_date
            FROM memories m JOIN contexts c ON c.id = m.context_id
            WHERE {where_clause}
            ORDER BY effective_date DESC
            LIMIT ${len(params)}
            """,
            *params,
        )

    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- search expand


async def get_search_related(
    pool: asyncpg.Pool,
    *,
    top_results: list[dict[str, Any]],
    decided_context: str | None,
    max_neighbors: int = 5,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Direct (1-hop) neighbors of the top `top_n` search results, for
    GET /memories/search?expand=true.

    Deduplicated by neighbor memory id (and never repeats a memory already present in
    `top_results`), capped at `max_neighbors` total, only `status='active'` neighbors.

    Cross-context rule: if `decided_context` is set (the Context Engine/explicit scope
    already picked a single context for `top_results`), a neighbor from a DIFFERENT
    context is included only when reached via an EXPLICIT edge (`virtual=False`) -
    explicit edges are intentional bridges the user/agent created on purpose, not
    contamination. The derived supersedence chain is always same-context by
    construction (an update() never changes context_id), but it is excluded from
    cross-context inclusion anyway as a defensive measure. Each returned entry gets a
    `cross_context: bool` field.
    """
    result_ids = {str(r["id"]) for r in top_results}
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for res in top_results[:top_n]:
        if len(out) >= max_neighbors:
            break
        try:
            neighbors = await get_related(pool, memory_id=res["id"], relation=None)
        except MemoryNotFoundError:
            continue

        for item in neighbors:
            if len(out) >= max_neighbors:
                break
            neighbor = item["memory"]
            if neighbor.get("status") != "active":
                continue
            neighbor_id = str(neighbor["id"])
            if neighbor_id in seen or neighbor_id in result_ids:
                continue

            cross_context = decided_context is not None and neighbor.get("context") != decided_context
            if cross_context and item["virtual"]:
                continue  # virtual supersedence never crosses context in practice; be safe anyway

            seen.add(neighbor_id)
            entry = dict(item)
            entry["cross_context"] = cross_context
            out.append(entry)

    return out
