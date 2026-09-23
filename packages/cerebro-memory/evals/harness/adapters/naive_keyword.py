"""
Reference adapter: search by normalized keyword overlap.

It is intentionally simple ("terrible but functional"): it doesn't use embeddings,
doesn't distinguish context, doesn't understand synonyms. It serves two purposes:

  1. Proving that the full harness (corpus loading, cases, metrics, report)
     works end-to-end without installing or configuring anything more than pyyaml.
  2. Being a "reasonable worst case" baseline: it is expected to have
     HIGH contamination in ambiguous cases, because it ranks by literal
     word matching and the corpus has deliberate lexical collisions
     between contexts (see evals/memories.yaml). A real adapter (mem0,
     graphiti, letta, embeddings + context filter...) should clearly
     outperform it, especially in the contamination rate for "ambiguous" and
     "temporal".
"""

from __future__ import annotations

import re
from collections import Counter

from base import MemoryAdapter

# Basic Spanish stopwords. Not meant to be exhaustive, just enough
# so that token-overlap ranking doesn't drown in "de", "la", "el"...
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
    """Lowercase, strip common accents, tokenize, and filter stopwords."""
    text = text.lower()
    accents = str.maketrans("áéíóúñü", "aeiounu")
    text = text.translate(accents)
    tokens = _TOKEN_RE.findall(text)
    return [t for t in tokens if t not in STOPWORDS_ES and len(t) > 1]


class NaiveKeywordAdapter(MemoryAdapter):
    """Indexes memories in memory (RAM) and ranks by token overlap."""

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
            # Normalizes a bit by document length so as not to reward
            # sheer text volume alone (still a naive heuristic).
            score = overlap / (len(doc_tokens) ** 0.5)
            scored.append((score, rank_hint, doc_id))

        # Descending order by score; rank_hint as a stable tiebreaker.
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [doc_id for _, _, doc_id in scored[:k]]

    def teardown(self) -> None:
        self._docs = {}
        self._order = []
