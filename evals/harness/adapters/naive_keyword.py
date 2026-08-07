"""
Adaptador de referencia: búsqueda por overlap de palabras clave normalizadas.

Es intencionalmente simple ("pésimo pero funcional"): no usa embeddings, no
distingue contexto, no entiende sinónimos. Sirve para dos cosas:

  1. Probar que el harness completo (carga de corpus, casos, métricas, reporte)
     funciona de punta a punta sin instalar ni configurar nada más que pyyaml.
  2. Ser una línea base de "peor caso razonable": se espera que tenga
     contaminación ALTA en los casos ambiguos, porque rankea por coincidencia
     literal de palabras y el corpus tiene colisiones léxicas deliberadas
     entre contextos (ver evals/memories.yaml). Un adaptador real (mem0,
     graphiti, letta, embeddings + filtro de contexto...) debería superarlo
     claramente, sobre todo en la tasa de contaminación de "ambiguo" y
     "temporal".
"""

from __future__ import annotations

import re
from collections import Counter

from base import MemoryAdapter

# Stopwords españolas básicas. No pretende ser exhaustiva, solo suficiente
# para que el ranking por overlap de tokens no se ahogue en "de", "la", "el"...
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


def _normalize(text: str) -> list[str]:
    """Minúsculas, quita acentos comunes, tokeniza y filtra stopwords."""
    text = text.lower()
    accents = str.maketrans("áéíóúñü", "aeiounu")
    text = text.translate(accents)
    tokens = _TOKEN_RE.findall(text)
    return [t for t in tokens if t not in STOPWORDS_ES and len(t) > 1]


class NaiveKeywordAdapter(MemoryAdapter):
    """Indexa memorias en memoria (RAM) y rankea por overlap de tokens."""

    def __init__(self) -> None:
        self._docs: dict[str, Counter] = {}
        self._order: list[str] = []

    def setup(self) -> None:
        self._docs = {}
        self._order = []

    def insert(self, memory: dict) -> None:
        text = f"{memory.get('title', '')} {memory.get('content', '')}"
        tokens = _normalize(text)
        self._docs[memory["id"]] = Counter(tokens)
        self._order.append(memory["id"])

    def search(self, query: str, k: int) -> list[str]:
        query_tokens = set(_normalize(query))
        if not query_tokens:
            return []

        scored: list[tuple[float, int, str]] = []
        for rank_hint, doc_id in enumerate(self._order):
            doc_tokens = self._docs[doc_id]
            if not doc_tokens:
                continue
            overlap = sum(doc_tokens[t] for t in query_tokens if t in doc_tokens)
            if overlap == 0:
                continue
            # Normaliza un poco por longitud del documento para no premiar
            # solo el volumen de texto (sigue siendo una heurística naive).
            score = overlap / (len(doc_tokens) ** 0.5)
            scored.append((score, rank_hint, doc_id))

        # Orden descendente por score; rank_hint como desempate estable.
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [doc_id for _, _, doc_id in scored[:k]]

    def teardown(self) -> None:
        self._docs = {}
        self._order = []
