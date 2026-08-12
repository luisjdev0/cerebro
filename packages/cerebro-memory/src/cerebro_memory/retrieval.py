"""Hybrid retrieval: vector (HNSW/cosine) + full-text (spanish), fused with RRF.

`reciprocal_rank_fusion` / `fuse_rankings` are pure functions (no I/O) so they are
unit-testable without a database - see tests/test_rrf.py. `hybrid_search` wires them
to Postgres.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from cerebro_memory.embeddings import EmbeddingProvider

_COLUMNS = """
    m.id, m.context_id, c.slug AS context, m.type, m.title, m.content,
    m.importance, m.confidence, m.source, m.status, m.superseded_by,
    m.occurred_at, m.created_at, m.updated_at
"""


class UnknownContextError(LookupError):
    """Raised when a `context` filter slug does not match any row in `contexts`."""


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Combine N ranked id lists (best result first) into one score per id.

    RRF score(id) = sum over rankings containing id of 1 / (k + rank), rank 1-based.
    Standard k=60 per Cormack et al. An id missing from a ranking simply contributes 0
    from it - it does not need to appear in every source to be scored.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores


def fuse_rankings(rankings: list[list[str]], k: int = 60, limit: int | None = None) -> list[str]:
    """Convenience wrapper: fuse and return ids sorted best-first."""
    scores = reciprocal_rank_fusion(rankings, k=k)
    ordered = sorted(scores, key=lambda item_id: scores[item_id], reverse=True)
    return ordered[:limit] if limit is not None else ordered


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.10f}" for v in vec) + "]"


async def hybrid_search(
    pool: asyncpg.Pool,
    embedding_provider: EmbeddingProvider,
    *,
    query: str,
    context_slug: str | None = None,
    type_: str | None = None,
    limit: int = 5,
    include_superseded: bool = False,
    candidate_pool: int = 50,
    rrf_k: int = 60,
    allowed_contexts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run vector + full-text search under the given filters and fuse with RRF.

    Only `status='active'` rows are considered unless `include_superseded=True`, in
    which case `superseded` rows are included too. `archived` (soft-deleted) rows are
    never returned here.

    `allowed_contexts` (plan_v2.md SS9, token authorization): when `context_slug` is
    not given and `allowed_contexts` is not None, results are narrowed to that set of
    slugs -- the token-scoping equivalent of an explicit context filter, used when a
    restricted token searches without naming a context (`scope=all`, or the
    unfiltered preliminary retrieval inside the Context Engine). Ignored when
    `context_slug` is given (an explicit context always wins; callers are expected to
    have already checked it against `allowed_contexts` themselves and rejected it
    with 403 if not allowed - see `cerebro_memory.auth.Principal.context_allowed`).
    """
    statuses = ["active", "superseded"] if include_superseded else ["active"]

    async with pool.acquire() as conn:
        context_id = None
        if context_slug:
            context_id = await conn.fetchval("SELECT id FROM contexts WHERE slug = $1", context_slug)
            if context_id is None:
                raise UnknownContextError(context_slug)

        filters = ["m.status = ANY($1::text[])"]
        params: list[Any] = [statuses]
        if context_id is not None:
            params.append(context_id)
            filters.append(f"m.context_id = ${len(params)}")
        elif allowed_contexts is not None:
            params.append(list(allowed_contexts))
            filters.append(f"c.slug = ANY(${len(params)}::text[])")
        if type_:
            params.append(type_)
            filters.append(f"m.type = ${len(params)}")
        where_clause = " AND ".join(filters)

        query_vector = await embedding_provider.embed_query(query)
        vec_literal = _vector_literal(query_vector)

        vec_sql = f"""
            SELECT {_COLUMNS}
            FROM memories m JOIN contexts c ON c.id = m.context_id
            WHERE {where_clause}
            ORDER BY m.embedding <=> ${len(params) + 1}::vector
            LIMIT ${len(params) + 2}
        """
        vector_rows = await conn.fetch(vec_sql, *params, vec_literal, candidate_pool)

        fts_sql = f"""
            SELECT {_COLUMNS}
            FROM memories m JOIN contexts c ON c.id = m.context_id
            WHERE {where_clause}
              AND to_tsvector('spanish', m.title || ' ' || m.content)
                  @@ plainto_tsquery('spanish', ${len(params) + 1})
            ORDER BY ts_rank(
                to_tsvector('spanish', m.title || ' ' || m.content),
                plainto_tsquery('spanish', ${len(params) + 1})
            ) DESC
            LIMIT ${len(params) + 2}
        """
        fts_rows = await conn.fetch(fts_sql, *params, query, candidate_pool)

    rows_by_id: dict[str, asyncpg.Record] = {str(r["id"]): r for r in vector_rows}
    for row in fts_rows:
        rows_by_id.setdefault(str(row["id"]), row)

    vector_ranking = [str(r["id"]) for r in vector_rows]
    fts_ranking = [str(r["id"]) for r in fts_rows]

    scores = reciprocal_rank_fusion([vector_ranking, fts_ranking], k=rrf_k)
    ordered_ids = sorted(rows_by_id, key=lambda i: scores.get(i, 0.0), reverse=True)[:limit]

    results = []
    for item_id in ordered_ids:
        row = dict(rows_by_id[item_id])
        row["score"] = scores.get(item_id, 0.0)
        results.append(row)
    return results
