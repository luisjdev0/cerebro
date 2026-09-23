"""
cerebro-memory adapter (the product's own system) against the real HTTP API.

Unlike NaiveKeywordAdapter (in-RAM index) or a mem0/graphiti/letta (in-process
SDK), this adapter talks to `cerebro_memory.api` over HTTP -- exactly
as the MCP server (`cerebro_memory.mcp_server`) or any other real client
would. Requires the API to be running (see README.md, `python -m cerebro_memory.main`)
and Postgres/pgvector to be up (`docker compose up -d`).

Config via environment variables (same names as the MCP server, so a
single `.env` covers everything):
    KNOWLEDGEOS_API_URL     default http://localhost:8000
    KNOWLEDGEOS_API_TOKEN   default "change-me-dev-token" (the .env.example default)
    KNOWLEDGEOS_SEARCH_SCOPE   "all" (default) | "auto" -- see below.

## Search mode: scope=all (Phase 1, control) vs scope=auto (Context Engine, Phase 2)

`KNOWLEDGEOS_SEARCH_SCOPE=all` (default, compatible with Phase 1 runs):
searches WITHOUT a context filter and without invoking the Context Engine -- measures
pure hybrid retrieval (vector + full-text), the way an agent that doesn't yet
know which context the question belongs to would today.

`KNOWLEDGEOS_SEARCH_SCOPE=auto`: passes `scope=auto`, enabling the Context Engine
(plan_v2.md SS7) in the API. Two cases:
  - The API decides that one context clearly dominates -> it already filters by that
    context and this adapter simply uses those results.
  - The API responds `ambiguous` (candidates + per-candidate evidence, `results`
    deliberately empty): this adapter simulates a reasonable agent **without reading
    the evaluation case's `contexto_esperado`** -- that would be cheating, since the
    real Context Engine has no access to that answer. The policy is: among the
    returned candidates, pick the one whose individual top result has the best score
    (`results_by_candidate[slug][0]['score']`), call
    `POST /disambiguations/{id}/resolve` with that context (the same as the
    MCP server would do), and repeat the search now scoped to it to get real
    results. If no candidate brings evidence (shouldn't happen in practice), it falls
    back to the candidate the engine itself scored highest.

## Real supersedence (Task 4 -- fixes harness debt)

Previously, this adapter inserted ALL corpus memories (active and superseded)
as independent POSTs, each ending up `active` in the real system -- the
suite's "temporal" category, with `--include-superseded`, ended up measuring a
scenario cerebro-memory can't even produce (two "active" memories in
conflict), not its real supersedence model (`PATCH` creates a new version + marks
the old one `superseded`, and default retrieval already excludes it).

Now, for the pairs marked in evals/memories.yaml with `superseded_by_id` (the 3
cases: production operating system, Acme's hourly rate, personal monthly
budget), `_insert_supersedence_chain()` does the real thing: `POST` the old
version, then `PATCH /memories/{id}` with the new version's content -- creating the
system's genuine supersedence chain. This only happens when the runner passed both
halves of the pair to `insert()` (i.e. with `--include-superseded`); without that
flag, the runner filters out `superseded` memories before calling `insert()` and the
"new" memory is simply inserted on its own, like any other active one.
"""

from __future__ import annotations

import os

import httpx
from base import MemoryAdapter

API_URL = os.environ.get("KNOWLEDGEOS_API_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.environ.get("KNOWLEDGEOS_API_TOKEN", "change-me-dev-token")
SEARCH_SCOPE = os.environ.get("KNOWLEDGEOS_SEARCH_SCOPE", "all")

# kind per corpus context (evals/memories.yaml), aligned with the table in
# evals/README.md ("proyecto" for expense-tracker/cliente-acme, "dominio" for the
# rest). Any new context that appears in the corpus and isn't listed here is still
# created, with kind="dominio" by default.
CONTEXT_KIND: dict[str, str] = {
    "expense-tracker": "proyecto",
    "cliente-acme": "proyecto",
    "finanzas-personales": "dominio",
    "infraestructura": "dominio",
    "salud": "dominio",
    "aprendizaje": "dominio",
}

CONTEXT_DESCRIPTION: dict[str, str] = {
    "expense-tracker": "Proyecto de software propio: app de finanzas personales (Next.js + Supabase).",
    "cliente-acme": "Proyecto freelance para el cliente Acme.",
    "finanzas-personales": "Finanzas personales reales del usuario (gastos, presupuesto, ahorro).",
    "infraestructura": "VPS personal, despliegues, DNS, backups.",
    "salud": "Ejercicio, alergias, chequeos médicos.",
    "aprendizaje": "Cursos, lecturas, certificaciones.",
}


class CerebroMemoryAdapter(MemoryAdapter):
    """Adapter against the real cerebro-memory API (`src/cerebro_memory/api.py`) via httpx."""

    def __init__(self) -> None:
        self.client: httpx.Client | None = None
        # corpus id (yaml slug) -> real UUID assigned by the API, and its inverse
        # so slugs can be returned from search() as MemoryAdapter.search() requires.
        self._uuid_to_slug: dict[str, str] = {}
        # insert() only stores; the real build (including resolving
        # superseded_by_id pairs, which may need to see both halves of the pair
        # regardless of the order they appear in the corpus) happens only once,
        # lazily, on the first search() - see _build_and_flush().
        self._buffered: list[dict] = []
        self._built = False

    def setup(self) -> None:
        self.client = httpx.Client(
            base_url=API_URL,
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "X-Agent-Name": "eval-harness",
            },
            timeout=30.0,
        )

        try:
            resp = self.client.get("/health")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"cerebro-memory API no responde en {API_URL}/health ({exc}). "
                "Arráncala primero con `python -m cerebro_memory.main` "
                "(y `docker compose up -d` para Postgres)."
            ) from exc

        self._uuid_to_slug = {}
        self._buffered = []
        self._built = False
        self._clean_existing_memories()
        self._ensure_contexts(sorted(CONTEXT_KIND.keys()))

    def _clean_existing_memories(self) -> None:
        """Hard-deletes every pre-existing memory, so each eval run starts from
        scratch and metrics stay reproducible (there's no dedicated "purge
        everything" endpoint, so we list via a broad search and hard-delete
        one by one)."""
        assert self.client is not None
        seen: set[str] = set()
        # Walks every existing context (from a previous run) and purges
        # its memories, including superseded ones (include_superseded=True) so as
        # not to leave orphans that break hard-delete via the FK (superseded_by).
        resp = self.client.get("/contexts")
        resp.raise_for_status()
        for ctx in resp.json():
            search_resp = self.client.get(
                "/memories/search",
                params={
                    "q": "a",
                    "context": ctx["slug"],
                    "limit": 50,
                    "include_superseded": True,
                },
            )
            search_resp.raise_for_status()
            for mem in search_resp.json()["results"]:
                seen.add(mem["id"])

        # Deleting first the ones not pointed to by superseded_by of an already-deleted
        # active memory would require topological order; simpler: retry in several
        # passes until nothing is left or progress stalls (avoids the FK 409).
        remaining = seen
        for _ in range(len(remaining) + 1):
            if not remaining:
                break
            still_there: set[str] = set()
            for mem_id in remaining:
                del_resp = self.client.delete(f"/memories/{mem_id}", params={"hard": True})
                if del_resp.status_code not in (200, 404):
                    still_there.add(mem_id)
            if still_there == remaining:
                break  # no progress, stop trying (shouldn't happen in practice)
            remaining = still_there

        # Note: this does NOT clean `disambiguation_log` / `context_preferences` (there
        # is no HTTP endpoint to purge them and this adapter deliberately stays
        # limited to HTTP, like any real client). To compare benchmark runs
        # cold (scope=auto with no learning accumulated from a previous
        # run), truncate those two tables manually between runs -- see
        # evals/README.md, "Context Engine" section.

    def _ensure_contexts(self, slugs: list[str]) -> None:
        assert self.client is not None
        resp = self.client.get("/contexts")
        resp.raise_for_status()
        existing = {c["slug"] for c in resp.json()}

        for slug in slugs:
            if slug in existing:
                continue
            create_resp = self.client.post(
                "/contexts",
                json={
                    "slug": slug,
                    "name": slug.replace("-", " ").title(),
                    "kind": CONTEXT_KIND.get(slug, "dominio"),
                    "description": CONTEXT_DESCRIPTION.get(slug, ""),
                },
            )
            if create_resp.status_code not in (201, 409):
                create_resp.raise_for_status()

    def insert(self, memory: dict) -> None:
        slug = memory["context"]
        if slug not in CONTEXT_KIND:
            # Corpus brings a context not anticipated in CONTEXT_KIND: create it on the
            # fly with a default kind instead of failing.
            self._ensure_contexts([slug])
        self._buffered.append(memory)

    # ------------------------------------------------------------- real build

    def _build_and_flush(self) -> None:
        """Materializes all buffered memories in the real API, resolving
        `superseded_by_id` pairs into genuine supersedence chains (Task 4)."""
        assert self.client is not None

        by_id = {m["id"]: m for m in self._buffered}
        superseded_with_target = {
            m["id"]: m
            for m in self._buffered
            if m.get("status") == "superseded" and m.get("superseded_by_id")
        }
        target_ids = {m["superseded_by_id"] for m in superseded_with_target.values()}

        # 1) Everything not part of a superseded_by_id pair is inserted as-is.
        for memory in self._buffered:
            if memory["id"] in superseded_with_target or memory["id"] in target_ids:
                continue
            self._insert_plain(memory)

        # 2) Real pairs: POST the old one, PATCH with the new one's content.
        for old_id, old in superseded_with_target.items():
            new = by_id.get(old["superseded_by_id"])
            if new is None:
                # The other half of the pair isn't in this run (e.g. it ran without
                # --include-superseded but a loose superseded memory still arrived,
                # which shouldn't happen given how run_eval.py filters). We don't
                # lose it: it gets inserted on its own instead of failing.
                self._insert_plain(old)
                continue
            self._insert_supersedence_chain(old, new)

    def _insert_plain(self, memory: dict) -> None:
        assert self.client is not None
        resp = self.client.post(
            "/memories",
            json={
                "content": memory["content"],
                "context": memory["context"],
                "type": memory["type"],
                "title": memory.get("title"),
            },
        )
        resp.raise_for_status()
        created = resp.json()
        self._uuid_to_slug[created["id"]] = memory["id"]

    def _insert_supersedence_chain(self, old: dict, new: dict) -> None:
        """POSTs the old memory, then PATCHes with the new one's content: the same
        path memory_update() would use in production. The result in the real
        database is old.status='superseded', old.superseded_by=<new's uuid>,
        new.status='active' -- not two active rows competing, which was the bug
        this task fixes (see the module docstring)."""
        assert self.client is not None
        old_resp = self.client.post(
            "/memories",
            json={
                "content": old["content"],
                "context": old["context"],
                "type": old["type"],
                "title": old.get("title"),
            },
        )
        old_resp.raise_for_status()
        old_created = old_resp.json()
        self._uuid_to_slug[old_created["id"]] = old["id"]

        patch_resp = self.client.patch(
            f"/memories/{old_created['id']}",
            json={"content": new["content"]},
        )
        patch_resp.raise_for_status()
        new_created = patch_resp.json()
        self._uuid_to_slug[new_created["id"]] = new["id"]

    # ------------------------------------------------------------------------ search

    def search(self, query: str, k: int) -> list[str]:
        assert self.client is not None
        if not self._built:
            self._build_and_flush()
            self._built = True

        params: dict[str, object] = {"q": query, "limit": k}
        if SEARCH_SCOPE == "auto":
            params["scope"] = "auto"
        else:
            params["scope"] = "all"

        resp = self.client.get("/memories/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data["results"]
        scope_decision = data.get("scope_decision", {})

        if SEARCH_SCOPE == "auto" and scope_decision.get("mode") == "ambiguous":
            results = self._resolve_ambiguous_like_a_reasonable_agent(query, k, scope_decision)

        return [self._uuid_to_slug[r["id"]] for r in results if r["id"] in self._uuid_to_slug][:k]

    def _resolve_ambiguous_like_a_reasonable_agent(
        self, query: str, k: int, scope_decision: dict
    ) -> list[dict]:
        """Deliberate policy (Task 5): picks the candidate whose individual TOP
        result has the best score, WITHOUT looking at the evaluation case's
        `contexto_esperado` -- the real Context Engine has no access to that answer,
        so using it here would be measuring an adapter that cheats. It then calls
        `POST /disambiguations/{id}/resolve` (the same as the MCP server would do
        upon detecting that the agent repeated the search with an explicit context)
        and repeats the search, now scoped, to get real results.

        Calibration note: the RRF score of result #1 within a candidate is
        almost always one of a handful of discrete values (1/(k+1), 2/(k+1)...
        depending on how many of the two lists -vector/fts- ranked it #1), so
        exact ties between candidates are the COMMON case, not the exception, with a
        `results_by_candidate` of only 2-3 items. A real agent looking at that
        evidence and seeing a tie doesn't flip a coin: it keeps reading the signal it
        already has in front of it -- the candidate's own aggregate score (`candidate['score']`,
        the normalized RRF sum of ALL its memories in the preliminary top, already
        computed by the Context Engine, not derived from `contexto_esperado`). That's why
        the real criterion is (top_result_score, candidate_score) in that order: the
        individual result rules, and the candidate's score only breaks ties.
        """
        assert self.client is not None
        candidates = scope_decision.get("candidates") or []
        results_by_candidate = scope_decision.get("results_by_candidate") or {}

        best_slug: str | None = None
        best_key: tuple[float, float] = (-1.0, -1.0)
        for candidate in candidates:
            slug = candidate["slug"]
            cand_results = results_by_candidate.get(slug) or []
            top_result_score = cand_results[0]["score"] if cand_results else -1.0
            key = (top_result_score, candidate.get("score", 0.0))
            if key > best_key:
                best_key = key
                best_slug = slug

        if best_slug is None and candidates:
            # No candidate brought evidence (shouldn't happen in practice):
            # falls back to the one the Context Engine itself scored highest.
            best_slug = candidates[0]["slug"]

        if best_slug is None:
            return []

        disambiguation_id = scope_decision.get("disambiguation_id")
        if disambiguation_id:
            resolve_resp = self.client.post(
                f"/disambiguations/{disambiguation_id}/resolve",
                json={"context": best_slug},
            )
            # Not fatal if it fails (e.g. already resolved) - we still proceed with the
            # scoped search anyway so as not to lose the case's result.
            if resolve_resp.status_code not in (200, 404, 409):
                resolve_resp.raise_for_status()

        rescoped_resp = self.client.get(
            "/memories/search",
            params={"q": query, "limit": k, "context": best_slug},
        )
        rescoped_resp.raise_for_status()
        return rescoped_resp.json()["results"]

    def teardown(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
