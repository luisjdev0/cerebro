"""Settings loaded from the environment (and .env for local dev)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://knowledgeos:knowledgeos@localhost:5432/knowledgeos"
    api_token: str = "change-me-dev-token"

    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = 384

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    migrations_dir: str = "db/migrations"

    # --- Context Engine (Phase 2, plan_v2.md SS7) -----------------------------------
    # All hand-calibrated against evals/ (see README "Context Engine"). There are no
    # LLM calls in this phase: scoring is deterministic and cheap.

    # Size of the preliminary retrieval with no context filter (top-N candidates).
    context_engine_candidate_pool: int = 20

    # A context "dominates" (-> auto-scope) if its normalized score (0..1, over the
    # sum of all contexts with score>0) is >= this threshold...
    context_engine_dominance_threshold: float = 0.45
    # ...AND its margin over the 2nd context (normalized) is also >= this threshold.
    # Prevents a 51/49 tie from being declared "dominant" just for crossing the first threshold.
    context_engine_margin_threshold: float = 0.25

    # Additive boost per unit of context_preferences weight that matches a
    # query token (see context_preferences.weight, table populated by
    # resolve_disambiguation). Deliberately small: a single generic token
    # (e.g. "month", "costs") that legitimately collides between contexts must not
    # be able to override the RRF signal from a single learned resolution - learning
    # must accumulate (several consistent resolutions) before it weighs as much as
    # a real hit in retrieval. Calibrated against evals/ (see README, "Context
    # Engine" section).
    context_engine_preference_boost_per_weight: float = 0.008

    # Flat additive boost when the query explicitly mentions the slug or name
    # of a context (e.g. "in Expense Tracker...").
    context_engine_mention_boost: float = 0.12

    # How many candidates (2-4 per plan_v2) to return when the case is ambiguous.
    context_engine_candidates_min: int = 2
    context_engine_candidates_max: int = 4

    # How many results per candidate to return along with the ambiguity list,
    # so the agent can decide with evidence (plan_v2 SS7 point 3).
    context_engine_results_per_candidate: int = 3

    # --- Phase 4: optional local classifier (plan_v2.md SS8, conditional) -------
    # OFF by default ("none" -> NullResolver, zero behavior change). It only
    # makes sense to seriously enable it once the plan's two conditions are met:
    # (a) >= ~500 disambiguations recorded (see `cerebro-memory export-disambiguations`)
    # and (b) a measured reason (latency/cost/strict privacy) - see README.
    context_engine_resolver: str = "none"  # "none" | "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"


@lru_cache
def get_settings() -> Settings:
    return Settings()
