"""Single MCP server for the cerebro ecosystem (stdio transport).

THIN adapter over `cerebro_clients` (`MemoryClient`/`DocsClient`): each tool
calls a client method and translates the result -- or the exception
(`CerebroAPIError`/`CerebroConnectionError`) -- into something useful for the LLM that calls it.
There is no business logic here (ecosistema-cerebro.md SS10): all of it lives in the
cerebro-memory/cerebro-docs APIs, so that `cerebro-cli` shares exactly the same
path via `cerebro_clients`.

`memory_*` / `docs_*` prefixes (SS10) to avoid confusing the two tool families --
the `memory_*` ones are a 1:1 port of the ones that already existed in the original
`mcp_server.py` of cerebro-memory (same names, same schemas/descriptions: the user's
agents already know them), and the `docs_*` ones are new.

Config via environment variables -- see `cerebro_clients.config` (SS4/SS13):
    CEREBRO_MEMORY_URL / CEREBRO_DOCS_URL   base URLs of each API
    CEREBRO_TOKEN                            single token shared between both
    CEREBRO_AGENT_NAME                       identity of this MCP client
    KNOWLEDGEOS_API_URL / KNOWLEDGEOS_API_TOKEN / KNOWLEDGEOS_AGENT_NAME
                                              compatibility fallback (memory only)

Startup:
    python -m cerebro_mcp.server
    # or, after `pip install -e .`, the console entry point:
    cerebro-mcp
"""

from __future__ import annotations

from typing import Any

from cerebro_clients import (
    AuthClient,
    CerebroAPIError,
    CerebroConnectionError,
    DocsClient,
    FlowsClient,
    MemoryClient,
)
from mcp.server.fastmcp import FastMCP

MEMORY_TYPES = ("semantic", "episodic", "procedural", "decision")
SECTION_OPERATIONS = ("replace", "append", "insert_after", "insert_before", "delete")

_memory = MemoryClient()
_docs = DocsClient()
_flows = FlowsClient()
_auth = AuthClient()

# Process-memory of the last *unresolved* disambiguation (Phase 2 Context Engine,
# plan_v2.md SS7). See the memory_search() docstring for the auto-resolution
# behavior this enables. Deliberately a single slot, not a
# stack/history: it only needs to bridge "ambiguous search" -> "the NEXT search
# from the agent with an explicit context", which is the pattern that an
# agent calling tools naturally produces.
_last_disambiguation_id: str | None = None

mcp = FastMCP(
    name="cerebro",
    instructions=(
        "cerebro ecosystem for AI agents: persistent memory (memory_*) and a "
        "repository of Markdown documents (docs_*). Use memory_search to "
        "retrieve saved facts, decisions and events before answering anything "
        "that depends on the user's history; use memory_remember to save "
        "new information relevant in the long term. Every memory lives in a "
        "'context' (project, client, life domain); call memory_contexts to "
        "see the available contexts before writing if you're not sure which "
        "one to use. Use docs_save/docs_get/docs_search for complete Markdown "
        "documents (guides, definitions, long notes) that should NOT be distilled "
        "into memories -- cerebro-docs is a document repository, not formal memory; "
        "call docs_categories to see the available categories before saving. Use "
        "flow_* for processes/checklists with steps, decisions and approval "
        "checkpoints -- flow_start/flow_next reveal the flow one step at a time "
        "(never read a full definition to 'follow' it manually), and flow_validate/"
        "flow_save/flow_update let you author new flows iterating over specific "
        "errors before saving them. Use auth_* to manage identities, groups and "
        "tokens in cerebro-auth -- these tools grant real access to other "
        "identities, so read each one's docstring in full before calling it, "
        "especially auth_create_token."
    ),
)


# --------------------------------------------------------------------------- error helpers


def _connection_error_message(service: str, exc: CerebroConnectionError) -> str:
    return (
        f"No se pudo conectar con la API de {service} en {exc.base_url}: {exc.original}. "
        f"Verifica que este corriendo y que la URL/variables de entorno sean correctas."
    )


def _auth_error_message(service: str, exc: CerebroAPIError) -> str:
    return (
        f"La API de {service} rechazo la autenticacion (401). Verifica que la variable "
        "de entorno CEREBRO_TOKEN tenga un valor valido para ese servicio."
    )


def _http_error_message(service: str, exc: CerebroAPIError) -> str:
    return f"La API de {service} devolvio {exc.status_code}: {exc.detail}"


def _format_context_list(contexts: list[dict[str, Any]]) -> str:
    if not contexts:
        return "(no hay contextos creados todavia; usa memory_create_context para crear el primero)"
    return "\n".join(f"- {c['slug']} ({c['kind']}): {c.get('description') or 'sin descripcion'}" for c in contexts)


def _format_category_list(categories: list[dict[str, Any]]) -> str:
    if not categories:
        return "(no hay categorias creadas todavia; usa docs_create_category para crear la primera)"
    return "\n".join(f"- {c['slug']}: {c.get('description') or c['name']}" for c in categories)


def _format_ambiguous_message(scope_decision: dict[str, Any]) -> str:
    candidates = scope_decision.get("candidates") or []
    results_by_candidate = scope_decision.get("results_by_candidate") or {}

    lines = ["La consulta es ambigua entre estos contextos:"]
    for c in candidates:
        pct = f"{c.get('score', 0.0):.0%}"
        desc = c.get("description") or c.get("name") or c["slug"]
        lines.append(f"- {c['slug']} ({pct}): {desc}")
        for r in results_by_candidate.get(c["slug"], []):
            lines.append(f"    · {r['title']}")
    lines.append(
        "Elige llamando memory_search con context=<slug>, o pregunta al usuario cual "
        "corresponde."
    )
    return "\n".join(lines)


# =============================================================================== memory_*


@mcp.tool()
def memory_search(
    query: str,
    context: str | None = None,
    type: str | None = None,  # noqa: A002 - nombre alineado con la API/plan_v2
    limit: int = 5,
    expand: bool = False,
) -> dict[str, Any]:
    """Searches saved memories by content (hybrid retrieval: vector + full text).

    Use it before answering any question that might depend on something the
    user already said before (preferences, past decisions, facts about their life or
    their projects) -- it's more reliable than assuming or reviewing the history of
    the current conversation, which is lost between sessions.

    What a "context" is: every memory belongs to a context (e.g. a software
    project, a client, a life domain like "health" or "personal-finances").
    It's used to isolate information that should NOT be mixed together: two contexts can
    share vocabulary (e.g. "expenses" appears both in a finance project
    and in the user's real personal finances) without being relevant to each other.

    Automatic scoping (Context Engine, Phase 2): if you do NOT pass `context`, the search uses
    `scope=auto` in the API. A cheap, deterministic scorer (no LLM) decides whether a
    context clearly dominates:
      - If it dominates, the search already comes filtered to that context (`scope_decision.mode
        == "auto"`) -- you don't need to do anything else.
      - If it's ambiguous (`scope_decision.mode == "ambiguous"`), `results` comes back empty on
        purpose (so as not to blindly mix memories from different contexts) and instead
        you get `candidates` (2-4 possible contexts with their description and score)
        and `results_by_candidate` (2-3 real results from each, as evidence).
        The `message` field already brings this formatted as text ready to reason about or
        show. Decide yourself (using the conversation's context) or ask the user,
        and repeat the call passing `context=<slug>` of the matching one.

    Automatic learning: this MCP server remembers in process memory the
    `disambiguation_id` of the last ambiguous search. If your NEXT call to
    memory_search passes an explicit `context`, the server assumes that's how you resolved that
    ambiguity and automatically calls `POST /disambiguations/{id}/resolve` with that
    context -- without you having to do anything extra. That grows
    `context_preferences` on the server (the tokens of that query add weight towards the
    chosen context), so similar questions in the future tend to resolve
    on their own (`mode == "auto"`) instead of being ambiguous again. The "slot" is cleared
    after that next call (whether it resolves anything or not), so it only covers the
    immediate "ambiguous -> re-ask with context" pattern, not standalone searches later on.

    Relations (Phase 3): if you pass `expand=True`, the response also includes a
    `related` block -- the 1-hop neighbors (explicit memory_link edges + the
    supersession chain) of the first 3 `results`, deduplicated and capped at 5.
    `related` is NEVER mixed with `results`: they are memories connected by a relation, not
    results of the search itself, so they should not be treated with the same confidence
    of semantic relevance. Each entry carries `relation`, `direction` ("outgoing" if
    the result points to the neighbor, "incoming" if it's the other way around), `virtual` (True
    only for the derived supersession chain, which doesn't live as a real edge) and
    `cross_context` (True if the neighbor belongs to a different context than the one that already
    resolved this search -- only happens via an explicit edge created with
    memory_link, never by coincidence). Useful for enriching a response with
    "this is related to..." without triggering a separate search.

    Args:
        query: the question or text to search for, in natural language.
        context: slug of a context to scope the search to it (recommended if you
            already know which context it is about, or if you're resolving a previous
            ambiguity). If you don't know it, omit it and let the Context Engine decide.
        type: filters by memory type: "semantic" (stable facts/preferences),
            "episodic" (one-off events), "procedural" (how to do something) or
            "decision" (a decision made and its reasoning). Optional.
        limit: maximum number of results to return (default 5).
        expand: if True, adds the `related` block described above (default False).

    Returns:
        dict with `results` (list of memories, empty if `ambiguous` is True),
        `scope_decision` (the raw decision from the API), `ambiguous` (bool, sugar
        over `scope_decision.mode`), `message` (str, present only if `ambiguous` is
        True: text already formatted to decide or show to the user), `note` (str or
        None, confirms when a preference was learned by resolving a
        previous ambiguity) and `related` (list, present only if `expand=True`).
    """
    global _last_disambiguation_id

    try:
        data = _memory.search_memories(query, context=context, type=type, limit=limit, expand=expand)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        return {"error": _http_error_message("cerebro-memory", exc)}

    results = data.get("results", [])
    scope_decision = data.get("scope_decision", {})
    related = data.get("related")
    mode = scope_decision.get("mode")

    # Consume the pending slot in THIS call (whether it's used below or not) so it only
    # covers one following call - see docstring.
    pending_id = _last_disambiguation_id
    _last_disambiguation_id = None

    note: str | None = None
    if context and pending_id:
        try:
            _memory.resolve_disambiguation(pending_id, context)
            note = (
                f"Aprendido: se registro que esta consulta corresponde a '{context}' "
                "-- preguntas similares se inclinaran hacia este contexto en el futuro."
            )
        except (CerebroConnectionError, CerebroAPIError):
            pass  # best-effort: no perdemos el resultado de la busqueda por esto

    if mode == "ambiguous":
        _last_disambiguation_id = scope_decision.get("disambiguation_id")
        out: dict[str, Any] = {
            "results": results,
            "scope_decision": scope_decision,
            "ambiguous": True,
            "message": _format_ambiguous_message(scope_decision),
            "note": note,
        }
        if expand:
            out["related"] = related
        return out

    out = {
        "results": results,
        "scope_decision": scope_decision,
        "ambiguous": False,
        "note": note,
    }
    if expand:
        out["related"] = related
    return out


@mcp.tool()
def memory_remember(
    content: str,
    context: str,
    type: str,  # noqa: A002
    title: str | None = None,
    importance: float | None = None,
) -> dict[str, Any]:
    """Saves a new memory persistently (survives between sessions/restarts).

    Use it when the user shares something worth remembering long-term:
    a fact ("I use Next.js in my project X"), a preference, a decision with its
    reasoning, or a relevant event. Don't use it for ephemeral details of the
    current conversation that have no future value.

    `context` and `type` are REQUIRED -- ambiguity is resolved once, at write time,
    not on every future search. If you don't know which context to use, call
    memory_contexts() first to see the available ones and their descriptions, and pick the
    one that fits best (or create a new one with memory_create_context if none of the
    existing ones truly fit).

    IMPORTANT: never pass real secrets (passwords, API keys, tokens) in
    `content` -- the API rejects them automatically and will ask you to save a
    reference like `secret://environment/name` instead.

    Args:
        content: the memory's text, 1-3 sentences with the fact/event/decision.
        context: slug of an existing context (required).
        type: one of "semantic", "episodic", "procedural", "decision" (required).
        title: optional short title; if omitted, it's derived from the content.
        importance: 0.0-1.0, how important this memory is (optional, default 0.5).

    Returns:
        dict with the memory created (includes its `id`), or `error` with an
        actionable message if something failed (e.g. nonexistent context: lists the
        available contexts and suggests creating one).
    """
    if type not in MEMORY_TYPES:
        return {"error": f"type invalido: '{type}'. Debe ser uno de: {', '.join(MEMORY_TYPES)}."}

    try:
        memory = _memory.create_memory(content, context, type, title=title, importance=importance)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        if exc.status_code == 422 and "unknown context" in str(exc.detail):
            try:
                contexts = _memory.list_contexts()
            except (CerebroConnectionError, CerebroAPIError):
                contexts = []
            return {
                "error": (
                    f"El contexto '{context}' no existe. Contextos disponibles:\n"
                    f"{_format_context_list(contexts)}\n\n"
                    "Elige uno de estos, o crealo primero con "
                    f"memory_create_context(slug='{context}', ...)."
                )
            }
        if exc.status_code == 422:
            return {"error": f"cerebro-memory rechazo la memoria (422): {exc.detail}"}
        return {"error": _http_error_message("cerebro-memory", exc)}

    return {"memory": memory}


@mcp.tool()
def memory_update(memory_id: str, content: str) -> dict[str, Any]:
    """Updates the content of an existing memory when a fact changed.

    cerebro-memory never edits in place: it creates a new memory with the
    updated content and marks the previous one as "superseded", preserving the
    complete history. Use it when something you saved before stopped being true
    (e.g. a rate, a budget, or a system version changed) instead of
    creating a loose new memory that would compete with the old one in searches.

    Args:
        memory_id: UUID of the active memory to replace (the `id` returned by
            memory_search or memory_remember).
        content: the new, correct content.

    Returns:
        dict with the new memory created (`memory`, with its own `id`), or `error`
        if the memory doesn't exist or is no longer active (e.g. archived or already
        superseded previously).
    """
    try:
        memory = _memory.update_memory(memory_id, content)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ninguna memoria con id '{memory_id}'."}
        if exc.status_code == 409:
            return {"error": f"No se pudo actualizar: {exc.detail}"}
        return {"error": _http_error_message("cerebro-memory", exc)}

    return {"memory": memory}


@mcp.tool()
def memory_forget(memory_id: str, hard: bool = False) -> dict[str, Any]:
    """Deletes a memory: by default archives it (recoverable), optionally hard-deletes it.

    Use it when the user explicitly asks to forget something, or when a memory
    became obsolete and should no longer appear in future searches. By default (`hard=False`)
    the memory is archived (stops appearing in normal results, but isn't
    lost). Use `hard=True` only if the user asks for a real, irreversible deletion
    (e.g. because something sensitive was saved by mistake).

    Args:
        memory_id: UUID of the memory to forget.
        hard: if True, hard-deletes the row (irreversible). If False (default),
            only archives it.

    Returns:
        dict with confirmation containing `id`, `hard` and `status`, or `error` if the
        memory doesn't exist.
    """
    try:
        return _memory.delete_memory(memory_id, hard=hard)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ninguna memoria con id '{memory_id}'."}
        return {"error": _http_error_message("cerebro-memory", exc)}


@mcp.tool()
def memory_link(
    from_memory_id: str,
    to_memory_id: str,
    relation: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Creates an explicit, directed relation between two existing memories (lightweight graph).

    cerebro-memory isn't just a list of loose memories: `memory_link` records
    HOW two facts/events/decisions you already saved connect to each other, so that
    `memory_related` and the `related` block of `memory_search` (with `expand=True`)
    can retrieve them together later.

    Relation vocabulary (`relation`, required, one of these 5 -- no
    free text, this is intentional so the graph stays queryable):
      - "relates_to": generic association, with no strong causal or temporal direction.
        Use it when two memories clearly touch each other but none of the other
        four relations fits better.
      - "caused_by": `from_memory_id` was CAUSED BY `to_memory_id`. The typical case:
        a decision (`from`) linked to the reason/event that motivated it (`to`) --
        e.g. "we decided to migrate to Postgres" caused_by "the Mongo provider raised
        prices".
      - "part_of": `from_memory_id` is PART OF `to_memory_id`. The typical case: a
        procedure (`from`) linked to the project it belongs to (`to`) --
        e.g. "how to deploy" part_of "expense-tracker project".
      - "contradicts": `from_memory_id` CONTRADICTS `to_memory_id`. Useful when
        you detect two active memories in conflict that aren't a clear supersession
        (if it is, use memory_update instead of this -- see below).
      - "follows": `from_memory_id` happened AFTER / as a CONSEQUENCE of
        `to_memory_id`, without one having strictly "caused" the other. The
        typical case: an episode (`from`) linked to its later consequence (`to`) --
        e.g. "the server went down" follows "disk space ran out".

    When to link (most common patterns): decisions -> their causes (`caused_by`),
    procedures -> the project they belong to (`part_of`), episodes -> their
    consequences (`follows`). Don't use this tool to version a memory that
    changed (that's `memory_update`, which creates a new version and marks the previous one
    as superseded) -- `memory_link` is for relations between memories that remain
    independent and current.

    Args:
        from_memory_id: UUID of the relation's source memory.
        to_memory_id: UUID of the target memory. Must be different from
            `from_memory_id`.
        relation: one of "relates_to", "caused_by", "part_of", "contradicts",
            "follows" (see above).
        note: optional comment explaining the relation (e.g. why they were linked).

    Returns:
        dict with the edge created (`edge`, includes its `id`), or `error` if the
        vocabulary is invalid, either memory doesn't exist, or the relation already existed
        (same pair + same relation -- it isn't duplicated).
    """
    try:
        edge = _memory.create_edge(from_memory_id, to_memory_id, relation, note=note)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        if exc.status_code == 404:
            return {"error": f"cerebro-memory no encontro alguna de las dos memorias: {exc.detail}"}
        if exc.status_code == 409:
            return {"error": f"Esa relacion ya existe: {exc.detail}"}
        if exc.status_code == 422:
            return {"error": f"Datos invalidos: {exc.detail}"}
        return {"error": _http_error_message("cerebro-memory", exc)}

    return {"edge": edge}


@mcp.tool()
def memory_related(memory_id: str, relation: str | None = None) -> dict[str, Any]:
    """Lists the 1-hop neighbors of a memory: explicit relations + supersession.

    Returns, for `memory_id`, all memories directly connected in
    any direction: both the edges created with `memory_link` and -- automatically,
    without anyone having created them by hand -- the version chain
    (`relation == "supersedes"`) if that memory was replaced by a newer one or
    replaced an older one (see `memory_update`).

    Each entry carries `relation`, `direction` ("outgoing" if `memory_id` is the source
    of that relation, "incoming" if it's the target), `virtual` (True only for the
    derived supersession entries, which aren't a real edge in the database)
    and `memory` (the full neighbor memory).

    Args:
        memory_id: UUID of the memory whose neighbors you want to see.
        relation: filters to a single relation type -- one of "relates_to",
            "caused_by", "part_of", "contradicts", "follows", or "supersedes" (to
            see only the version chain). If omitted, returns everything.

    Returns:
        dict with `related`: list of neighbors (may be empty if the memory has
        no relations), or `error` if the memory doesn't exist or `relation` isn't
        valid.
    """
    try:
        data = _memory.get_related(memory_id, relation=relation)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ninguna memoria con id '{memory_id}'."}
        if exc.status_code == 422:
            return {"error": f"relation invalida: {exc.detail}"}
        return {"error": _http_error_message("cerebro-memory", exc)}

    return {"related": data.get("related", [])}


@mcp.tool()
def memory_timeline(
    context: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Returns a timeline of events and decisions, most recent first.

    Useful for answering questions like "what happened in <context> in the last few
    weeks?" or "what decisions were made in project X within <date range>?" -- it
    combines memories of type "episodic" (one-off events) and "decision" (decisions made),
    ordered by their effective date (`occurred_at` if specified when saving them, otherwise
    `created_at`).

    Args:
        context: slug of a context to scope the timeline to it (optional;
            if omitted, combines events/decisions from all contexts).
        from_date: ISO 8601 date/time (e.g. "2026-07-01" or
            "2026-07-01T00:00:00Z") -- only events with an effective date >= this one.
        to_date: same as `from_date` but as an upper bound (<=).
        limit: maximum number of items to return (default 50).

    Returns:
        dict with `items`: list of memories with their `effective_date` (the date used
        for ordering), most recent first. `error` if `context` doesn't exist or a
        date is invalid.
    """
    try:
        data = _memory.get_timeline(context=context, from_date=from_date, to_date=to_date, limit=limit)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        if exc.status_code == 422:
            return {"error": f"cerebro-memory rechazo la consulta: {exc.detail}"}
        return {"error": _http_error_message("cerebro-memory", exc)}

    return {"items": data.get("items", [])}


@mcp.tool()
def memory_contexts() -> dict[str, Any]:
    """Lists all existing contexts, with their type (`kind`) and description.

    A "context" is a memory's isolation space: a software project, a client, a life
    domain (health, personal finances, learning...). Call this tool:
      - before memory_remember, if you don't know which context something new should go in;
      - when memory_search without `context` returns results from several
        contexts mixed together and you need to decide which is the correct one;
      - when starting to work with a new user, to understand how they organize their
        knowledge.

    Returns:
        dict with `contexts`: list of {id, slug, name, kind, description, created_at}.
    """
    try:
        contexts = _memory.list_contexts()
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        return {"error": _http_error_message("cerebro-memory", exc)}

    return {"contexts": contexts}


@mcp.tool()
def memory_create_context(slug: str, name: str, kind: str, description: str | None = None) -> dict[str, Any]:
    """Creates a new context (project, client, or life domain) to organize memories.

    Needed for "bootstrapping": if you're helping a user start using
    cerebro-memory and none of the existing contexts (check with memory_contexts)
    fits what they want to save, create a new one before calling
    memory_remember. Avoid creating redundant contexts -- check first whether
    something equivalent already exists.

    Args:
        slug: short, stable identifier in lowercase with hyphens, e.g.
            "personal-finances" or "client-acme". Must be unique.
        name: human-readable name, e.g. "Personal finances".
        kind: type of context, e.g. "project", "client" or "domain".
        description: brief description of what kind of information goes in this
            context (helps decide later where to classify new things).

    Returns:
        dict with the context created (`context`), or `error` if the slug already exists.
    """
    try:
        context = _memory.create_context(slug, name, kind, description=description)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        if exc.status_code == 409:
            return {"error": f"Ya existe un contexto con slug '{slug}'."}
        return {"error": _http_error_message("cerebro-memory", exc)}

    return {"context": context}


@mcp.tool()
def memory_stats() -> dict[str, Any]:
    """Shows system statistics: memories, disambiguations, and learned preferences.

    Useful so the user can see the Context Engine (Phase 2) "learn" over time:
    how many ambiguous searches were resolved automatically vs. how many needed
    an agent to choose, and which terms are already associated with which contexts.

    Returns:
        dict with `stats`: {
          memories_by_context: [{context, status, count}, ...],
          disambiguations: {total, auto, agent, user, local_model, unresolved},
          preferences_learned: [{context, term, weight}, ...] (top 100 by weight),
        }, or `error` if something failed.
    """
    try:
        stats = _memory.get_stats()
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-memory", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-memory", exc)}
        return {"error": _http_error_message("cerebro-memory", exc)}

    return {"stats": stats}


# =============================================================================== docs_*


@mcp.tool()
def docs_create_category(
    slug: str, name: str, description: str | None = None, hidden: bool = False, locked: bool = False
) -> dict[str, Any]:
    """Creates a new category to organize complete Markdown documents.

    Same flow as memory_create_context: cerebro-docs organizes documents into
    categories (a formal table, not free text) so they can be redistributed later without
    touching the documents they contain (renaming a category is free for its
    documents, see docs_categories). Needed before docs_save if no
    existing category fits.

    `hidden=True` excludes it from docs_categories()/docs_list()/docs_search() without
    an explicit filter -- it's still reachable by creating/reading documents with its
    exact slug. Use this for internal-reference categories that shouldn't
    appear in a normal listing (e.g. subagent prompts of a flow). If
    you also pass `locked=True`, the category stays hidden FOREVER -- no
    admin will be able to reveal it later (there's no tool for that), so it only makes
    sense for categories that by design should never be browsable. `locked=True`
    without `hidden=True` is invalid.

    Args:
        slug: short, stable identifier in lowercase with hyphens, e.g.
            "ecosystem" or "runbooks". Must be unique.
        name: human-readable name, e.g. "cerebro ecosystem".
        description: brief description of what kind of documents go in this
            category (helps decide later where to save something new).
        hidden: if True, doesn't appear in listings (default False).
        locked: if True, `hidden` stays fixed forever (default False; requires
            `hidden=True`).

    Returns:
        dict with the category created (`category`), or `error` if the slug already exists or
        `locked=True` without `hidden=True`.
    """
    try:
        category = _docs.create_category(slug, name, description=description, hidden=hidden, locked=locked)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 409:
            return {"error": f"Ya existe una categoria con slug '{slug}'."}
        if exc.status_code == 422:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"category": category}


@mcp.tool()
def docs_categories() -> dict[str, Any]:
    """Lists all existing document categories, with their description.

    Call this tool before docs_save if you don't know which category a new
    document should go in, or when docs_search/docs_list return results from several
    categories and you need to decide which is the correct one.

    Returns:
        dict with `categories`: list of {id, slug, name, description, created_at,
        updated_at}.
    """
    try:
        categories = _docs.list_categories()
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"categories": categories}


@mcp.tool()
def docs_save(title: str, content: str, category: str, slug: str | None = None) -> dict[str, Any]:
    """Saves a COMPLETE new Markdown document (not distilled or truncated).

    Unlike memory_remember (which saves distilled facts/decisions in 1-3
    sentences), docs_save saves the entire document as-is -- cerebro-docs is a
    document repository, not formal memory. Use it for guides, definitions,
    runbooks, long notes, or any Markdown the user wants to be able to retrieve
    in full later, instead of summarized.

    `category` is REQUIRED and must be an existing category -- check with
    docs_categories() and create a new one with docs_create_category if none of the
    existing ones truly fit.

    Slug collision: if the slug (given or auto-generated from the title) already exists in that
    category, this tool fails with an explicit error pointing to the
    existing document and suggesting docs_update -- it never auto-suffixes (`-2`, `-3`) nor
    silently overwrites.

    Args:
        title: the document's title.
        content: the complete Markdown, untruncated.
        category: slug of an existing category (required).
        slug: optional short identifier for the document's path
            (`/{category}/{slug}`); if omitted, it's auto-generated from the title.

    Returns:
        dict with the document created (`document`, includes its `id`), or `error` if the
        category doesn't exist or the slug is already in use in that category.
    """
    try:
        document = _docs.create_document(title, content, category, slug=slug)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 404:
            try:
                categories = _docs.list_categories()
            except (CerebroConnectionError, CerebroAPIError):
                categories = []
            return {
                "error": (
                    f"La categoria '{category}' no existe. Categorias disponibles:\n"
                    f"{_format_category_list(categories)}\n\n"
                    "Elige una de estas, o creala primero con "
                    f"docs_create_category(slug='{category}', ...)."
                )
            }
        if exc.status_code == 409:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"document": document}


@mcp.tool()
def docs_get(category: str, slug: str) -> dict[str, Any]:
    """Reads a complete document by its exact path `/{category}/{slug}`.

    Use it when you already know exactly which document you want (e.g. because you
    found it earlier with docs_search/docs_list, or the user gave you both slugs
    directly). If you only have an imprecise reference ("the deployment
    document"), use docs_search instead of this one.

    If the requested path was renamed (the document or its category changed slug), the
    result still brings the document (via internal redirect) but with an `alert` at
    the start asking you to stop using the old path and correct it wherever
    you had it saved -- treat it as an instruction, not just an informational notice.

    Args:
        category: slug of the document's category.
        slug: slug of the document within that category.

    Returns:
        dict with the document (`document`, includes complete `content`), or `error`
        if no document exists at that path.
    """
    try:
        document = _docs.get_document(category, slug)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun documento en '{category}/{slug}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}

    redirected_from = document.get("redirected_from")
    if redirected_from:
        # The real document always wins over a redirect -- this only happens when
        # '{category}/{slug}' no longer has a direct match. The alert is DIRECTED AT
        # THE MODEL, not just informational: it must stop using the old path from now
        # on and, if it had it saved in a memory or document, correct it there
        # too -- not just report it to the user.
        return {
            "alert": (
                f"AVISO: la ruta '{category}/{slug}' que pediste ya no existe -- este documento se movio a "
                f"'{document['category']}/{document['slug']}'. No vuelvas a usar la ruta vieja de aqui en "
                "adelante; si la tenias guardada en una memoria o en otro documento, actualizala a la nueva "
                "antes de terminar."
            ),
            "document": document,
        }

    return {"document": document}


@mcp.tool()
def docs_search(query: str, category: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Searches documents by text (simple full-text, no embeddings) -- for imprecise references.

    Use it when the user refers to a document without giving its exact path (e.g.
    "the document about development x", "the deployment guide") -- it searches in title AND
    content. If you already know the exact category and slug, use docs_get instead of
    this one (it's more direct). Never includes archived documents -- for those, use
    docs_list_archived.

    Args:
        query: text to search for (full-text over title + content).
        category: slug of a category to scope the search to it (optional).
        limit: maximum results per page (default 20, maximum 100).
        offset: how many results to skip, for paging (default 0).

    Returns:
        dict with `documents`: list of documents (each with a relevance `score`),
        or `error` if something failed.
    """
    try:
        documents = _docs.list_documents(category=category, q=query, limit=limit, offset=offset)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 403:
            return {"error": f"No tienes acceso a la categoria '{category}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"documents": documents}


@mcp.tool()
def docs_list(category: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Lists documents, most recent first (no text filter -- see docs_search for that).

    Use it to explore what documents exist in a category, or across the whole
    repository if `category` is omitted. Ordered by `updated_at` descending. Never
    includes archived documents -- for those, use docs_list_archived.

    Args:
        category: slug of a category to scope the listing to it (optional; if
            omitted, lists from all visible categories).
        limit: maximum results per page (default 20, maximum 100).
        offset: how many results to skip, for paging (default 0).

    Returns:
        dict with `documents`: list of documents, or `error` if something failed.
    """
    try:
        documents = _docs.list_documents(category=category, limit=limit, offset=offset)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 403:
            return {"error": f"No tienes acceso a la categoria '{category}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"documents": documents}


@mcp.tool()
def docs_update(document_id: str, title: str, content: str, category: str, slug: str | None = None) -> dict[str, Any]:
    """COMPLETE replacement of an existing document (including moving it between categories).

    cerebro-docs never overwrites without leaving a trace: before applying the replacement,
    it archives a snapshot of the previous content in the version history (no
    restore endpoint in v1 -- it's a safety net against accidental
    overwrites, not a browsable versioning system). To edit only part
    of the document without rewriting it entirely, use docs_patch_section instead of this one.

    Args:
        document_id: UUID of the document to replace.
        title: new title (replaces the previous one).
        content: COMPLETE new Markdown content (replaces the entire previous one).
        category: slug of the destination category -- can be different from the current
            one to move the document (must exist).
        slug: optional new slug; if omitted, keeps the document's current
            slug (still subject to the `(category, slug)` uniqueness constraint).

    Returns:
        dict with the updated document (`document`), or `error` if it doesn't exist, the
        destination category doesn't exist, or the new slug is already in use there.
    """
    try:
        document = _docs.update_document(document_id, title, content, category, slug=slug)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 404:
            return {"error": str(exc.detail)}
        if exc.status_code == 409:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"document": document}


@mcp.tool()
def docs_patch_section(
    document_id: str,
    heading: str,
    operation: str,
    body: str = "",
    create_if_missing: bool = False,
    new_heading_level: int = 2,
) -> dict[str, Any]:
    """PARTIAL patch of a document by section (from a heading down to the next one at the same level or higher).

    Use it to edit only part of a long document without having to reread and
    resend the entire Markdown (that's docs_update). A "section" is everything that
    goes from a heading line (`#`, `##`, ..., `######`) up to the next
    heading at the SAME level or higher (a `##` closes at the next `##` or `#`, but
    never at a `###` nested within it).

    `operation` vocabulary (required, one of these 5 -- no free text):
      - "replace": replaces the BODY of the section (everything that follows the
        heading line, up to the next heading of level <=) with `body`. The
        heading line doesn't change.
      - "append": adds `body` to the end of the section's body, before the
        next heading.
      - "insert_after": inserts `body` (raw Markdown, may bring its own
        heading) as a sibling block right AFTER the complete section (heading
        included).
      - "insert_before": same as insert_after but BEFORE the complete section.
      - "delete": removes the complete section (heading included). `body` is ignored.

    Unique-heading-or-error (same criterion as the Edit tool with `old_string`): the
    `heading` must EXACTLY match the text of a heading in the document (without the
    `#`). If it appears MORE THAN ONCE in the document (ambiguous), this tool ALWAYS
    fails -- it never guesses which of the duplicates is the target, even with
    `create_if_missing=True`. If it doesn't appear AT ALL, it also fails unless you
    pass `create_if_missing=True`, in which case a new section is added at the
    end of the document (at level `new_heading_level`) with `body` as its content -- except
    for `operation="delete"`, where there's nothing to create or delete.

    Like docs_update, it first archives a snapshot of the previous content in the
    version history before applying the patch.

    Args:
        document_id: UUID of the document to patch.
        heading: exact text of the target heading (without the `#`).
        operation: one of "replace", "append", "insert_after", "insert_before",
            "delete" (see above).
        body: Markdown content to insert/use depending on `operation` (ignored for
            "delete").
        create_if_missing: if True and the heading doesn't exist, creates it at the
            end of the document instead of failing (default False).
        new_heading_level: level of the new heading (1-6) if `create_if_missing`
            creates it (default 2, i.e. `##`).

    Returns:
        dict with the updated document (`document`), or `error` if the document
        doesn't exist, the heading is ambiguous, doesn't exist and `create_if_missing` is False, or
        `operation` isn't one of the 5 valid ones.
    """
    if operation not in SECTION_OPERATIONS:
        return {"error": f"operation invalida: '{operation}'. Debe ser una de: {', '.join(SECTION_OPERATIONS)}."}

    try:
        document = _docs.patch_section(
            document_id,
            heading,
            operation,
            body=body,
            create_if_missing=create_if_missing,
            new_heading_level=new_heading_level,
        )
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 404:
            return {"error": str(exc.detail)}
        if exc.status_code == 409:
            return {"error": str(exc.detail)}
        if exc.status_code == 422:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"document": document}


@mcp.tool()
def docs_delete(document_id: str) -> dict[str, Any]:
    """Deletes a document (and, in cascade, its entire version history).

    IRREVERSIBLE -- if what the user wants is to take a document out of circulation
    without losing it forever, use docs_archive instead of this one (equivalent to
    memory_forget: archives by default, doesn't delete). Use docs_delete only when the
    user explicitly asks to delete. If you're unsure which of the two they want,
    confirm before calling either one.

    Args:
        document_id: UUID of the document to delete.

    Returns:
        dict with confirmation containing `id` and `status`, or `error` if the document doesn't exist.
    """
    try:
        return _docs.delete_document(document_id)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun documento con id '{document_id}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}


@mcp.tool()
def docs_archive(document_id: str) -> dict[str, Any]:
    """Archives a document (soft-delete): it stops appearing in docs_list/docs_search,
    but remains accessible by its exact path with docs_get and can be reverted with
    docs_unarchive. cerebro-docs's equivalent of memory_forget -- prefer it over
    docs_delete when the user wants to "get something out of the way" without losing it.

    Args:
        document_id: UUID of the document to archive.

    Returns:
        dict with the updated document (`document`), or `error` if it doesn't exist.
    """
    try:
        document = _docs.archive_document(document_id)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun documento con id '{document_id}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"document": document}


@mcp.tool()
def docs_unarchive(document_id: str) -> dict[str, Any]:
    """Reverts a docs_archive: the document appears again in docs_list/docs_search.

    Args:
        document_id: UUID of the document to unarchive.

    Returns:
        dict with the updated document (`document`), or `error` if it doesn't exist.
    """
    try:
        document = _docs.unarchive_document(document_id)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun documento con id '{document_id}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"document": document}


@mcp.tool()
def docs_list_archived(category: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Lists archived documents (docs_list/docs_search never show them).

    Use it to find something that was archived earlier, or to decide whether to unarchive
    (docs_unarchive) or permanently delete (docs_delete) something old.

    Args:
        category: slug of a category to scope the listing (optional).
        limit: maximum results per page (default 20, maximum 100).
        offset: how many results to skip, for paging (default 0).

    Returns:
        dict with `documents`: list of archived documents, or `error` if something failed.
    """
    try:
        documents = _docs.list_archived_documents(category=category, limit=limit, offset=offset)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 403:
            return {"error": f"No tienes acceso a la categoria '{category}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"documents": documents}


@mcp.tool()
def docs_history(document_id: str) -> dict[str, Any]:
    """Lists the history of previous versions of a document (snapshots
    saved automatically before each docs_update/docs_patch_section).

    Read-only -- there's no automatic restore. To recover content from an
    old version, copy it from the result and save it with docs_update.

    Args:
        document_id: UUID of the document.

    Returns:
        dict with `versions`: list ordered from most recent to oldest (each with
        `version_number`, `category`, `title`, `content`, `created_at`), or `error` if
        the document doesn't exist.
    """
    try:
        versions = _docs.get_document_versions(document_id)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun documento con id '{document_id}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"versions": versions}


# =============================================================================== flow_*


@mcp.tool()
def flow_create_category(slug: str, code: str, name: str, description: str | None = None) -> dict[str, Any]:
    """Creates a new flow category (luisjdev-pendientes/cerebro-flows).

    A cerebro-flows category is NOT the same as a cerebro-docs category
    -- they are independent modules. Here a category is also the prefix of the
    sequential id of its flows: a category with `code="INC"` generates flows
    "INC-1", "INC-2", etc. when saving them without an explicit `code`.

    Args:
        slug: short, stable identifier in lowercase with hyphens, e.g.
            "incident" or "onboarding". Must be unique.
        code: short uppercase prefix for its flows' ids, e.g. "INC".
            Must be unique.
        name: human-readable name, e.g. "Incidents".
        description: brief description of what kind of processes go in this category.

    Returns:
        dict with the category created (`category`), or `error` if the slug or the code already exist.
    """
    try:
        category = _flows.create_category(slug, code, name, description=description)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 409:
            return {"error": f"Ya existe una categoria con slug '{slug}' o code '{code}'."}
        return {"error": _http_error_message("cerebro-flows", exc)}

    return {"category": category}


@mcp.tool()
def flow_categories() -> dict[str, Any]:
    """Lists all existing flow categories, with their `code` (id prefix).

    Call this tool before flow_save if you don't know which category a new flow
    should go in, or if you need a category's `code` to predict the id it will get.

    Returns:
        dict with `categories`: list of {id, slug, code, name, description, created_at, updated_at}.
    """
    try:
        categories = _flows.list_categories()
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        return {"error": _http_error_message("cerebro-flows", exc)}

    return {"categories": categories}


@mcp.tool()
def flow_validate(yaml_content: str) -> dict[str, Any]:
    """Validates a flow-definition YAML WITHOUT saving it -- for iterative authoring.

    Use it while drafting or editing a flow, before committing it with flow_save/
    flow_update: it runs exactly the same validation those two would (referential
    included -- every `next`/`branches`/`checkpoint.on_reject` must point to a real
    step that exists in `procedure`, each step must have exactly one of
    `next`/`branches`/`terminal: true` depending on its type, etc.) and returns the
    specific error (which step, which field) if something is wrong, without touching the database.

    Args:
        yaml_content: the complete YAML of the flow to validate.

    Returns:
        dict `{"valid": true}` if valid, or `error` with the specific detail of the
        first problem found.
    """
    try:
        _flows.validate_flow(yaml_content)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 422:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-flows", exc)}

    return {"valid": True}


@mcp.tool()
def flow_save(category: str, yaml_content: str, code: str | None = None) -> dict[str, Any]:
    """Saves a NEW flow (validated before writing -- same check as flow_validate).

    `category` is REQUIRED and must be an existing category -- check with
    flow_categories() and create a new one with flow_create_category if none of the
    existing ones truly fits. If you omit `code`, it's auto-generated as
    "{category-code}-{next number}" (e.g. "INC-23").

    Args:
        category: slug of an existing category (required).
        yaml_content: the complete YAML of the flow (see flow_validate for the schema).
        code: optional explicit sequential id; if omitted, it's auto-generated.

    Returns:
        dict with the flow created (`flow`), or `error` if the YAML is invalid, the
        category doesn't exist, or the code is already in use.
    """
    try:
        flow = _flows.create_flow(category, yaml_content, code=code)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            try:
                categories = _flows.list_categories()
            except (CerebroConnectionError, CerebroAPIError):
                categories = []
            return {
                "error": (
                    f"La categoria '{category}' no existe. Categorias disponibles:\n"
                    f"{_format_category_list(categories)}\n\n"
                    f"Elige una de estas, o creala primero con flow_create_category(slug='{category}', ...)."
                )
            }
        if exc.status_code in (409, 422):
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-flows", exc)}

    return {"flow": flow}


@mcp.tool()
def flow_get(code: str) -> dict[str, Any]:
    """Reads the complete definition (YAML included) of a flow by its sequential id.

    Do NOT use this to EXECUTE a flow (that's flow_start/flow_next, which reveal one
    step at a time) -- it's for inspecting or editing an existing definition.

    Args:
        code: sequential id of the flow, e.g. "INC-22".

    Returns:
        dict with the flow (`flow`, includes the complete `yaml_content`), or `error` if it doesn't exist.
    """
    try:
        flow = _flows.get_flow(code)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun flujo con code '{code}'."}
        return {"error": _http_error_message("cerebro-flows", exc)}

    return {"flow": flow}


@mcp.tool()
def flow_list(category: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Lists flow definitions, most recent first.

    Args:
        category: slug of a category to scope the listing (optional).
        limit: maximum results per page (default 20, maximum 100).
        offset: how many results to skip, for paging (default 0).

    Returns:
        dict with `flows`: list of flows, or `error` if something failed.
    """
    try:
        flows = _flows.list_flows(category=category, limit=limit, offset=offset)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 403:
            return {"error": f"No tienes acceso a la categoria '{category}'."}
        return {"error": _http_error_message("cerebro-flows", exc)}

    return {"flows": flows}


@mcp.tool()
def flow_update(code: str, yaml_content: str) -> dict[str, Any]:
    """Replaces the YAML of an existing flow, creating a new version (never
    silently overwrites -- the previous version is snapshotted). A run
    already in progress (previous `flow_start`) follows the version it started with, so
    editing a flow never changes the behavior of a run that's already running.

    Args:
        code: sequential id of the flow to update.
        yaml_content: the complete new YAML (replaces the entire previous one).

    Returns:
        dict with the updated flow (`flow`), or `error` if it doesn't exist or the YAML is invalid.
    """
    try:
        flow = _flows.update_flow(code, yaml_content)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun flujo con code '{code}'."}
        if exc.status_code == 422:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-flows", exc)}

    return {"flow": flow}


@mcp.tool()
def flow_delete(code: str) -> dict[str, Any]:
    """Deletes a flow definition (and, in cascade, its version history and its
    recorded runs). Irreversible -- confirm with the user if in doubt.

    Args:
        code: sequential id of the flow to delete.

    Returns:
        dict with confirmation, or `error` if it doesn't exist.
    """
    try:
        return _flows.delete_flow(code)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun flujo con code '{code}'."}
        return {"error": _http_error_message("cerebro-flows", exc)}


@mcp.tool()
def flow_start(code: str) -> dict[str, Any]:
    """Starts a new execution of a flow -- the FIRST step of the execution
    protocol (luisjdev-pendientes/cerebro-flows SS3). NEVER read the complete
    definition with flow_get to "follow" it by hand -- the server reveals one step at a
    time, and that's intentional: you cannot skip a checkpoint you haven't
    seen yet.

    Full protocol:
      1. flow_start(code) -> {run_id, status, step}. Save `run_id`, you need it in
         ALL the following calls.
      2. The first `step` is almost always synthetic (`id: "__prerequisites__"`) with
         `tools_required`: confirm you have those tools (try ToolSearch if
         any is deferred) before continuing. If one is genuinely missing, tell the
         user and do NOT continue.
      3. Call flow_next(run_id) in a loop to advance. If the `step` it returns is
         `type: "decision"`, your NEXT call to flow_next must include `decision`
         with one of the keys from `branches` (the server doesn't infer the condition,
         you report it).
      4. If a `step` carries a `checkpoint`, flow_next blocks until you call
         flow_approve_checkpoint or flow_reject_checkpoint.
      5. `status: "completed"` (with `step: null`) marks a successful end. There's no
         need to recognize any free-text like "end of flow".

    Args:
        code: sequential id of the flow to run, e.g. "INC-22".

    Returns:
        dict with `run_id`, `status` and `step` (the first step), or `error` if the flow doesn't exist.
    """
    try:
        return _flows.start_flow(code)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun flujo con code '{code}'."}
        return {"error": _http_error_message("cerebro-flows", exc)}


@mcp.tool()
def flow_next(run_id: str, decision: str | None = None) -> dict[str, Any]:
    """Advances a run to the next step (see the full protocol in flow_start).

    If the CURRENT step (the one you already saw) is `type: "decision"`, pass `decision` with
    one of its `branches`. If the current step has an unresolved `checkpoint`, this
    tool fails (409) until you call flow_approve_checkpoint/flow_reject_checkpoint.

    Args:
        run_id: the run_id returned by flow_start.
        decision: the branch taken, only if the current step is a decision (see above).

    Returns:
        dict with `status` (`in_progress`|`completed`) and `step` (the next step, or
        `null` if `completed`), or `error`.
    """
    try:
        return _flows.next_step(run_id, decision=decision)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            return {"error": f"run_id '{run_id}' no existe o expiro por inactividad."}
        if exc.status_code in (409, 422):
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-flows", exc)}


@mcp.tool()
def flow_approve_checkpoint(run_id: str) -> dict[str, Any]:
    """Approves the checkpoint of the current step -- unblocks the next flow_next.

    A DELIBERATE call, kept separate from flow_next on purpose (ecosistema-cerebro.md,
    same criterion as memory_forget/docs_archive): approving a checkpoint is the
    highest-consequence action in a flow, never a parameter that can be passed
    "out of habit". If you're unsure whether the user would approve this step,
    confirm with them BEFORE calling this tool.

    Args:
        run_id: the run_id of the execution.

    Returns:
        dict with the updated status, or `error` if the current step has no checkpoint.
    """
    try:
        return _flows.approve_checkpoint(run_id)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            return {"error": f"run_id '{run_id}' no existe o expiro por inactividad."}
        if exc.status_code == 409:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-flows", exc)}


@mcp.tool()
def flow_reject_checkpoint(run_id: str, reason: str) -> dict[str, Any]:
    """Rejects the checkpoint of the current step -- the flow jumps to the step
    defined in `checkpoint.on_reject` in the YAML (e.g. back to review or redo
    a previous step), it doesn't just stop.

    Args:
        run_id: the run_id of the execution.
        reason: reason for the rejection (stays in the run's audit history).

    Returns:
        dict with the new current step (the `on_reject` destination), or `error`.
    """
    try:
        return _flows.reject_checkpoint(run_id, reason)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            return {"error": f"run_id '{run_id}' no existe o expiro por inactividad."}
        if exc.status_code == 409:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-flows", exc)}


@mcp.tool()
def flow_abort(run_id: str, reason: str | None = None) -> dict[str, Any]:
    """Aborts an in-progress run -- marks it as `aborted` (irreversible, unlike
    a rejected checkpoint, which reroutes the flow instead of
    ending it). Use it when the user explicitly decides not to continue with a
    process that already started.

    Args:
        run_id: the run_id of the execution to abort.
        reason: optional reason (stays in the run's audit history).

    Returns:
        dict with confirmation containing `status: "aborted"`, or `error` if the run doesn't exist or
        was already terminated (`completed`/`aborted`).
    """
    try:
        return _flows.abort_run(run_id, reason=reason)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-flows", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-flows", exc)}
        if exc.status_code == 404:
            return {"error": f"run_id '{run_id}' no existe."}
        if exc.status_code == 409:
            return {"error": str(exc.detail)}
        return {"error": _http_error_message("cerebro-flows", exc)}


# =============================================================================== auth_*


@mcp.tool()
def auth_create_user(name: str, email: str | None = None, access_level: str = "user") -> dict[str, Any]:
    """Creates a new identity (human or service) in cerebro-auth.

    Requires an admin-level token: the underlying API enforces this and returns
    an error if the token calling this tool isn't itself admin-level -- expect
    that failure (don't retry with different arguments) if the caller isn't
    admin-level, rather than assuming something else went wrong.

    `access_level` sets this user's own permission tier (e.g. "user", "owner",
    "admin"). Among other things, it's what a token created for this user via
    auth_create_token inherits by default when that call omits its own
    `access_level` and instead passes `user=name`. Creating the user does NOT
    by itself grant access to any module -- that only happens once the user is
    added to a group (auth_add_group_member) whose scopes were defined with
    auth_set_group_scopes.

    Args:
        name: unique identifying name for this user.
        email: optional email address, typically for a human user (omit for a
            service/bot identity).
        access_level: permission tier for this user (default "user").

    Returns:
        dict with the user created (`user`), or `error` if the calling token
        isn't admin-level, or the name is already taken.
    """
    try:
        user = _auth.create_user(name, email=email, access_level=access_level)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-auth", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-auth", exc)}
        return {"error": _http_error_message("cerebro-auth", exc)}

    return {"user": user}


@mcp.tool()
def auth_list_users() -> dict[str, Any]:
    """Lists all users (identities) registered in cerebro-auth.

    Useful before auth_add_group_member or auth_create_token(user=...) to
    confirm the exact name of an existing user, or to audit which identities
    exist and at which `access_level`.

    Returns:
        dict with `users`: list of users (each with at least `name`,
        `access_level` and, if set, `email`), or `error` if something failed.
    """
    try:
        users = _auth.list_users()
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-auth", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-auth", exc)}
        return {"error": _http_error_message("cerebro-auth", exc)}

    return {"users": users}


@mcp.tool()
def auth_create_group(slug: str, name: str) -> dict[str, Any]:
    """Creates a new group -- the unit cerebro-auth uses to grant module access.

    A freshly created group has NO permissions of its own: it's just a named
    bucket. Use auth_set_group_scopes to define what its members can reach, and
    auth_add_group_member to actually put users in it.

    Args:
        slug: short, stable identifier in lowercase with hyphens, e.g.
            "eng-team" or "client-acme". Must be unique.
        name: human-readable name for the group.

    Returns:
        dict with the group created (`group`), or `error` if the slug already exists.
    """
    try:
        group = _auth.create_group(slug, name)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-auth", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-auth", exc)}
        if exc.status_code == 409:
            return {"error": f"Ya existe un grupo con slug '{slug}'."}
        return {"error": _http_error_message("cerebro-auth", exc)}

    return {"group": group}


@mcp.tool()
def auth_set_group_scopes(
    group_slug: str,
    allowed_modules: list[str],
    module_scopes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Defines what every owner-level member of this group can access BY DEFAULT.

    This is the group's actual permission grant, in two layers (the same shape
    used by auth_create_token for an individual token):
      - `allowed_modules` is the COARSE gate: which of "memory"/"docs"/"flows"
        this group's members may touch at all. A module left out stays fully
        inaccessible to them, no matter what `module_scopes` says about it.
      - `module_scopes` is the FINE gate WITHIN each allowed module, e.g.
        `{"memory": {"contexts": ["proyecto-x"]}}` restricts memory access to
        just that one context even though "memory" is in `allowed_modules`.

    This call REPLACES the group's current scopes -- it isn't additive. Every
    owner-level member picks up the new scopes as their default the moment this
    call succeeds (a token already created for them keeps whatever it was
    created with, unless it was set up to fully inherit -- see auth_create_token).

    Args:
        group_slug: slug of an existing group.
        allowed_modules: list of module names this group's members may access
            at all, e.g. ["memory", "docs"].
        module_scopes: optional per-module fine-grained restriction, keyed by
            module name (only meaningful for modules already present in
            `allowed_modules`).

    Returns:
        dict with the updated group (`group`), or `error` if the group doesn't
        exist or a module name is invalid.
    """
    try:
        group = _auth.set_group_scopes(group_slug, allowed_modules, module_scopes=module_scopes)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-auth", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-auth", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun grupo con slug '{group_slug}'."}
        if exc.status_code == 422:
            return {"error": f"cerebro-auth rechazo los scopes: {exc.detail}"}
        return {"error": _http_error_message("cerebro-auth", exc)}

    return {"group": group}


@mcp.tool()
def auth_add_group_member(group_slug: str, user: str) -> dict[str, Any]:
    """Adds an existing user to a group, granting them that group's default scopes.

    Requires an admin-level token: the underlying API enforces this and returns
    an error if the token calling this tool isn't itself admin-level -- expect
    that failure (don't retry with different arguments) if the caller isn't
    admin-level. Once added, an owner-level user inherits whatever
    auth_set_group_scopes defined for this group -- see auth_create_token for
    how that inheritance flows into tokens created for this user afterwards.

    Args:
        group_slug: slug of an existing group.
        user: name of the existing user to add.

    Returns:
        dict with the updated group or membership (`group`), or `error` if the
        calling token isn't admin-level, or the group/user doesn't exist.
    """
    try:
        group = _auth.add_group_member(group_slug, user)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-auth", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-auth", exc)}
        if exc.status_code == 404:
            return {"error": f"El grupo '{group_slug}' o el usuario '{user}' no existen."}
        return {"error": _http_error_message("cerebro-auth", exc)}

    return {"group": group}


@mcp.tool()
def auth_create_token(
    name: str,
    scopes: list[str],
    allowed_modules: list[str] | None = None,
    module_scopes: dict[str, Any] | None = None,
    user: str | None = None,
    access_level: str | None = None,
) -> dict[str, Any]:
    """Creates a new API token -- this is how real access gets handed to another
    identity (a person, an agent, a service). Read this whole docstring before
    calling it, not just the argument names: a token is a live credential, and
    getting its scoping wrong either under- or over-grants access.

    `access_level` vs. `user` -- exactly ONE of the two applies, never both:
      - Pass `access_level` directly to create a token with NO associated user
        (a service/root-adjacent token) -- its permission tier is whatever you
        pass here.
      - Pass `user` (the name of an existing user, see auth_list_users) to
        create a token FOR that user -- its permission tier is inherited from
        that user's own `access_level` (set at auth_create_user time), not
        chosen by this call.
      - Passing BOTH `user` and `access_level` is an error: the tier always
        comes from exactly one of the two, never a mix of both.

    `allowed_modules` / `module_scopes` -- the two-layer access gate, same shape
    as auth_set_group_scopes:
      - `allowed_modules` is the COARSE gate: which of "memory"/"docs"/"flows"
        this token may touch AT ALL. A module left out is fully inaccessible to
        this token, no matter what `module_scopes` says.
      - `module_scopes` is the FINE gate WITHIN each allowed module, e.g.
        `{"memory": {"contexts": ["proyecto-x"]}}` restricts this token's
        memory access to just that one context, even though "memory" is
        present in `allowed_modules`.

    How the two pairs interact:
      - For a token with NO `user` (i.e. `access_level` given directly),
        provide `allowed_modules` explicitly -- there's no group membership to
        inherit from, so omitting it likely means the token can't touch
        anything.
      - For a token tied to an `owner`-level `user`, `allowed_modules` /
        `module_scopes` can be OMITTED ENTIRELY to fully inherit whatever that
        user's groups currently grant (see auth_set_group_scopes) -- this is
        the common case for a token that should simply "be" that user. They can
        also be given explicitly, but only as a NARROWING of what the user's
        groups already allow (e.g. their groups grant memory+docs, but this one
        token should only get memory) -- never as a WIDENING beyond it; the API
        rejects an attempt to widen rather than silently clamping it.

    `scopes` is the action-level grant within cerebro-auth itself (what
    operations this token can perform), independent of the module gates above.

    IMPORTANT: the token's secret value is shown only ONCE, in this call's
    response, and can never be retrieved again afterward by anyone, including
    an admin. If this token is meant for someone or something else, relay the
    value to them right away as part of finishing this task -- don't assume you
    or they can fetch it later; the only recourse at that point is
    auth_revoke_token + creating a new one.

    Args:
        name: unique identifying name for this token.
        scopes: list of action-level scopes this token is granted within
            cerebro-auth.
        allowed_modules: coarse gate -- which modules ("memory", "docs", "flows")
            this token may touch at all. See above for when it may be omitted.
        module_scopes: fine gate within each allowed module, keyed by module
            name. See above for the narrowing-only rule when `user` is given.
        user: name of an existing user this token acts as. Mutually exclusive
            with `access_level`.
        access_level: permission tier for a token with no associated user.
            Mutually exclusive with `user`.

    Returns:
        dict with the token created (`token`, includes the one-time-visible
        `value` -- relay it now), or `error` if both or neither of `user`/
        `access_level` were given, the user doesn't exist, or `allowed_modules`/
        `module_scopes` would widen access beyond what the user's groups allow.
    """
    try:
        token = _auth.create_token(
            name,
            scopes,
            allowed_modules=allowed_modules,
            module_scopes=module_scopes,
            user=user,
            access_level=access_level,
        )
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-auth", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-auth", exc)}
        if exc.status_code == 404:
            return {"error": f"El usuario '{user}' no existe."}
        if exc.status_code == 409:
            return {"error": f"Ya existe un token con nombre '{name}'."}
        if exc.status_code == 422:
            return {"error": f"cerebro-auth rechazo el token: {exc.detail}"}
        return {"error": _http_error_message("cerebro-auth", exc)}

    return {"token": token}


@mcp.tool()
def auth_list_tokens() -> dict[str, Any]:
    """Lists tokens visible to the caller (metadata only -- never the secret value).

    A token's `value` is shown only once, at auth_create_token time -- this
    listing lets you audit what access currently exists (which scopes,
    allowed_modules, module_scopes, and associated user/access_level each token
    has) before deciding whether to auth_revoke_token something stale or
    over-privileged, but it can never recover a lost token value.

    Returns:
        dict with `tokens`: list of tokens (name, scopes, allowed_modules,
        module_scopes, user/access_level, created_at, etc -- no `value`), or
        `error` if something failed.
    """
    try:
        tokens = _auth.list_tokens()
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-auth", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-auth", exc)}
        return {"error": _http_error_message("cerebro-auth", exc)}

    return {"tokens": tokens}


@mcp.tool()
def auth_revoke_token(name: str) -> dict[str, Any]:
    """Revokes a token by name -- immediate and irreversible: whoever was using
    it loses access right away, and a brand-new token (with a new one-time
    value) must be created via auth_create_token to replace it.

    Use it when a token leaked, is no longer needed, or belonged to an
    identity/purpose that shouldn't have access anymore. Confirm with the user
    first if revoking might break something currently in use, unless they
    already asked for this specific token to be revoked.

    Args:
        name: unique name of the token to revoke.

    Returns:
        dict with confirmation, or `error` if no token with that name exists.
    """
    try:
        return _auth.revoke_token(name)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-auth", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-auth", exc)}
        if exc.status_code == 404:
            return {"error": f"No existe ningun token con nombre '{name}'."}
        return {"error": _http_error_message("cerebro-auth", exc)}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
