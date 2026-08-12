"""Context Engine (Fase 2, plan_v2.md SS7): decide search *scope* before the final
retrieval runs.

No LLM calls happen here - the whole point of Fase 2 is that the "auxiliary model" is
the model already in the conversation (see plan_v2.md SS7 point 1). This module only
does cheap, deterministic scoring over a preliminary unfiltered retrieval:

    1. Run hybrid_search with no context filter, top ~20 (candidate_pool).
    2. Score each context = sum of RRF scores of its memories in that top-N
       + a boost from `context_preferences` (learned term -> context weights)
       + a flat boost if the query explicitly names the context (slug or name).
    3. If the best context dominates (normalized score >= DOMINANCE_THRESHOLD *and*
       margin over the runner-up >= MARGIN_THRESHOLD) -> auto-scope: the caller filters
       the final retrieval to that context. Logged to `disambiguation_log` with
       resolved_by='auto'.
    4. Otherwise -> ambiguous: return the top 2-4 candidate contexts (slug, name,
       description, normalized score) *plus* a few top results per candidate, so the
       calling agent can decide with evidence instead of getting memories blindly
       mixed together. Logged unresolved; the caller gets a `disambiguation_id` to
       pass to `resolve_disambiguation` once a choice is made.

`resolve_disambiguation` is the write side of step 4: it records the choice and grows
`context_preferences` so the same/similar queries route faster (and more decisively)
next time - the "learning" half of the Context Engine.
"""

from __future__ import annotations

import abc
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg
import httpx

from cerebro_memory.config import Settings
from cerebro_memory.embeddings import EmbeddingProvider
from cerebro_memory.retrieval import UnknownContextError, hybrid_search

logger = logging.getLogger("cerebro_memory.context_engine")

# ----------------------------------------------------------------------------- tokens

# Same spirit as evals/harness/adapters/naive_keyword.py's STOPWORDS_ES (kept as an
# independent copy on purpose: that file lives outside the installed package and is
# explicitly the "naive, no dependencies" baseline; this is the real product's list
# and is free to diverge/grow, e.g. with domain-specific stopwords later).
STOPWORDS_ES = {
    "a", "al", "algo", "algunas", "algunos", "ante", "antes", "como", "con",
    "contra", "cual", "cuales", "cuando", "cuanto", "cuanta", "cuantos",
    "cuantas", "de", "del", "desde", "donde", "dos", "el", "ella", "ellas",
    "ello", "ellos", "en", "entre", "era", "es", "esa", "esas", "ese", "eso",
    "esos", "esta", "estas", "este", "esto", "estos", "hay", "la", "las",
    "le", "les", "lo", "los", "mas", "me", "mi", "mis", "mucho", "muchos",
    "muy", "nada", "ni", "no", "nos", "nuestra", "nuestro", "nuestros",
    "o", "os", "otra", "otras", "otro", "otros", "para", "pero", "poco",
    "por", "porque", "que", "quien", "quienes", "se", "sin", "sobre", "su",
    "sus", "tambien", "tan", "tanto", "te", "ti", "tiene", "tu", "tus", "un",
    "una", "uno", "unos", "y", "ya", "yo",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ACCENTS = str.maketrans("áéíóúñü", "aeiounu")


def normalize_tokens(text: str) -> list[str]:
    """Lowercase, strip common accents, tokenize, drop stopwords and 1-2 char noise.

    Deterministic and dependency-free (no NLP library) - good enough for the term
    matching this module needs (context_preferences lookups, explicit-mention
    detection), not intended as a general-purpose tokenizer.
    """
    text = text.lower().translate(_ACCENTS)
    tokens = _TOKEN_RE.findall(text)
    return [t for t in tokens if t not in STOPWORDS_ES and len(t) > 2]


# ----------------------------------------------------------------------------- types


@dataclass
class ContextCandidate:
    slug: str
    name: str
    description: str | None
    score: float  # normalized 0..1 (share of total score across scored contexts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "score": round(self.score, 4),
        }


# ----------------------------------------------------------------- Fase 4: resolver
# Punto de enchufe para un clasificador local opcional (plan_v2.md SS8, Fase 4). El
# plan es honesto: hoy NO existe el dataset (~500 desambiguaciones reales) para
# entrenar/evaluar nada, asi que esto es solo la INFRAESTRUCTURA -- el resolver por
# defecto (NullResolver) no cambia el comportamiento actual en absoluto: sigue
# devolviendo la ambiguedad al agente que llama (Fase 2, SS7). Ver README "Clasificador
# local opcional" para la condicion de activacion en serio.


class AmbiguityResolver(abc.ABC):
    """Interfaz para resolver una ambiguedad de contexto sin devolverla al agente que
    llama. `decide_scope` la invoca SOLO cuando el scoring determinista ya decidio que
    el caso es ambiguo (nunca reemplaza el scoring barato de la Fase 2, solo actua
    despues de que este ya fallo en decidir)."""

    @abc.abstractmethod
    async def resolve(self, query: str, candidates: list[ContextCandidate]) -> str | None:
        """Devuelve el slug del candidato elegido, o None si no hay respuesta
        confiable (en cuyo caso el flujo cae de vuelta a `mode == "ambiguous"`, sin
        ningun cambio respecto a hoy)."""


class NullResolver(AmbiguityResolver):
    """Default. Nunca resuelve nada -> el flujo actual (ambiguedad al agente) queda
    exactamente igual. Activar cualquier otro resolver es opt-in explicito via
    CONTEXT_ENGINE_RESOLVER."""

    async def resolve(self, query: str, candidates: list[ContextCandidate]) -> str | None:
        return None


class OllamaResolver(AmbiguityResolver):
    """OPCIONAL: clasificador local vía Ollama (`/api/generate`), activado solo con
    `CONTEXT_ENGINE_RESOLVER=ollama` (+ `OLLAMA_URL`, `OLLAMA_MODEL`).

    No es un fine-tune (eso es exactamente lo que la Fase 4 condiciona a tener ~500
    ejemplos reales, ver plan_v2.md SS8) -- es un modelo generico con un prompt corto
    de clasificacion. Por eso nunca debe tratarse como mas confiable que devolver la
    ambiguedad al agente: cualquier fallo (Ollama caido, timeout, respuesta no
    parseable, slug que no está entre los candidatos) cae en silencio a `None`, que es
    exactamente lo que hace NullResolver -- esto JAMAS debe poder romper una búsqueda.
    """

    def __init__(self, url: str, model: str, timeout: float = 2.0) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def resolve(self, query: str, candidates: list[ContextCandidate]) -> str | None:
        if not candidates:
            return None
        prompt = self._build_prompt(query, candidates)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._url}/api/generate",
                    json={"model": self._model, "prompt": prompt, "stream": False},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:  # noqa: BLE001 - nunca romper la busqueda por un modelo local
            logger.debug("OllamaResolver: fallo la llamada a Ollama, fallback a None", exc_info=True)
            return None

        raw_response = data.get("response") if isinstance(data, dict) else None
        if not isinstance(raw_response, str):
            return None
        return self._parse_slug(raw_response, candidates)

    @staticmethod
    def _build_prompt(query: str, candidates: list[ContextCandidate]) -> str:
        lines = [
            "Elige a que contexto pertenece la consulta de un usuario. Responde SOLO "
            "con el slug exacto de un contexto de la lista, sin explicacion ni "
            "puntuacion. Si ninguno encaja con confianza, responde exactamente: none",
            "",
            f'Consulta: "{query}"',
            "",
            "Contextos posibles:",
        ]
        for c in candidates:
            lines.append(f"- {c.slug}: {c.description or c.name}")
        lines.append("")
        lines.append("Slug elegido:")
        return "\n".join(lines)

    @staticmethod
    def _parse_slug(raw: str, candidates: list[ContextCandidate]) -> str | None:
        cleaned = raw.strip().strip(".").strip('"').strip("'").lower()
        if not cleaned or cleaned == "none":
            return None
        by_slug = {c.slug.lower(): c.slug for c in candidates}
        if cleaned in by_slug:
            return by_slug[cleaned]
        # tolera una respuesta corta que solo menciona el slug dentro de mas texto
        for slug_lower, slug in by_slug.items():
            if slug_lower in cleaned:
                return slug
        return None


def build_ambiguity_resolver(settings: Settings) -> AmbiguityResolver:
    """Factory leída por `decide_scope`. `settings.context_engine_resolver` es
    `"none"` por defecto (NullResolver) -- ver README, "Clasificador local opcional"."""
    kind = (settings.context_engine_resolver or "none").strip().lower()
    if kind == "ollama":
        return OllamaResolver(url=settings.ollama_url, model=settings.ollama_model)
    return NullResolver()


@dataclass
class ScopeDecision:
    mode: str  # "auto" | "ambiguous"
    context: str | None = None
    candidates: list[ContextCandidate] | None = None
    disambiguation_id: str | None = None
    results_by_candidate: dict[str, list[dict[str, Any]]] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode}
        if self.context is not None:
            out["context"] = self.context
        if self.candidates is not None:
            out["candidates"] = [c.to_dict() for c in self.candidates]
        if self.disambiguation_id is not None:
            out["disambiguation_id"] = self.disambiguation_id
        return out


@dataclass
class _ContextRow:
    id: UUID
    slug: str
    name: str
    description: str | None


@dataclass
class _ScoredContext:
    ctx: _ContextRow
    base_score: float = 0.0  # sum of RRF scores from the preliminary retrieval
    preference_score: float = 0.0  # boost from context_preferences term matches
    mention_score: float = 0.0  # flat boost if slug/name explicitly named
    matched_terms: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.base_score + self.preference_score + self.mention_score


# ----------------------------------------------------------------------------- scoring
# Pure-ish function (only reads already-fetched data) so it is unit-testable without a
# database - see tests/test_context_engine.py.


def score_contexts(
    *,
    preliminary_results: list[dict[str, Any]],
    contexts: list[_ContextRow],
    query: str,
    preferences: dict[tuple[str, str], float],  # (context_slug, term) -> weight
    preference_boost_per_weight: float,
    mention_boost: float,
) -> list[_ScoredContext]:
    """Score every context that has *some* signal (RRF hits, matched preferences, or
    an explicit mention), sorted best-first. Contexts with zero signal are omitted.
    """
    query_tokens = set(normalize_tokens(query))

    scored: dict[str, _ScoredContext] = {
        ctx.slug: _ScoredContext(ctx=ctx) for ctx in contexts
    }

    for row in preliminary_results:
        slug = row.get("context")
        if slug in scored:
            scored[slug].base_score += float(row.get("score", 0.0))

    for ctx in contexts:
        for term in query_tokens:
            weight = preferences.get((ctx.slug, term))
            if weight:
                scored[ctx.slug].preference_score += weight * preference_boost_per_weight
                scored[ctx.slug].matched_terms.append(term)

    for ctx in contexts:
        slug_tokens = set(normalize_tokens(ctx.slug.replace("-", " ")))
        name_tokens = set(normalize_tokens(ctx.name))
        mentioned_tokens = (slug_tokens | name_tokens) & query_tokens
        if mentioned_tokens:
            scored[ctx.slug].mention_score += mention_boost

    return sorted(
        (sc for sc in scored.values() if sc.total > 0),
        key=lambda sc: sc.total,
        reverse=True,
    )


@dataclass
class ThresholdDecision:
    mode: str  # "auto" | "ambiguous"
    top_slug: str | None
    normalized: list[tuple[str, float]]  # (slug, normalized_score) best-first


def decide_from_scores(
    scored: list[_ScoredContext],
    *,
    dominance_threshold: float,
    margin_threshold: float,
) -> ThresholdDecision:
    """Pure threshold logic, split out from decide_scope() for direct unit testing."""
    if not scored:
        return ThresholdDecision(mode="auto", top_slug=None, normalized=[])

    total = sum(sc.total for sc in scored)
    normalized = [(sc.ctx.slug, sc.total / total) for sc in scored] if total > 0 else [
        (sc.ctx.slug, 0.0) for sc in scored
    ]

    top_slug, top_score = normalized[0]
    second_score = normalized[1][1] if len(normalized) > 1 else 0.0
    margin = top_score - second_score

    dominant = top_score >= dominance_threshold and margin >= margin_threshold
    return ThresholdDecision(
        mode="auto" if dominant else "ambiguous",
        top_slug=top_slug if dominant else None,
        normalized=normalized,
    )


# ----------------------------------------------------------------------------- I/O


async def _fetch_contexts(pool: asyncpg.Pool) -> list[_ContextRow]:
    rows = await pool.fetch("SELECT id, slug, name, description FROM contexts ORDER BY slug")
    return [_ContextRow(id=r["id"], slug=r["slug"], name=r["name"], description=r["description"]) for r in rows]


async def _fetch_preferences(
    pool: asyncpg.Pool, query_tokens: set[str]
) -> dict[tuple[str, str], float]:
    """Only fetches rows whose term matches a token actually present in this query -
    cheap even with many learned preferences."""
    if not query_tokens:
        return {}
    rows = await pool.fetch(
        """
        SELECT c.slug AS context_slug, cp.term, cp.weight
        FROM context_preferences cp
        JOIN contexts c ON c.id = cp.context_id
        WHERE cp.term = ANY($1::text[])
        """,
        list(query_tokens),
    )
    return {(r["context_slug"], r["term"]): float(r["weight"]) for r in rows}


async def decide_scope(
    pool: asyncpg.Pool,
    embedding_provider: EmbeddingProvider,
    *,
    query: str,
    type_: str | None,
    include_superseded: bool,
    settings: Settings,
    agent: str,
    limit: int,
    resolver: AmbiguityResolver | None = None,
    allowed_contexts: list[str] | None = None,
) -> ScopeDecision:
    """Full decision: preliminary retrieval -> score -> threshold -> log -> (ambiguous:
    fetch per-candidate evidence, then optionally hand off to `resolver` - Fase 4,
    OFF by default). Writes one row to `disambiguation_log` always.

    `resolver` defaults to `build_ambiguity_resolver(settings)` when omitted (mainly
    so callers/tests can inject a fake one directly).

    `allowed_contexts` (plan_v2.md SS9, token authorization): when not None, contexts
    outside this set are dropped BEFORE scoring - they can never become the auto-scope
    winner, never appear as an "ambiguous" candidate, and never leak their name or
    description to a restricted token. The preliminary retrieval is also narrowed at
    the SQL level (`hybrid_search(allowed_contexts=...)`) so disallowed-context rows
    are not even fetched, let alone scored.
    """
    preliminary = await hybrid_search(
        pool,
        embedding_provider,
        query=query,
        context_slug=None,
        type_=type_,
        limit=settings.context_engine_candidate_pool,
        include_superseded=include_superseded,
        allowed_contexts=allowed_contexts,
    )
    contexts = await _fetch_contexts(pool)
    if allowed_contexts is not None:
        allowed_set = set(allowed_contexts)
        contexts = [c for c in contexts if c.slug in allowed_set]
    query_tokens = set(normalize_tokens(query))
    preferences = await _fetch_preferences(pool, query_tokens)

    scored = score_contexts(
        preliminary_results=preliminary,
        contexts=contexts,
        query=query,
        preferences=preferences,
        preference_boost_per_weight=settings.context_engine_preference_boost_per_weight,
        mention_boost=settings.context_engine_mention_boost,
    )

    decision = decide_from_scores(
        scored,
        dominance_threshold=settings.context_engine_dominance_threshold,
        margin_threshold=settings.context_engine_margin_threshold,
    )

    by_slug = {sc.ctx.slug: sc for sc in scored}
    normalized_map = dict(decision.normalized)

    if decision.mode == "auto" and decision.top_slug is not None:
        candidates_json = [
            {"slug": slug, "score": round(score, 4)} for slug, score in decision.normalized
        ]
        disambiguation_id = await _log_disambiguation(
            pool,
            query=query,
            candidates=candidates_json,
            chosen_context=decision.top_slug,
            resolved_by="auto",
            agent=agent,
        )
        return ScopeDecision(mode="auto", context=decision.top_slug, disambiguation_id=disambiguation_id)

    if decision.mode == "auto" and decision.top_slug is None:
        # No signal at all (empty corpus / nothing matched anything) - nothing to
        # scope to; let the caller run an unfiltered (empty-ish) search. Not worth
        # logging - there was no real ambiguity to resolve, just no data.
        return ScopeDecision(mode="auto", context=None)

    # Ambiguous: pick top N candidates (2..4), fetch a few results per candidate.
    n_min = settings.context_engine_candidates_min
    n_max = settings.context_engine_candidates_max
    top_slugs = [slug for slug, _ in decision.normalized[: max(n_min, min(n_max, len(decision.normalized)))]]

    candidates = [
        ContextCandidate(
            slug=by_slug[slug].ctx.slug,
            name=by_slug[slug].ctx.name,
            description=by_slug[slug].ctx.description,
            score=normalized_map.get(slug, 0.0),
        )
        for slug in top_slugs
    ]

    results_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for slug in top_slugs:
        try:
            per_ctx = await hybrid_search(
                pool,
                embedding_provider,
                query=query,
                context_slug=slug,
                type_=type_,
                limit=settings.context_engine_results_per_candidate,
                include_superseded=include_superseded,
            )
        except UnknownContextError:
            per_ctx = []
        results_by_candidate[slug] = per_ctx

    candidates_json = [{"slug": c.slug, "score": round(c.score, 4)} for c in candidates]
    disambiguation_id = await _log_disambiguation(
        pool,
        query=query,
        candidates=candidates_json,
        chosen_context=None,
        resolved_by=None,
        agent=agent,
    )

    # Fase 4 (OFF por defecto): solo se llega aqui si el scoring determinista de la
    # Fase 2 ya decidio que es ambiguo. resolver es NullResolver a menos que
    # CONTEXT_ENGINE_RESOLVER=ollama este seteado -- en ese caso siempre devuelve None
    # y este bloque es un no-op exacto (mismo comportamiento que sin esta tarea).
    resolver = resolver if resolver is not None else build_ambiguity_resolver(settings)
    if not isinstance(resolver, NullResolver):
        try:
            resolved_slug = await resolver.resolve(query, candidates)
        except Exception:  # noqa: BLE001 - un resolver roto nunca debe tumbar la busqueda
            logger.warning("ambiguity resolver raised, falling back to ambiguous", exc_info=True)
            resolved_slug = None

        if resolved_slug is not None and resolved_slug in top_slugs:
            resolved = await resolve_disambiguation(
                pool,
                disambiguation_id=disambiguation_id,
                context_slug=resolved_slug,
                resolved_by="local_model",
                agent=agent,
            )
            return ScopeDecision(
                mode="auto",
                context=resolved["chosen_context"],
                disambiguation_id=disambiguation_id,
            )

    return ScopeDecision(
        mode="ambiguous",
        candidates=candidates,
        disambiguation_id=disambiguation_id,
        results_by_candidate=results_by_candidate,
    )


async def _log_disambiguation(
    pool: asyncpg.Pool,
    *,
    query: str,
    candidates: list[dict[str, Any]],
    chosen_context: str | None,
    resolved_by: str | None,
    agent: str | None,
) -> str:
    resolved_at_expr = "now()" if resolved_by is not None else "NULL"
    row = await pool.fetchrow(
        f"""
        INSERT INTO disambiguation_log (query, candidates, chosen_context, resolved_by, agent, resolved_at)
        VALUES ($1, $2::jsonb, $3, $4, $5, {resolved_at_expr})
        RETURNING id
        """,
        query,
        json.dumps(candidates),
        chosen_context,
        resolved_by,
        agent,
    )
    return str(row["id"])


class DisambiguationNotFoundError(LookupError):
    """Raised when a disambiguation_id passed to resolve_disambiguation does not exist."""


class DisambiguationAlreadyResolvedError(ValueError):
    """Raised when trying to resolve a disambiguation_log row that already has a choice."""


async def resolve_disambiguation(
    pool: asyncpg.Pool,
    *,
    disambiguation_id: str,
    context_slug: str,
    resolved_by: str,
    agent: str,
    preference_weight_increment: float = 1.0,
) -> dict[str, Any]:
    """Resolve a pending disambiguation: record the choice, and grow
    `context_preferences` so the same significant query tokens push future scoring
    towards this context (the "learning" half of the Context Engine, plan_v2 SS7.2).
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, query, chosen_context FROM disambiguation_log WHERE id = $1::uuid FOR UPDATE",
                disambiguation_id,
            )
            if row is None:
                raise DisambiguationNotFoundError(disambiguation_id)
            if row["chosen_context"] is not None:
                raise DisambiguationAlreadyResolvedError(disambiguation_id)

            context_id = await conn.fetchval("SELECT id FROM contexts WHERE slug = $1", context_slug)
            if context_id is None:
                raise UnknownContextError(context_slug)

            await conn.execute(
                """
                UPDATE disambiguation_log
                SET chosen_context = $1, resolved_by = $2, agent = $3, resolved_at = now()
                WHERE id = $4::uuid
                """,
                context_slug,
                resolved_by,
                agent,
                disambiguation_id,
            )

            tokens = set(normalize_tokens(row["query"]))
            for term in tokens:
                await conn.execute(
                    """
                    INSERT INTO context_preferences (context_id, term, weight)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (context_id, term)
                    DO UPDATE SET weight = context_preferences.weight + $3, updated_at = now()
                    """,
                    context_id,
                    term,
                    preference_weight_increment,
                )

    return {
        "id": disambiguation_id,
        "query": row["query"],
        "chosen_context": context_slug,
        "resolved_by": resolved_by,
        "learned_terms": sorted(tokens),
    }
