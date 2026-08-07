"""Unit tests for the Context Engine's scoring/threshold logic (Fase 2, plan_v2.md SS7).

`score_contexts` and `decide_from_scores` are pure functions (no I/O - they take
already-fetched preliminary results / preferences and return decisions), so these
run without Postgres, same spirit as tests/test_rrf.py.
"""

from __future__ import annotations

from knowledgeos.context_engine import (
    _ContextRow,
    decide_from_scores,
    normalize_tokens,
    score_contexts,
)

PREF_BOOST = 0.03
MENTION_BOOST = 0.12
DOMINANCE = 0.6
MARGIN = 0.2


def _ctx(slug: str, name: str | None = None, description: str | None = None) -> _ContextRow:
    return _ContextRow(id=slug, slug=slug, name=name or slug, description=description)


# --------------------------------------------------------------------------- normalize_tokens


def test_normalize_tokens_strips_stopwords_accents_and_short_noise():
    tokens = normalize_tokens("¿Cuánto gasté este mes en José?")
    assert "gaste" in tokens
    assert "jose" in tokens
    assert "mes" in tokens
    # stopwords must not survive
    assert "cuanto" not in tokens
    assert "este" not in tokens
    assert "en" not in tokens


# --------------------------------------------------------------------------- dominance


def test_dominance_one_context_clearly_wins():
    contexts = [_ctx("finanzas-personales"), _ctx("expense-tracker")]
    preliminary = [
        {"context": "finanzas-personales", "score": 0.030},
        {"context": "finanzas-personales", "score": 0.025},
        {"context": "finanzas-personales", "score": 0.020},
        {"context": "expense-tracker", "score": 0.005},
    ]
    scored = score_contexts(
        preliminary_results=preliminary,
        contexts=contexts,
        query="cuanto gaste este mes",
        preferences={},
        preference_boost_per_weight=PREF_BOOST,
        mention_boost=MENTION_BOOST,
    )
    decision = decide_from_scores(scored, dominance_threshold=DOMINANCE, margin_threshold=MARGIN)

    assert decision.mode == "auto"
    assert decision.top_slug == "finanzas-personales"


def test_no_signal_at_all_returns_auto_with_no_context():
    decision = decide_from_scores([], dominance_threshold=DOMINANCE, margin_threshold=MARGIN)
    assert decision.mode == "auto"
    assert decision.top_slug is None
    assert decision.normalized == []


# --------------------------------------------------------------------------- tie -> ambiguous


def test_tie_between_two_contexts_is_ambiguous():
    contexts = [_ctx("cliente-acme"), _ctx("finanzas-personales")]
    preliminary = [
        {"context": "cliente-acme", "score": 0.020},
        {"context": "finanzas-personales", "score": 0.020},
    ]
    scored = score_contexts(
        preliminary_results=preliminary,
        contexts=contexts,
        query="cual es el presupuesto",
        preferences={},
        preference_boost_per_weight=PREF_BOOST,
        mention_boost=MENTION_BOOST,
    )
    decision = decide_from_scores(scored, dominance_threshold=DOMINANCE, margin_threshold=MARGIN)

    assert decision.mode == "ambiguous"
    assert decision.top_slug is None
    assert {slug for slug, _ in decision.normalized} == {"cliente-acme", "finanzas-personales"}


def test_dominant_ratio_without_enough_margin_is_still_ambiguous():
    # top context clears the DOMINANCE ratio on its own, but the runner-up is close
    # enough that the margin requirement should still classify this as ambiguous -
    # guards against a lone "51 vs 49 out of a 3rd tiny context" false dominance.
    contexts = [_ctx("a"), _ctx("b"), _ctx("c")]
    preliminary = [
        {"context": "a", "score": 0.031},
        {"context": "b", "score": 0.029},
        {"context": "c", "score": 0.001},
    ]
    scored = score_contexts(
        preliminary_results=preliminary,
        contexts=contexts,
        query="algo generico",
        preferences={},
        preference_boost_per_weight=PREF_BOOST,
        mention_boost=MENTION_BOOST,
    )
    decision = decide_from_scores(scored, dominance_threshold=0.45, margin_threshold=MARGIN)
    # a's normalized share is ~0.51 (>= 0.45 dominance) but margin over b (~0.02) < 0.2
    assert decision.mode == "ambiguous"


# --------------------------------------------------------------------------- preference boost


def test_preference_boost_tips_a_near_tie_into_dominance():
    contexts = [_ctx("expense-tracker"), _ctx("finanzas-personales")]
    preliminary = [
        {"context": "expense-tracker", "score": 0.020},
        {"context": "finanzas-personales", "score": 0.018},
    ]

    scored_no_pref = score_contexts(
        preliminary_results=preliminary,
        contexts=contexts,
        query="gastos del mes",
        preferences={},
        preference_boost_per_weight=PREF_BOOST,
        mention_boost=MENTION_BOOST,
    )
    decision_no_pref = decide_from_scores(scored_no_pref, dominance_threshold=DOMINANCE, margin_threshold=MARGIN)
    assert decision_no_pref.mode == "ambiguous"

    # a learned preference (from a past resolved disambiguation) pushes "gastos"
    # towards finanzas-personales - should now flip the decision to auto/dominant.
    preferences = {("finanzas-personales", "gastos"): 5.0}
    scored_with_pref = score_contexts(
        preliminary_results=preliminary,
        contexts=contexts,
        query="gastos del mes",
        preferences=preferences,
        preference_boost_per_weight=PREF_BOOST,
        mention_boost=MENTION_BOOST,
    )
    decision_with_pref = decide_from_scores(scored_with_pref, dominance_threshold=DOMINANCE, margin_threshold=MARGIN)
    assert decision_with_pref.mode == "auto"
    assert decision_with_pref.top_slug == "finanzas-personales"


def test_preferences_only_apply_to_tokens_actually_in_the_query():
    contexts = [_ctx("a"), _ctx("b")]
    preliminary = [{"context": "a", "score": 0.01}, {"context": "b", "score": 0.01}]
    # preference term "presupuesto" does not appear in this query, so it must not
    # contribute any boost.
    preferences = {("a", "presupuesto"): 50.0}
    scored = score_contexts(
        preliminary_results=preliminary,
        contexts=contexts,
        query="cuanto gaste hoy",
        preferences=preferences,
        preference_boost_per_weight=PREF_BOOST,
        mention_boost=MENTION_BOOST,
    )
    by_slug = {sc.ctx.slug: sc for sc in scored}
    assert by_slug["a"].preference_score == 0.0


# --------------------------------------------------------------------------- explicit mention


def test_explicit_context_mention_boosts_that_context():
    contexts = [
        _ctx("expense-tracker", name="Expense Tracker"),
        _ctx("finanzas-personales", name="Finanzas Personales"),
    ]
    preliminary = [
        {"context": "expense-tracker", "score": 0.010},
        {"context": "finanzas-personales", "score": 0.012},
    ]
    scored = score_contexts(
        preliminary_results=preliminary,
        contexts=contexts,
        query="cuanto gaste en expense tracker",
        preferences={},
        preference_boost_per_weight=PREF_BOOST,
        mention_boost=MENTION_BOOST,
    )
    decision = decide_from_scores(scored, dominance_threshold=DOMINANCE, margin_threshold=MARGIN)
    assert decision.mode == "auto"
    assert decision.top_slug == "expense-tracker"
