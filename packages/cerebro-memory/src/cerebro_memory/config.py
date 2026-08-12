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

    # --- Context Engine (Fase 2, plan_v2.md SS7) -----------------------------------
    # Todos calibrados a mano contra evals/ (ver README "Context Engine"). No hay
    # llamadas a LLM en esta fase: el scoring es determinista y barato.

    # Tamaño del retrieval preliminar sin filtro de contexto (top-N candidato).
    context_engine_candidate_pool: int = 20

    # Un contexto "domina" (-> auto-scope) si su score normalizado (0..1, sobre la
    # suma de todos los contextos con score>0) es >= este umbral...
    context_engine_dominance_threshold: float = 0.45
    # ...Y ademas su margen sobre el 2do contexto (normalizado) es >= este umbral.
    # Evita que un empate 51/49 se declare "dominante" solo por cruzar el primer umbral.
    context_engine_margin_threshold: float = 0.25

    # Boost aditivo por cada unidad de peso de context_preferences que matchea un
    # token de la query (ver context_preferences.weight, tabla poblada por
    # resolve_disambiguation). Deliberadamente pequeno: un solo token generico
    # (p.ej. "mes", "costos") que colisiona legitimamente entre contextos no debe
    # poder tumbar la senal RRF de una sola resolucion aprendida - el aprendizaje
    # debe acumularse (varias resoluciones consistentes) antes de pesar tanto como
    # un hit real en el retrieval. Calibrado contra evals/ (ver README, seccion
    # "Context Engine").
    context_engine_preference_boost_per_weight: float = 0.008

    # Boost aditivo flat cuando la query menciona explicitamente el slug o el name
    # de un contexto (p.ej. "en Expense Tracker...").
    context_engine_mention_boost: float = 0.12

    # Cuantos candidatos (2-4 segun plan_v2) devolver cuando el caso es ambiguo.
    context_engine_candidates_min: int = 2
    context_engine_candidates_max: int = 4

    # Cuantos resultados por candidato devolver junto con la lista de ambiguedad,
    # para que el agente decida con evidencia (plan_v2 SS7 punto 3).
    context_engine_results_per_candidate: int = 3

    # --- Fase 4: clasificador local opcional (plan_v2.md SS8, condicionada) -------
    # OFF por defecto ("none" -> NullResolver, cero cambio de comportamiento). Solo
    # tiene sentido activarlo en serio cuando se cumplan las dos condiciones del plan:
    # (a) >= ~500 desambiguaciones registradas (ver `cerebro-memory export-disambiguations`)
    # y (b) una razon medida (latencia/costo/privacidad estricta) - ver README.
    context_engine_resolver: str = "none"  # "none" | "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"


@lru_cache
def get_settings() -> Settings:
    return Settings()
