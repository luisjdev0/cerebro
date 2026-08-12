"""Embedding provider abstraction.

`EmbeddingProvider` is the seam that lets us swap the local fastembed model for an
API-backed provider later (plan_v2.md SS10) without touching retrieval/API code.
`embed_query` and `embed_passage` are kept as separate methods because some models
(e.g. the E5 family) require different prefixes for queries vs. stored passages;
the model used in Fase 1 does not, so both delegate to the same call here.
"""

from __future__ import annotations

import abc
import asyncio
import logging

logger = logging.getLogger("cerebro_memory.embeddings")


class EmbeddingProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def dimension(self) -> int:
        """Vector width produced by this provider."""

    @abc.abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query."""

    @abc.abstractmethod
    async def embed_passage(self, text: str) -> list[float]:
        """Embed a piece of content being stored as a memory."""


class FastEmbedProvider(EmbeddingProvider):
    """Local, CPU-only embeddings via `fastembed` (ONNX runtime, no API keys)."""

    def __init__(self, model_name: str, dimension: int) -> None:
        from fastembed import TextEmbedding  # imported lazily so tests without

        # fastembed installed still work for unrelated modules.
        self._model = TextEmbedding(model_name=model_name)
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed_sync(self, text: str) -> list[float]:
        vector = next(iter(self._model.embed([text])))
        return vector.tolist()

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)

    async def embed_passage(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)


class SentenceTransformersProvider(EmbeddingProvider):
    """Fallback used only if `fastembed` fails to install/import on this platform."""

    def __init__(self, model_name: str, dimension: int) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed_sync(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=False).tolist()

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)

    async def embed_passage(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)


def build_embedding_provider(model_name: str, dimension: int) -> EmbeddingProvider:
    """fastembed first (lighter, no torch dependency); sentence-transformers as fallback."""
    try:
        provider = FastEmbedProvider(model_name, dimension)
        logger.info("using fastembed provider (%s)", model_name)
        return provider
    except Exception:  # noqa: BLE001
        logger.exception("fastembed unavailable, falling back to sentence-transformers")
        provider = SentenceTransformersProvider(model_name, dimension)
        logger.info("using sentence-transformers provider (%s)", model_name)
        return provider
