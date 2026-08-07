"""Servidor MCP para KnowledgeOS (Fase 1, transporte stdio).

Es un adaptador DELGADO sobre la API HTTP de KnowledgeOS (`knowledgeos.api`): cada
tool hace una llamada httpx al endpoint correspondiente y traduce la respuesta (o el
error) a algo útil para el LLM que la invoca. No hay lógica de negocio aquí -- toda
vive en la API, para que otros clientes (CLI, webhooks, UI) compartan exactamente el
mismo camino (ver plan_v2.md SS6).

Config por variables de entorno:
    KNOWLEDGEOS_API_URL     URL base de la API (default: http://localhost:8000)
    KNOWLEDGEOS_API_TOKEN   token Bearer para autenticar contra la API
    KNOWLEDGEOS_AGENT_NAME  identidad de este cliente MCP (default: "mcp-client"),
                            se envía como header X-Agent-Name en cada request y
                            queda registrada en el audit log y en memory.source.

Arranque:
    python -m knowledgeos.mcp_server
    # o, tras `pip install -e .`, el entry point de consola:
    knowledgeos-mcp
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_URL = os.environ.get("KNOWLEDGEOS_API_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.environ.get("KNOWLEDGEOS_API_TOKEN", "")
AGENT_NAME = os.environ.get("KNOWLEDGEOS_AGENT_NAME", "mcp-client")

MEMORY_TYPES = ("semantic", "episodic", "procedural", "decision")

# Process-memory of the most recent *unresolved* disambiguation (Fase 2 Context
# Engine, plan_v2.md SS7). See memory_search()'s docstring for the auto-resolve
# behavior this enables. Deliberately a single slot, not a stack/history: it only
# needs to bridge "ambiguous search" -> "agent's very next search with an explicit
# context", which is the pattern a tool-calling agent naturally produces.
_last_disambiguation_id: str | None = None

mcp = FastMCP(
    name="knowledgeos",
    instructions=(
        "Memoria persistente self-hosted para agentes de IA. Usa memory_search para "
        "recuperar hechos, decisiones y eventos guardados antes de responder algo que "
        "dependa del historial del usuario; usa memory_remember para guardar "
        "información nueva relevante a largo plazo. Toda memoria vive en un "
        "'contexto' (proyecto, cliente, dominio de vida); llama memory_contexts para "
        "ver los contextos disponibles antes de escribir si no estás seguro de cuál "
        "usar."
    ),
)


def _client() -> httpx.Client:
    headers = {"X-Agent-Name": AGENT_NAME}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    return httpx.Client(base_url=API_URL, headers=headers, timeout=30.0)


def _connection_error_message(exc: httpx.RequestError) -> str:
    return (
        f"No se pudo conectar con la API de KnowledgeOS en {API_URL}: {exc}. "
        f"Verifica que esté corriendo (`python -m knowledgeos.main`) y que "
        f"KNOWLEDGEOS_API_URL apunte a la URL correcta."
    )


def _auth_error_message() -> str:
    return (
        f"La API de KnowledgeOS en {API_URL} rechazó la autenticación (401). "
        "Verifica que la variable de entorno KNOWLEDGEOS_API_TOKEN tenga el mismo "
        "valor que API_TOKEN en el .env de la API."
    )


def _http_error_message(exc: httpx.HTTPStatusError) -> str:
    detail: Any
    try:
        detail = exc.response.json().get("detail", exc.response.text)
    except Exception:
        detail = exc.response.text
    return f"La API de KnowledgeOS devolvió {exc.response.status_code}: {detail}"


def _get_contexts(client: httpx.Client) -> list[dict[str, Any]]:
    resp = client.get("/contexts")
    resp.raise_for_status()
    return resp.json()


def _format_context_list(contexts: list[dict[str, Any]]) -> str:
    if not contexts:
        return "(no hay contextos creados todavía; usa memory_create_context para crear el primero)"
    return "\n".join(f"- {c['slug']} ({c['kind']}): {c.get('description') or 'sin descripción'}" for c in contexts)


# --------------------------------------------------------------------------- tools


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
        "Elige llamando memory_search con context=<slug>, o pregunta al usuario cuál "
        "corresponde."
    )
    return "\n".join(lines)


@mcp.tool()
def memory_search(
    query: str,
    context: str | None = None,
    type: str | None = None,  # noqa: A002 - nombre alineado con la API/plan_v2
    limit: int = 5,
) -> dict[str, Any]:
    """Busca memorias guardadas por contenido (retrieval híbrido: vector + texto completo).

    Úsala antes de responder cualquier pregunta que pueda depender de algo que el
    usuario ya contó antes (preferencias, decisiones pasadas, hechos sobre su vida o
    sus proyectos) -- es más confiable que asumir o que revisar el historial de la
    conversación actual, que se pierde entre sesiones.

    Qué es un "contexto": cada memoria pertenece a un contexto (p.ej. un proyecto de
    software, un cliente, un dominio de vida como "salud" o "finanzas-personales").
    Sirve para aislar información que NO debería mezclarse: dos contextos pueden
    compartir vocabulario (p.ej. "gastos" aparece tanto en un proyecto de finanzas
    como en las finanzas personales reales del usuario) sin ser relevantes entre sí.

    Scoping automático (Context Engine, Fase 2): si NO pasas `context`, la búsqueda usa
    `scope=auto` en la API. Un scorer barato y determinista (sin LLM) decide si un
    contexto domina claramente:
      - Si domina, la búsqueda ya viene filtrada a ese contexto (`scope_decision.mode
        == "auto"`) -- no necesitas hacer nada más.
      - Si es ambiguo (`scope_decision.mode == "ambiguous"`), `results` viene vacío a
        propósito (para no mezclar memorias de contextos distintos a ciegas) y en su
        lugar recibes `candidates` (2-4 contextos posibles con su descripción y score)
        y `results_by_candidate` (2-3 resultados reales de cada uno, como evidencia).
        El campo `message` ya trae esto formateado en texto listo para razonar o
        mostrar. Decide tú (con el contexto de la conversación) o pregunta al usuario,
        y repite la llamada pasando `context=<slug>` del que corresponda.

    Aprendizaje automático: este servidor MCP recuerda en memoria de proceso el
    `disambiguation_id` de la última búsqueda ambigua. Si tu SIGUIENTE llamada a
    memory_search pasa `context` explícito, el servidor asume que así resolviste esa
    ambigüedad y llama automáticamente a `POST /disambiguations/{id}/resolve` con ese
    contexto -- sin que tengas que hacer nada extra. Eso hace crecer
    `context_preferences` en el servidor (los tokens de esa query suman peso hacia el
    contexto elegido), así que preguntas parecidas en el futuro tienden a resolverse
    solas (`mode == "auto"`) en vez de volver a ser ambiguas. El "slot" se limpia
    después de esa siguiente llamada (se resuelva o no), así que solo cubre el patrón
    "ambigua -> repregunto con context" inmediato, no búsquedas sueltas más tarde.

    Args:
        query: la pregunta o texto a buscar, en lenguaje natural.
        context: slug de un contexto para acotar la búsqueda a él (recomendado si ya
            sabes de qué contexto se trata, o si estás resolviendo una ambigüedad
            anterior). Si no lo sabes, omítelo y deja que el Context Engine decida.
        type: filtra por tipo de memoria: "semantic" (hechos/preferencias estables),
            "episodic" (eventos puntuales), "procedural" (cómo hacer algo) o
            "decision" (una decisión tomada y su motivo). Opcional.
        limit: máximo de resultados a devolver (default 5).

    Returns:
        dict con `results` (lista de memorias, vacía si `ambiguous` es True),
        `scope_decision` (la decisión cruda de la API), `ambiguous` (bool, azúcar
        sobre `scope_decision.mode`), `message` (str, presente solo si `ambiguous` es
        True: texto ya formateado para decidir o mostrar al usuario) y `note` (str o
        None, confirma cuando se aprendió una preferencia por resolver una
        ambigüedad anterior).
    """
    global _last_disambiguation_id

    params: dict[str, Any] = {"q": query, "limit": limit}
    if context:
        params["context"] = context
    if type:
        params["type"] = type

    try:
        with _client() as client:
            resp = client.get("/memories/search", params=params)
            if resp.status_code == 401:
                return {"error": _auth_error_message()}
            resp.raise_for_status()
            data = resp.json()
    except httpx.RequestError as exc:
        return {"error": _connection_error_message(exc)}
    except httpx.HTTPStatusError as exc:
        return {"error": _http_error_message(exc)}

    results = data.get("results", [])
    scope_decision = data.get("scope_decision", {})
    mode = scope_decision.get("mode")

    # Consume the pending slot on THIS call (whether or not it gets used below) so it
    # only ever covers one subsequent call - see docstring.
    pending_id = _last_disambiguation_id
    _last_disambiguation_id = None

    note: str | None = None
    if context and pending_id:
        try:
            with _client() as client:
                resolve_resp = client.post(
                    f"/disambiguations/{pending_id}/resolve", json={"context": context}
                )
            if resolve_resp.status_code == 200:
                note = (
                    f"Aprendido: se registró que esta consulta corresponde a '{context}' "
                    "-- preguntas similares se inclinarán hacia este contexto en el futuro."
                )
        except httpx.HTTPError:
            pass  # best-effort: no perdemos el resultado de la búsqueda por esto

    if mode == "ambiguous":
        _last_disambiguation_id = scope_decision.get("disambiguation_id")
        return {
            "results": results,
            "scope_decision": scope_decision,
            "ambiguous": True,
            "message": _format_ambiguous_message(scope_decision),
            "note": note,
        }

    return {
        "results": results,
        "scope_decision": scope_decision,
        "ambiguous": False,
        "note": note,
    }


@mcp.tool()
def memory_remember(
    content: str,
    context: str,
    type: str,  # noqa: A002
    title: str | None = None,
    importance: float | None = None,
) -> dict[str, Any]:
    """Guarda una memoria nueva de forma persistente (sobrevive entre sesiones/reinicios).

    Úsala cuando el usuario comparta algo que valga la pena recordar a largo plazo:
    un hecho ("uso Next.js en mi proyecto X"), una preferencia, una decisión con su
    motivo, o un evento relevante. No la uses para detalles efímeros de la
    conversación actual que no tienen valor futuro.

    `context` y `type` son OBLIGATORIOS -- la ambigüedad se resuelve una sola vez al
    escribir, no en cada búsqueda futura. Si no sabes qué contexto usar, llama primero
    a memory_contexts() para ver los disponibles y sus descripciones, y elige el que
    mejor encaje (o crea uno nuevo con memory_create_context si de verdad no existe
    ninguno adecuado).

    IMPORTANTE: nunca pases secretos reales (contraseñas, API keys, tokens) en
    `content` -- la API los rechaza automáticamente y te pedirá guardar una
    referencia tipo `secret://entorno/nombre` en su lugar.

    Args:
        content: el texto de la memoria, 1-3 frases con el hecho/evento/decisión.
        context: slug de un contexto existente (obligatorio).
        type: uno de "semantic", "episodic", "procedural", "decision" (obligatorio).
        title: título corto opcional; si se omite, se deriva del contenido.
        importance: 0.0-1.0, qué tan importante es esta memoria (opcional, default 0.5).

    Returns:
        dict con la memoria creada (incluye su `id`), o `error` con un mensaje
        accionable si algo falló (p.ej. contexto inexistente: lista los contextos
        disponibles y sugiere crear uno).
    """
    if type not in MEMORY_TYPES:
        return {
            "error": (
                f"type inválido: '{type}'. Debe ser uno de: {', '.join(MEMORY_TYPES)}."
            )
        }

    body: dict[str, Any] = {"content": content, "context": context, "type": type}
    if title:
        body["title"] = title
    if importance is not None:
        body["importance"] = importance

    try:
        with _client() as client:
            resp = client.post("/memories", json=body)
            if resp.status_code == 401:
                return {"error": _auth_error_message()}
            if resp.status_code == 422:
                detail = resp.json().get("detail", "")
                if "unknown context" in str(detail):
                    contexts = _get_contexts(client)
                    return {
                        "error": (
                            f"El contexto '{context}' no existe. Contextos disponibles:\n"
                            f"{_format_context_list(contexts)}\n\n"
                            "Elige uno de estos, o créalo primero con "
                            f"memory_create_context(slug='{context}', ...)."
                        )
                    }
                return {"error": f"KnowledgeOS rechazó la memoria (422): {detail}"}
            resp.raise_for_status()
            memory = resp.json()
    except httpx.RequestError as exc:
        return {"error": _connection_error_message(exc)}
    except httpx.HTTPStatusError as exc:
        return {"error": _http_error_message(exc)}

    return {"memory": memory}


@mcp.tool()
def memory_update(memory_id: str, content: str) -> dict[str, Any]:
    """Actualiza el contenido de una memoria existente cuando un hecho cambió.

    KnowledgeOS nunca edita en el sitio: crea una memoria nueva con el contenido
    actualizado y marca la anterior como "superseded" (reemplazada), preservando el
    historial completo. Úsala cuando algo que guardaste antes dejó de ser cierto
    (p.ej. cambió una tarifa, un presupuesto, la versión de un sistema) en vez de
    crear una memoria nueva suelta que competiría con la vieja en las búsquedas.

    Args:
        memory_id: UUID de la memoria activa a reemplazar (el `id` devuelto por
            memory_search o memory_remember).
        content: el contenido nuevo y correcto.

    Returns:
        dict con la memoria nueva creada (`memory`, con su propio `id`), o `error`
        si la memoria no existe o ya no está activa (p.ej. archivada o ya
        reemplazada previamente).
    """
    try:
        with _client() as client:
            resp = client.patch(f"/memories/{memory_id}", json={"content": content})
            if resp.status_code == 401:
                return {"error": _auth_error_message()}
            if resp.status_code == 404:
                return {"error": f"No existe ninguna memoria con id '{memory_id}'."}
            if resp.status_code == 409:
                detail = resp.json().get("detail", "")
                return {"error": f"No se pudo actualizar: {detail}"}
            resp.raise_for_status()
            memory = resp.json()
    except httpx.RequestError as exc:
        return {"error": _connection_error_message(exc)}
    except httpx.HTTPStatusError as exc:
        return {"error": _http_error_message(exc)}

    return {"memory": memory}


@mcp.tool()
def memory_forget(memory_id: str, hard: bool = False) -> dict[str, Any]:
    """Elimina una memoria: por defecto la archiva (recuperable), opcionalmente la borra en duro.

    Úsala cuando el usuario pida explícitamente olvidar algo, o cuando una memoria
    quedó obsoleta y ya no debe aparecer en búsquedas futuras. Por defecto (`hard=False`)
    la memoria queda archivada (deja de aparecer en resultados normales, pero no se
    pierde). Usa `hard=True` solo si el usuario pide un borrado real e irreversible
    (p.ej. porque se guardó algo sensible por error).

    Args:
        memory_id: UUID de la memoria a olvidar.
        hard: si True, borra la fila en duro (irreversible). Si False (default),
            solo la archiva.

    Returns:
        dict de confirmación con `id`, `hard` y `status`, o `error` si la memoria
        no existe.
    """
    try:
        with _client() as client:
            resp = client.delete(f"/memories/{memory_id}", params={"hard": hard})
            if resp.status_code == 401:
                return {"error": _auth_error_message()}
            if resp.status_code == 404:
                return {"error": f"No existe ninguna memoria con id '{memory_id}'."}
            resp.raise_for_status()
            result = resp.json()
    except httpx.RequestError as exc:
        return {"error": _connection_error_message(exc)}
    except httpx.HTTPStatusError as exc:
        return {"error": _http_error_message(exc)}

    return result


@mcp.tool()
def memory_contexts() -> dict[str, Any]:
    """Lista todos los contextos existentes, con su tipo (`kind`) y descripción.

    Un "contexto" es el espacio de aislamiento de una memoria: un proyecto de
    software, un cliente, un dominio de vida (salud, finanzas personales,
    aprendizaje...). Llama esta tool:
      - antes de memory_remember, si no sabes en qué contexto debe ir algo nuevo;
      - cuando memory_search sin `context` te devuelva resultados de varios
        contextos mezclados y necesites decidir cuál es el correcto;
      - al empezar a trabajar con un usuario nuevo, para entender cómo organiza su
        conocimiento.

    Returns:
        dict con `contexts`: lista de {id, slug, name, kind, description, created_at}.
    """
    try:
        with _client() as client:
            resp = client.get("/contexts")
            if resp.status_code == 401:
                return {"error": _auth_error_message()}
            resp.raise_for_status()
            contexts = resp.json()
    except httpx.RequestError as exc:
        return {"error": _connection_error_message(exc)}
    except httpx.HTTPStatusError as exc:
        return {"error": _http_error_message(exc)}

    return {"contexts": contexts}


@mcp.tool()
def memory_create_context(slug: str, name: str, kind: str, description: str | None = None) -> dict[str, Any]:
    """Crea un contexto nuevo (proyecto, cliente o dominio de vida) para organizar memorias.

    Necesaria para el "bootstrapping": si estás ayudando a un usuario a empezar a usar
    KnowledgeOS y ninguno de los contextos existentes (revisa con memory_contexts)
    encaja con lo que quiere guardar, crea uno nuevo antes de llamar a
    memory_remember. Evita crear contextos redundantes -- revisa primero si ya existe
    algo equivalente.

    Args:
        slug: identificador corto y estable en minúsculas con guiones, p.ej.
            "finanzas-personales" o "cliente-acme". Debe ser único.
        name: nombre legible para humanos, p.ej. "Finanzas personales".
        kind: tipo de contexto, p.ej. "proyecto", "cliente" o "dominio".
        description: descripción breve de qué tipo de información va en este
            contexto (ayuda a decidir después dónde clasificar cosas nuevas).

    Returns:
        dict con el contexto creado (`context`), o `error` si el slug ya existe.
    """
    body: dict[str, Any] = {"slug": slug, "name": name, "kind": kind}
    if description is not None:
        body["description"] = description

    try:
        with _client() as client:
            resp = client.post("/contexts", json=body)
            if resp.status_code == 401:
                return {"error": _auth_error_message()}
            if resp.status_code == 409:
                return {"error": f"Ya existe un contexto con slug '{slug}'."}
            resp.raise_for_status()
            context = resp.json()
    except httpx.RequestError as exc:
        return {"error": _connection_error_message(exc)}
    except httpx.HTTPStatusError as exc:
        return {"error": _http_error_message(exc)}

    return {"context": context}


@mcp.tool()
def memory_stats() -> dict[str, Any]:
    """Muestra estadísticas del sistema: memorias, desambiguaciones y preferencias aprendidas.

    Útil para que el usuario vea al Context Engine (Fase 2) "aprender" con el tiempo:
    cuántas búsquedas ambiguas se resolvieron automáticamente vs. cuántas necesitaron
    que un agente eligiera, y qué términos ya se asociaron a qué contextos.

    Returns:
        dict con `stats`: {
          memories_by_context: [{context, status, count}, ...],
          disambiguations: {total, auto, agent, user, unresolved},
          preferences_learned: [{context, term, weight}, ...] (top 100 por peso),
        }, o `error` si algo falló.
    """
    try:
        with _client() as client:
            resp = client.get("/stats")
            if resp.status_code == 401:
                return {"error": _auth_error_message()}
            resp.raise_for_status()
            stats = resp.json()
    except httpx.RequestError as exc:
        return {"error": _connection_error_message(exc)}
    except httpx.HTTPStatusError as exc:
        return {"error": _http_error_message(exc)}

    return {"stats": stats}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
