"""
Common interface that any memory system evaluated by the cerebro-memory suite
(evals/harness/run_eval.py) must implement.

Lives in its own module (instead of inside run_eval.py) so that the adapters
in evals/harness/adapters/ can import it without creating a circular import
with the runner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MemoryAdapter(ABC):
    """Minimal contract between the evaluation harness and a memory system.

    The runner (run_eval.py) calls these methods in this exact order for
    each run:

        adapter.setup()
        for memory in memories:
            adapter.insert(memory)
        for case in cases:
            ids = adapter.search(case["query"], k=5)
        adapter.teardown()

    An adapter can wrap an in-memory index (like NaiveKeywordAdapter),
    an HTTP call to an external service, or a library like mem0/graphiti/letta.
    """

    @abstractmethod
    def setup(self) -> None:
        """Prepares the adapter before inserting memories.

        Use it to open connections, create collections/indexes, authenticate
        against a service, etc. Called only once at the start of the run.
        """
        raise NotImplementedError

    @abstractmethod
    def insert(self, memory: dict) -> None:
        """Inserts a memory into the system under test.

        `memory` is a dict with, at minimum, the corpus keys from
        evals/memories.yaml: id, context, type, title, content, status.
        The adapter decides how to map those fields to its own model
        (metadata, namespace, collection, etc.).
        """
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, k: int) -> list[str]:
        """Searches for `query` and returns up to `k` memory ids, best result first.

        Must return a list of the `id`s exactly as inserted (not full
        objects), in descending relevance order. Length <= k.
        """
        raise NotImplementedError

    @abstractmethod
    def teardown(self) -> None:
        """Releases resources at the end of the run (close connections, delete temporary indexes, etc.)."""
        raise NotImplementedError
