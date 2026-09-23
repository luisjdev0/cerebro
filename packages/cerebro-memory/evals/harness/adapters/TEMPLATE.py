"""
Adapter skeleton for connecting a real memory system
(mem0, graphiti, letta, or cerebro-memory itself) to the evaluation suite.

HOW TO USE THIS FILE
-----------------------
1. Copy this file to `evals/harness/adapters/<system_name>.py`
   (e.g. `mem0.py`, `graphiti.py`, `letta.py`).
2. Rename the `TemplateAdapter` class to something like `Mem0Adapter`.
3. Fill in the TODOs in each method with the real API/SDK calls
   for the system.
4. Register it in `evals/harness/adapters/__init__.py`:

       from .mem0 import Mem0Adapter
       ADAPTERS = {
           "naive": NaiveKeywordAdapter,
           "mem0": Mem0Adapter,
       }

5. Run `python evals/harness/run_eval.py --adapter mem0`.

Do NOT implement real logic in THIS file (TEMPLATE.py) — it is only the
reference skeleton. It is not registered in ADAPTERS and is not executed.
"""

from __future__ import annotations

from base import MemoryAdapter


class TemplateAdapter(MemoryAdapter):
    """Replace this docstring with a description of the real system
    (which API/SDK it uses, whether it runs locally or remotely, auth requirements, etc.)."""

    def __init__(self) -> None:
        # TODO: store client config here (api_key, base_url, project_id,
        # collection/namespace name to use for this run, etc.). Do not
        # hardcode credentials: read them from environment variables.
        #
        # Example (mem0):
        #   self.client = None  # created in setup()
        #   self.user_id = "eval-run"  # namespace/isolation to avoid
        #                               # mixing with real memories
        #
        # Example (graphiti):
        #   self.graphiti = None
        #   self.group_id = "eval-run"
        #
        # Example (letta):
        #   self.client = None
        #   self.agent_id = None
        pass

    def setup(self) -> None:
        """Initializes the client/connection and leaves the system ready to insert.

        TODO:
          - mem0: instantiate `Memory()` or `MemoryClient(api_key=...)`.
          - graphiti: instantiate `Graphiti(neo4j_uri, user, password)` and
            call `build_indices_and_constraints()` if applicable.
          - letta: create a `Letta(...)` client and, if the adapter tests
            an agent's memory, create/retrieve the evaluation agent.

        Important: if the system persists data across runs, use a
        namespace/collection dedicated to evals (see `self.user_id` /
        `self.group_id` above) so as not to contaminate real memories or
        carry over results from a previous run.
        """
        raise NotImplementedError("TODO: initialize the real system's client")

    def insert(self, memory: dict) -> None:
        """Inserts a memory from the corpus (evals/memories.yaml) into the real system.

        `memory` carries: id, context, type, title, content, status (and any
        other field added to the corpus later).

        TODO:
          - Decide how to map `context` (e.g. "cliente-acme") to the system's
            isolation concept (namespace, tag, subgraph, agent_id...).
          - Decide how to map `type` (semantic/episodic/procedural/decision)
            if the system distinguishes memory types.
          - Store `memory["id"]` as metadata so it can be returned as-is
            in `search()` — the harness compares by that id, not by text.

        Example (mem0, very simplified):
            self.client.add(
                memory["content"],
                user_id=self.user_id,
                metadata={"eval_id": memory["id"], "context": memory["context"]},
            )
        """
        raise NotImplementedError("TODO: insert the memory into the real system")

    def search(self, query: str, k: int) -> list[str]:
        """Searches for `query` in the real system and returns up to `k` corpus ids.

        TODO:
          - Call the system's search endpoint/method (e.g.
            `self.client.search(query, user_id=self.user_id, limit=k)`).
          - Extract `eval_id` from each result's metadata (the id you
            stored in `insert()`), NOT the system's internal id.
          - Return the list in descending relevance order, length <= k.

        If the real system does not support an exact top-k, truncate here.
        """
        raise NotImplementedError("TODO: search and map results to corpus ids")

    def teardown(self) -> None:
        """Cleans up the state created in setup()/insert() to leave the system clean.

        TODO:
          - Delete the evaluation namespace/collection/group if the system
            persists to disk or a remote service (so the next run starts
            from scratch and metrics stay reproducible).
          - Close connections (DB drivers, HTTP sessions, etc.).
        """
        raise NotImplementedError("TODO: clean up run data and close connections")
