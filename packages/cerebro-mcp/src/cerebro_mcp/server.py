"""Servidor MCP unico del ecosistema cerebro (transporte stdio).

Adaptador DELGADO sobre `cerebro_clients` (`MemoryClient`/`DocsClient`): cada tool
llama un metodo del cliente y traduce el resultado -- o la excepcion
(`CerebroAPIError`/`CerebroConnectionError`) -- a algo util para el LLM que la invoca.
No hay logica de negocio aqui (ecosistema-cerebro.md SS10): toda vive en las APIs de
cerebro-memory/cerebro-docs, para que `cerebro-cli` comparta exactamente el mismo
camino via `cerebro_clients`.

Prefijos `memory_*` / `docs_*` (SS10) para no confundir las dos familias de tools --
las `memory_*` son un port 1:1 de las que ya existian en el `mcp_server.py` original
de cerebro-memory (mismos nombres, mismos schemas/descripciones: los agentes del
usuario ya las conocen), y las `docs_*` son nuevas.

Config por variables de entorno -- ver `cerebro_clients.config` (SS4/SS13):
    CEREBRO_MEMORY_URL / CEREBRO_DOCS_URL   URLs base de cada API
    CEREBRO_TOKEN                            token unico compartido entre ambas
    CEREBRO_AGENT_NAME                       identidad de este cliente MCP
    KNOWLEDGEOS_API_URL / KNOWLEDGEOS_API_TOKEN / KNOWLEDGEOS_AGENT_NAME
                                              fallback de compatibilidad (solo memory)

Arranque:
    python -m cerebro_mcp.server
    # o, tras `pip install -e .`, el entry point de consola:
    cerebro-mcp
"""

from __future__ import annotations

from typing import Any

from cerebro_clients import CerebroAPIError, CerebroConnectionError, DocsClient, MemoryClient
from mcp.server.fastmcp import FastMCP

MEMORY_TYPES = ("semantic", "episodic", "procedural", "decision")
SECTION_OPERATIONS = ("replace", "append", "insert_after", "insert_before", "delete")

_memory = MemoryClient()
_docs = DocsClient()

# Process-memory de la ultima desambiguacion *sin resolver* (Fase 2 Context Engine,
# plan_v2.md SS7). Ver el docstring de memory_search() para el comportamiento de
# auto-resolucion que esto habilita. Deliberadamente un solo slot, no una
# pila/historial: solo necesita puentear "busqueda ambigua" -> "la SIGUIENTE busqueda
# del agente con un contexto explicito", que es el patron que produce naturalmente un
# agente que llama tools.
_last_disambiguation_id: str | None = None

mcp = FastMCP(
    name="cerebro",
    instructions=(
        "Ecosistema cerebro para agentes de IA: memoria persistente (memory_*) y "
        "repositorio de documentos Markdown (docs_*). Usa memory_search para "
        "recuperar hechos, decisiones y eventos guardados antes de responder algo "
        "que dependa del historial del usuario; usa memory_remember para guardar "
        "informacion nueva relevante a largo plazo. Toda memoria vive en un "
        "'contexto' (proyecto, cliente, dominio de vida); llama memory_contexts para "
        "ver los contextos disponibles antes de escribir si no estas seguro de cual "
        "usar. Usa docs_save/docs_get/docs_search para documentos Markdown completos "
        "(guias, definiciones, notas largas) que NO deben destilarse a memorias -- "
        "cerebro-docs es un repositorio de documentos, no memoria formal; llama "
        "docs_categories para ver las categorias disponibles antes de guardar."
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
    """Busca memorias guardadas por contenido (retrieval hibrido: vector + texto completo).

    Usala antes de responder cualquier pregunta que pueda depender de algo que el
    usuario ya conto antes (preferencias, decisiones pasadas, hechos sobre su vida o
    sus proyectos) -- es mas confiable que asumir o que revisar el historial de la
    conversacion actual, que se pierde entre sesiones.

    Que es un "contexto": cada memoria pertenece a un contexto (p.ej. un proyecto de
    software, un cliente, un dominio de vida como "salud" o "finanzas-personales").
    Sirve para aislar informacion que NO deberia mezclarse: dos contextos pueden
    compartir vocabulario (p.ej. "gastos" aparece tanto en un proyecto de finanzas
    como en las finanzas personales reales del usuario) sin ser relevantes entre si.

    Scoping automatico (Context Engine, Fase 2): si NO pasas `context`, la busqueda usa
    `scope=auto` en la API. Un scorer barato y determinista (sin LLM) decide si un
    contexto domina claramente:
      - Si domina, la busqueda ya viene filtrada a ese contexto (`scope_decision.mode
        == "auto"`) -- no necesitas hacer nada mas.
      - Si es ambiguo (`scope_decision.mode == "ambiguous"`), `results` viene vacio a
        proposito (para no mezclar memorias de contextos distintos a ciegas) y en su
        lugar recibes `candidates` (2-4 contextos posibles con su descripcion y score)
        y `results_by_candidate` (2-3 resultados reales de cada uno, como evidencia).
        El campo `message` ya trae esto formateado en texto listo para razonar o
        mostrar. Decide tu (con el contexto de la conversacion) o pregunta al usuario,
        y repite la llamada pasando `context=<slug>` del que corresponda.

    Aprendizaje automatico: este servidor MCP recuerda en memoria de proceso el
    `disambiguation_id` de la ultima busqueda ambigua. Si tu SIGUIENTE llamada a
    memory_search pasa `context` explicito, el servidor asume que asi resolviste esa
    ambiguedad y llama automaticamente a `POST /disambiguations/{id}/resolve` con ese
    contexto -- sin que tengas que hacer nada extra. Eso hace crecer
    `context_preferences` en el servidor (los tokens de esa query suman peso hacia el
    contexto elegido), asi que preguntas parecidas en el futuro tienden a resolverse
    solas (`mode == "auto"`) en vez de volver a ser ambiguas. El "slot" se limpia
    despues de esa siguiente llamada (se resuelva o no), asi que solo cubre el patron
    "ambigua -> repregunto con context" inmediato, no busquedas sueltas mas tarde.

    Relaciones (Fase 3): si pasas `expand=True`, la respuesta incluye ademas un bloque
    `related` -- los vecinos a 1 salto (memory_link explicitos + la cadena de
    supersedencia) de los 3 primeros `results`, deduplicados y con un maximo de 5.
    `related` NUNCA se mezcla con `results`: son memorias conectadas por relacion, no
    resultados de la busqueda en si, asi que no deben tratarse con la misma confianza
    de relevancia semantica. Cada entrada trae `relation`, `direction` ("outgoing" si
    el resultado apunta hacia el vecino, "incoming" si es al reves), `virtual` (True
    solo para la cadena de supersedencia derivada, que no vive en una arista real) y
    `cross_context` (True si el vecino pertenece a un contexto distinto del que ya
    resolvio esta busqueda -- solo ocurre via una arista explicita creada con
    memory_link, nunca por casualidad). Util para enriquecer una respuesta con
    "esto esta relacionado con..." sin disparar una busqueda aparte.

    Args:
        query: la pregunta o texto a buscar, en lenguaje natural.
        context: slug de un contexto para acotar la busqueda a el (recomendado si ya
            sabes de que contexto se trata, o si estas resolviendo una ambiguedad
            anterior). Si no lo sabes, omitelo y deja que el Context Engine decida.
        type: filtra por tipo de memoria: "semantic" (hechos/preferencias estables),
            "episodic" (eventos puntuales), "procedural" (como hacer algo) o
            "decision" (una decision tomada y su motivo). Opcional.
        limit: maximo de resultados a devolver (default 5).
        expand: si True, añade el bloque `related` descrito arriba (default False).

    Returns:
        dict con `results` (lista de memorias, vacia si `ambiguous` es True),
        `scope_decision` (la decision cruda de la API), `ambiguous` (bool, azucar
        sobre `scope_decision.mode`), `message` (str, presente solo si `ambiguous` es
        True: texto ya formateado para decidir o mostrar al usuario), `note` (str o
        None, confirma cuando se aprendio una preferencia por resolver una
        ambiguedad anterior) y `related` (lista, solo presente si `expand=True`).
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

    # Consume el slot pendiente en ESTA llamada (se use o no abajo) para que solo
    # cubra una llamada siguiente - ver docstring.
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
    """Guarda una memoria nueva de forma persistente (sobrevive entre sesiones/reinicios).

    Usala cuando el usuario comparta algo que valga la pena recordar a largo plazo:
    un hecho ("uso Next.js en mi proyecto X"), una preferencia, una decision con su
    motivo, o un evento relevante. No la uses para detalles efimeros de la
    conversacion actual que no tienen valor futuro.

    `context` y `type` son OBLIGATORIOS -- la ambiguedad se resuelve una sola vez al
    escribir, no en cada busqueda futura. Si no sabes que contexto usar, llama primero
    a memory_contexts() para ver los disponibles y sus descripciones, y elige el que
    mejor encaje (o crea uno nuevo con memory_create_context si de verdad no existe
    ninguno adecuado).

    IMPORTANTE: nunca pases secretos reales (contraseñas, API keys, tokens) en
    `content` -- la API los rechaza automaticamente y te pedira guardar una
    referencia tipo `secret://entorno/nombre` en su lugar.

    Args:
        content: el texto de la memoria, 1-3 frases con el hecho/evento/decision.
        context: slug de un contexto existente (obligatorio).
        type: uno de "semantic", "episodic", "procedural", "decision" (obligatorio).
        title: titulo corto opcional; si se omite, se deriva del contenido.
        importance: 0.0-1.0, que tan importante es esta memoria (opcional, default 0.5).

    Returns:
        dict con la memoria creada (incluye su `id`), o `error` con un mensaje
        accionable si algo fallo (p.ej. contexto inexistente: lista los contextos
        disponibles y sugiere crear uno).
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
    """Actualiza el contenido de una memoria existente cuando un hecho cambio.

    cerebro-memory nunca edita en el sitio: crea una memoria nueva con el contenido
    actualizado y marca la anterior como "superseded" (reemplazada), preservando el
    historial completo. Usala cuando algo que guardaste antes dejo de ser cierto
    (p.ej. cambio una tarifa, un presupuesto, la version de un sistema) en vez de
    crear una memoria nueva suelta que competiria con la vieja en las busquedas.

    Args:
        memory_id: UUID de la memoria activa a reemplazar (el `id` devuelto por
            memory_search o memory_remember).
        content: el contenido nuevo y correcto.

    Returns:
        dict con la memoria nueva creada (`memory`, con su propio `id`), o `error`
        si la memoria no existe o ya no esta activa (p.ej. archivada o ya
        reemplazada previamente).
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
    """Elimina una memoria: por defecto la archiva (recuperable), opcionalmente la borra en duro.

    Usala cuando el usuario pida explicitamente olvidar algo, o cuando una memoria
    quedo obsoleta y ya no debe aparecer en busquedas futuras. Por defecto (`hard=False`)
    la memoria queda archivada (deja de aparecer en resultados normales, pero no se
    pierde). Usa `hard=True` solo si el usuario pide un borrado real e irreversible
    (p.ej. porque se guardo algo sensible por error).

    Args:
        memory_id: UUID de la memoria a olvidar.
        hard: si True, borra la fila en duro (irreversible). Si False (default),
            solo la archiva.

    Returns:
        dict de confirmacion con `id`, `hard` y `status`, o `error` si la memoria
        no existe.
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
    """Crea una relacion explicita y dirigida entre dos memorias existentes (grafo ligero).

    cerebro-memory no es solo una lista de memorias sueltas: `memory_link` deja constancia
    de COMO se conectan dos hechos/eventos/decisiones que ya guardaste, para que
    `memory_related` y el bloque `related` de `memory_search` (con `expand=True`)
    puedan recuperarlas juntas despues.

    Vocabulario de relaciones (`relation`, obligatorio, uno de estos 5 -- no hay
    texto libre, es a proposito para que el grafo se mantenga consultable):
      - "relates_to": asociacion generica, sin direccion causal ni temporal fuerte.
        Usala cuando dos memorias claramente se tocan pero ninguna de las otras
        cuatro relaciones encaja mejor.
      - "caused_by": `from_memory_id` fue CAUSADO por `to_memory_id`. El caso tipico:
        una decision (`from`) enlazada a la razon/evento que la motivo (`to`) --
        p.ej. "decidimos migrar a Postgres" caused_by "el proveedor de Mongo subio
        precios".
      - "part_of": `from_memory_id` es PARTE de `to_memory_id`. El caso tipico: un
        procedimiento (`from`) enlazado al proyecto al que pertenece (`to`) --
        p.ej. "como hacer deploy" part_of "proyecto expense-tracker".
      - "contradicts": `from_memory_id` CONTRADICE a `to_memory_id`. Util cuando
        detectas dos memorias activas en conflicto que no son una supersedencia clara
        (si si lo es, usa memory_update en vez de esto -- ver mas abajo).
      - "follows": `from_memory_id` ocurrio DESPUES de / como CONSECUENCIA de
        `to_memory_id`, sin que uno haya "causado" estrictamente al otro. El caso
        tipico: un episodio (`from`) enlazado a su consecuencia posterior (`to`) --
        p.ej. "se cayo el servidor" follows "se agoto el disco".

    Cuando enlazar (patrones mas comunes): decisiones -> sus causas (`caused_by`),
    procedimientos -> el proyecto al que pertenecen (`part_of`), episodios -> sus
    consecuencias (`follows`). No uses esta tool para versionar una memoria que
    cambio (eso es `memory_update`, que crea una nueva version y marca la anterior
    como reemplazada) -- `memory_link` es para relaciones entre memorias que siguen
    siendo independientes y vigentes.

    Args:
        from_memory_id: UUID de la memoria de origen de la relacion.
        to_memory_id: UUID de la memoria de destino. Debe ser distinta de
            `from_memory_id`.
        relation: una de "relates_to", "caused_by", "part_of", "contradicts",
            "follows" (ver arriba).
        note: comentario opcional explicando la relacion (p.ej. por que se enlazaron).

    Returns:
        dict con la arista creada (`edge`, incluye su `id`), o `error` si el
        vocabulario es invalido, alguna memoria no existe, o la relacion ya existia
        (mismo par + misma relacion -- no se duplica).
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
    """Lista los vecinos a 1 salto de una memoria: relaciones explicitas + supersedencia.

    Devuelve, para `memory_id`, todas las memorias conectadas directamente en
    cualquier direccion: tanto las aristas creadas con `memory_link` como -- de forma
    automatica, sin que nadie las haya creado a mano -- la cadena de versiones
    (`relation == "supersedes"`) si esa memoria fue reemplazada por otra mas nueva o
    reemplazo a una mas vieja (ver `memory_update`).

    Cada entrada trae `relation`, `direction` ("outgoing" si `memory_id` es el origen
    de esa relacion, "incoming" si es el destino), `virtual` (True solo para las
    entradas de supersedencia derivadas, que no son una arista real en la base de
    datos) y `memory` (la memoria vecina completa).

    Args:
        memory_id: UUID de la memoria cuyos vecinos quieres ver.
        relation: filtra a un solo tipo de relacion -- uno de "relates_to",
            "caused_by", "part_of", "contradicts", "follows", o "supersedes" (para
            ver solo la cadena de versiones). Si se omite, devuelve todo.

    Returns:
        dict con `related`: lista de vecinos (puede estar vacia si la memoria no
        tiene relaciones), o `error` si la memoria no existe o `relation` no es
        valido.
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
    """Devuelve una linea de tiempo de eventos y decisiones, la mas reciente primero.

    Util para responder preguntas tipo "¿que paso en <contexto> las ultimas semanas?"
    o "¿que decisiones se tomaron en el proyecto X en <rango de fechas>?" -- junta
    memorias de tipo "episodic" (eventos puntuales) y "decision" (decisiones tomadas),
    ordenadas por su fecha efectiva (`occurred_at` si se especifico al guardarlas, si
    no `created_at`).

    Args:
        context: slug de un contexto para acotar la linea de tiempo a el (opcional;
            si se omite, junta eventos/decisiones de todos los contextos).
        from_date: fecha/hora ISO 8601 (p.ej. "2026-07-01" o
            "2026-07-01T00:00:00Z") -- solo eventos con fecha efectiva >= esta.
        to_date: igual que `from_date` pero como limite superior (<=).
        limit: maximo de items a devolver (default 50).

    Returns:
        dict con `items`: lista de memorias con su `effective_date` (la fecha usada
        para ordenar), mas reciente primero. `error` si `context` no existe o alguna
        fecha es invalida.
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
    """Lista todos los contextos existentes, con su tipo (`kind`) y descripcion.

    Un "contexto" es el espacio de aislamiento de una memoria: un proyecto de
    software, un cliente, un dominio de vida (salud, finanzas personales,
    aprendizaje...). Llama esta tool:
      - antes de memory_remember, si no sabes en que contexto debe ir algo nuevo;
      - cuando memory_search sin `context` te devuelva resultados de varios
        contextos mezclados y necesites decidir cual es el correcto;
      - al empezar a trabajar con un usuario nuevo, para entender como organiza su
        conocimiento.

    Returns:
        dict con `contexts`: lista de {id, slug, name, kind, description, created_at}.
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
    """Crea un contexto nuevo (proyecto, cliente o dominio de vida) para organizar memorias.

    Necesaria para el "bootstrapping": si estas ayudando a un usuario a empezar a usar
    cerebro-memory y ninguno de los contextos existentes (revisa con memory_contexts)
    encaja con lo que quiere guardar, crea uno nuevo antes de llamar a
    memory_remember. Evita crear contextos redundantes -- revisa primero si ya existe
    algo equivalente.

    Args:
        slug: identificador corto y estable en minusculas con guiones, p.ej.
            "finanzas-personales" o "cliente-acme". Debe ser unico.
        name: nombre legible para humanos, p.ej. "Finanzas personales".
        kind: tipo de contexto, p.ej. "proyecto", "cliente" o "dominio".
        description: descripcion breve de que tipo de informacion va en este
            contexto (ayuda a decidir despues donde clasificar cosas nuevas).

    Returns:
        dict con el contexto creado (`context`), o `error` si el slug ya existe.
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
    """Muestra estadisticas del sistema: memorias, desambiguaciones y preferencias aprendidas.

    Util para que el usuario vea al Context Engine (Fase 2) "aprender" con el tiempo:
    cuantas busquedas ambiguas se resolvieron automaticamente vs. cuantas necesitaron
    que un agente eligiera, y que terminos ya se asociaron a que contextos.

    Returns:
        dict con `stats`: {
          memories_by_context: [{context, status, count}, ...],
          disambiguations: {total, auto, agent, user, local_model, unresolved},
          preferences_learned: [{context, term, weight}, ...] (top 100 por peso),
        }, o `error` si algo fallo.
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
def docs_create_category(slug: str, name: str, description: str | None = None) -> dict[str, Any]:
    """Crea una categoria nueva para organizar documentos Markdown completos.

    Igual flujo que memory_create_context: cerebro-docs organiza documentos en
    categorias (tabla formal, no texto libre) para poder redistribuirlas despues sin
    tocar los documentos que contienen (renombrar una categoria es gratis para sus
    documentos, ver docs_categories). Necesaria antes de docs_save si ninguna
    categoria existente encaja.

    Args:
        slug: identificador corto y estable en minusculas con guiones, p.ej.
            "ecosistema" o "runbooks". Debe ser unico.
        name: nombre legible para humanos, p.ej. "Ecosistema cerebro".
        description: descripcion breve de que tipo de documentos va en esta
            categoria (ayuda a decidir despues donde guardar algo nuevo).

    Returns:
        dict con la categoria creada (`category`), o `error` si el slug ya existe.
    """
    try:
        category = _docs.create_category(slug, name, description=description)
    except CerebroConnectionError as exc:
        return {"error": _connection_error_message("cerebro-docs", exc)}
    except CerebroAPIError as exc:
        if exc.status_code == 401:
            return {"error": _auth_error_message("cerebro-docs", exc)}
        if exc.status_code == 409:
            return {"error": f"Ya existe una categoria con slug '{slug}'."}
        return {"error": _http_error_message("cerebro-docs", exc)}

    return {"category": category}


@mcp.tool()
def docs_categories() -> dict[str, Any]:
    """Lista todas las categorias de documentos existentes, con su descripcion.

    Llama esta tool antes de docs_save si no sabes en que categoria debe ir un
    documento nuevo, o cuando docs_search/docs_list te devuelvan resultados de varias
    categorias y necesites decidir cual es la correcta.

    Returns:
        dict con `categories`: lista de {id, slug, name, description, created_at,
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
    """Guarda un documento Markdown COMPLETO nuevo (sin destilar ni truncar).

    A diferencia de memory_remember (que guarda hechos/decisiones destilados de 1-3
    frases), docs_save guarda el documento integro tal cual -- cerebro-docs es un
    repositorio de documentos, no memoria formal. Usala para guias, definiciones,
    runbooks, notas largas o cualquier Markdown que el usuario quiera poder recuperar
    completo despues, en vez de resumido.

    `category` es OBLIGATORIA y debe ser una categoria existente -- revisa con
    docs_categories() y crea una nueva con docs_create_category si de verdad no
    existe ninguna adecuada.

    Colision de slug: si el slug (dado o autogenerado del titulo) ya existe en esa
    categoria, esta tool falla con un error explicito que apunta al documento
    existente y sugiere docs_update -- nunca auto-sufija (`-2`, `-3`) ni sobrescribe
    en silencio.

    Args:
        title: titulo del documento.
        content: el Markdown completo, sin truncar.
        category: slug de una categoria existente (obligatorio).
        slug: identificador corto opcional para la ruta del documento
            (`/{category}/{slug}`); si se omite, se autogenera del titulo.

    Returns:
        dict con el documento creado (`document`, incluye su `id`), o `error` si la
        categoria no existe o el slug ya esta en uso en esa categoria.
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
    """Lee un documento completo por su ruta exacta `/{category}/{slug}`.

    Usala cuando ya sabes exactamente que documento quieres (p.ej. porque lo
    encontraste antes con docs_search/docs_list, o el usuario te dio ambos slugs
    directamente). Si solo tienes una referencia imprecisa ("el documento del
    despliegue"), usa docs_search en vez de esta.

    Args:
        category: slug de la categoria del documento.
        slug: slug del documento dentro de esa categoria.

    Returns:
        dict con el documento (`document`, incluye `content` completo), o `error`
        si no existe ningun documento en esa ruta.
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

    return {"document": document}


@mcp.tool()
def docs_search(query: str, category: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Busca documentos por texto (full-text simple, sin embeddings) -- para referencias imprecisas.

    Usala cuando el usuario se refiere a un documento sin dar su ruta exacta (p.ej.
    "el documento del desarrollo x", "la guia de despliegue") -- busca en titulo Y
    contenido. Si ya sabes la categoria y el slug exactos, usa docs_get en vez de
    esta (es mas directo).

    Args:
        query: texto a buscar (full-text sobre titulo + contenido).
        category: slug de una categoria para acotar la busqueda a ella (opcional).
        limit: maximo de resultados por pagina (default 20, maximo 100).
        offset: cuantos resultados saltar, para paginar (default 0).

    Returns:
        dict con `documents`: lista de documentos (cada uno con `score` de
        relevancia), o `error` si algo fallo.
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
    """Lista documentos, mas recientes primero (sin filtro de texto -- ver docs_search para eso).

    Usala para explorar que documentos existen en una categoria, o en todo el
    repositorio si se omite `category`. Ordenado por `updated_at` descendente.

    Args:
        category: slug de una categoria para acotar el listado a ella (opcional; si
            se omite, lista de todas las categorias visibles).
        limit: maximo de resultados por pagina (default 20, maximo 100).
        offset: cuantos resultados saltar, para paginar (default 0).

    Returns:
        dict con `documents`: lista de documentos, o `error` si algo fallo.
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
    """Reemplazo COMPLETO de un documento existente (incluye poder moverlo de categoria).

    cerebro-docs nunca sobrescribe sin dejar rastro: antes de aplicar el reemplazo,
    archiva un snapshot del contenido anterior en el historial de versiones (sin
    endpoint de restore en v1 -- es una red de seguridad contra sobrescritura
    accidental, no un sistema de versionado navegable). Para editar solo una parte
    del documento sin reescribirlo entero, usa docs_patch_section en vez de esta.

    Args:
        document_id: UUID del documento a reemplazar.
        title: titulo nuevo (reemplaza el anterior).
        content: contenido Markdown nuevo COMPLETO (reemplaza el anterior entero).
        category: slug de la categoria destino -- puede ser distinta de la actual
            para mover el documento (debe existir).
        slug: slug nuevo opcional; si se omite, conserva el slug actual del
            documento (sujeto igual a la unicidad de `(category, slug)`).

    Returns:
        dict con el documento actualizado (`document`), o `error` si no existe, la
        categoria destino no existe, o el nuevo slug ya esta en uso en ella.
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
    """Parche PARCIAL de un documento por seccion (desde un heading hasta el siguiente del mismo nivel o superior).

    Usala para editar solo una parte de un documento largo sin tener que releer y
    reenviar el Markdown completo (eso es docs_update). Una "seccion" es todo lo que
    va desde una linea de heading (`#`, `##`, ..., `######`) hasta el siguiente
    heading del MISMO nivel o superior (un `##` cierra en el proximo `##` o `#`, pero
    nunca en un `###` anidado dentro de el).

    Vocabulario de `operation` (obligatorio, uno de estos 5 -- no hay texto libre):
      - "replace": sustituye el CUERPO de la seccion (todo lo que sigue a la linea
        del heading, hasta el siguiente heading de nivel <=) por `body`. La linea del
        heading no cambia.
      - "append": agrega `body` al final del cuerpo de la seccion, antes del
        siguiente heading.
      - "insert_after": inserta `body` (Markdown crudo, puede traer su propio
        heading) como bloque hermano justo DESPUES de la seccion completa (heading
        incluido).
      - "insert_before": igual que insert_after pero ANTES de la seccion completa.
      - "delete": elimina la seccion completa (heading incluido). `body` se ignora.

    Heading unico-o-error (mismo criterio que la tool Edit con `old_string`): el
    `heading` debe matchear EXACTO el texto de un heading del documento (sin los
    `#`). Si aparece MAS DE UNA VEZ en el documento (ambiguo), esta tool SIEMPRE
    falla -- nunca adivina cual de los duplicados es el objetivo, incluso con
    `create_if_missing=True`. Si no aparece NINGUNA vez, falla tambien salvo que
    pases `create_if_missing=True`, en cuyo caso se agrega una seccion nueva al
    final del documento (nivel `new_heading_level`) con `body` como cuerpo -- excepto
    para `operation="delete"`, donde no hay nada que crear ni borrar.

    Como docs_update, archiva primero un snapshot del contenido anterior en el
    historial de versiones antes de aplicar el parche.

    Args:
        document_id: UUID del documento a parchear.
        heading: texto exacto del heading objetivo (sin los `#`).
        operation: una de "replace", "append", "insert_after", "insert_before",
            "delete" (ver arriba).
        body: contenido Markdown a insertar/usar segun `operation` (ignorado en
            "delete").
        create_if_missing: si True y el heading no existe, lo crea al final del
            documento en vez de fallar (default False).
        new_heading_level: nivel del heading nuevo (1-6) si `create_if_missing` lo
            crea (default 2, i.e. `##`).

    Returns:
        dict con el documento actualizado (`document`), o `error` si el documento no
        existe, el heading es ambiguo, no existe y `create_if_missing` es False, o
        `operation` no es una de las 5 validas.
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
    """Borra un documento (y, en cascada, todo su historial de versiones).

    Usala cuando el usuario pida explicitamente borrar un documento. No hay
    papelera/soft-delete en v1 -- es irreversible, a diferencia de memory_forget (que
    por defecto solo archiva). Si tienes dudas sobre si el usuario quiere borrar en
    vez de solo editar, confirma antes de llamar esta tool.

    Args:
        document_id: UUID del documento a borrar.

    Returns:
        dict de confirmacion con `id` y `status`, o `error` si el documento no existe.
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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
