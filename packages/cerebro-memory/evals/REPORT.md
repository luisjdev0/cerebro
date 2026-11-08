# Final evaluation report — KnowledgeOS v1.0

**Date:** 2026-08-07
**Suite:** 40 synthetic memories / 6 contexts / 30 cases (12 direct, 12 ambiguous, 6 temporal), k=5, with `--include-superseded`.

## The signature metric: contamination rate

Fraction of cases where at least one "trap memory" (semantically relevant but
from the wrong context) makes it into the top-5. It is the metric that
motivated the project: recall was never the problem (100% on every system
measured); the problem is *what else* sneaks in next to the correct memory.

## Evolution by phase

| System | Ambiguous | Direct | Temporal | Total |
|---|---:|---:|---:|---:|
| Naive baseline (keywords) | 50.0% | 0% | 83.3% | 36.7% |
| Phase 1 — hybrid retrieval (`scope=all`) | 25.0% | 0% | 0%¹ | 10.0% |
| Phase 2+ — Context Engine (`scope=auto`) | **0%** | **0%** | **0%** | **0%** |

Recall@5 = 100% on the three systems and the three categories (it never
degraded). Precision@5 ≈ 20% by corpus design (1 relevant memory / k=5); it is
not informative here.

¹ The improvement on temporal cases combines two things: the real supersession
model (obsolete versions are not indexed as active) and hybrid retrieval. The
baseline's 83.3% corresponds to indexing both versions as active with no
relationship between them.

## What produced each jump

- **50% → 25%** (Phase 1): hybrid retrieval — multilingual embeddings + Spanish
  full-text fused with RRF discriminate better than lexical overlap, even
  without knowing the context. Real supersession eliminated temporal
  contamination.
- **25% → 0%** (Phase 2): Context Engine — deterministic context scoring
  (per-context RRF aggregate + learned preferences + explicit mention) with a
  dominance/margin decision. When no context dominates, the system **does not
  mix**: it returns candidates with evidence and leaves the choice to the
  agent, and that choice is recorded and feeds learning
  (`context_preferences`).
- **Verified with no regression** in every later phase (3, 4, 5 and
  productization): the 0% was reproduced cold (truncating the disambiguation
  log and preferences) on every control run.

## Calibration and honest caveats

- The engine's thresholds (`CONTEXT_ENGINE_DOMINANCE_THRESHOLD=0.45`,
  `MARGIN_THRESHOLD=0.25`, `PREFERENCE_BOOST_PER_WEIGHT=0.008`) were calibrated
  in 2 iterations **against this synthetic 30-case corpus**. The 0% is the
  ceiling of this suite, not a universal guarantee: real day-to-day usage data
  may require re-tuning — that is what the learned preferences and `GET /stats`
  are for.
- Findings from the calibration that stayed as design: the tie-break of the
  "reasonable agent" uses the candidate's aggregate score (the top individual
  score almost always ties because of RRF's discretization), and the preference
  boost is deliberately small so that a learned generic term ("mes") cannot
  knock down the retrieval signal with a single resolution.
- The benchmark against external alternatives (mem0/Graphiti/Letta) was
  deferred with the skeleton ready (`harness/adapters/TEMPLATE.py`); the build
  decision was made on documentary evidence
  (`../decisions/000-build-vs-adopt.md`).

## How to reproduce

```bash
# Postgres up and the API running, then:
python evals/harness/run_eval.py --adapter naive --include-superseded
KNOWLEDGEOS_SEARCH_SCOPE=all  python evals/harness/run_eval.py --adapter knowledgeos --include-superseded
KNOWLEDGEOS_SEARCH_SCOPE=auto python evals/harness/run_eval.py --adapter knowledgeos --include-superseded
```

Detailed per-case results in `results/*.json`.

## Next measurement frontier

The synthetic corpus is saturated (0%). Further improvements must be measured
with:
1. **Real user cases** added to `cases.yaml` (see this folder's README).
2. **Dogfooding**: `knowledgeos stats` exposes auto vs. agent disambiguations
   and learned preferences — the auto-resolution rate growing over time is the
   metric that learning works in the real world.
3. Once ~500 real disambiguations exist (`knowledgeos export-disambiguations`),
   evaluate the local classifier (Phase 4) against the current flow.
