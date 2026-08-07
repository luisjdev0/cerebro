"""
Adaptador de KnowledgeOS (el sistema propio) contra la API HTTP real.

A diferencia de NaiveKeywordAdapter (índice en RAM) o de un mem0/graphiti/letta
(SDK en proceso), este adaptador habla con `knowledgeos.api` por HTTP -- exactamente
como lo haría el servidor MCP (`knowledgeos.mcp_server`) o cualquier otro cliente
real. Requiere que la API esté corriendo (ver README.md, `python -m knowledgeos.main`)
y que Postgres/pgvector esté arriba (`docker compose up -d`).

Config por variables de entorno (mismos nombres que el servidor MCP, para que un
solo `.env` sirva para todo):
    KNOWLEDGEOS_API_URL     default http://localhost:8000
    KNOWLEDGEOS_API_TOKEN   default "change-me-dev-token" (el default de .env.example)
    KNOWLEDGEOS_SEARCH_SCOPE   "all" (default) | "auto" -- ver más abajo.

## Modo de búsqueda: scope=all (Fase 1, control) vs scope=auto (Context Engine, Fase 2)

`KNOWLEDGEOS_SEARCH_SCOPE=all` (default, compatibilidad con las corridas de Fase 1):
busca SIN filtro de contexto y sin invocar el Context Engine -- mide el retrieval
híbrido puro (vector + full-text), tal como lo haría hoy un agente que todavía no
sabe a qué contexto pertenece la pregunta.

`KNOWLEDGEOS_SEARCH_SCOPE=auto`: pasa `scope=auto`, activando el Context Engine
(plan_v2.md SS7) en la API. Dos casos:
  - La API decide que un contexto domina claramente -> ya filtra por ese contexto
    y este adaptador simplemente usa esos resultados.
  - La API responde `ambiguous` (candidatos + evidencia por candidato, `results`
    vacío a propósito): este adaptador simula un agente razonable **sin leer el
    `contexto_esperado` del caso de evaluación** -- eso sería hacer trampa, ya que el
    Context Engine real no tiene acceso a esa respuesta. La política es: de los
    candidatos devueltos, elegir el que tenga el resultado individual con mejor score
    (`results_by_candidate[slug][0]['score']`), llamar
    `POST /disambiguations/{id}/resolve` con ese contexto (igual que haría el
    servidor MCP), y repetir la búsqueda ya acotada a él para obtener resultados
    reales. Si ningún candidato trae evidencia (no debería pasar en la práctica), cae
    de vuelta al candidato mejor puntuado por el propio engine.

## Supersedencia real (Tarea 4 -- arregla deuda del harness)

Antes, este adaptador insertaba TODAS las memorias del corpus (activas y superseded)
como POSTs independientes, cada una quedando `active` en el sistema real -- la
categoría "temporal" de la suite, con `--include-superseded`, terminaba midiendo un
escenario que KnowledgeOS ni siquiera puede producir (dos memorias "activas" en
conflicto), no su modelo real de supersedencia (`PATCH` crea versión nueva + marca la
vieja `superseded`, y el retrieval por defecto ya la excluye).

Ahora, para los pares marcados en evals/memories.yaml con `superseded_by_id` (los 3
casos: sistema operativo de producción, tarifa por hora de Acme, presupuesto mensual
personal), `_insert_supersedence_chain()` hace lo real: `POST` la versión vieja,
luego `PATCH /memories/{id}` con el contenido de la nueva -- creando la cadena de
supersedencia genuina del sistema. Solo ocurre cuando el runner pasó ambas mitades del
par a `insert()` (o sea, con `--include-superseded`); sin esa flag, el runner filtra
las `superseded` antes de llamar a `insert()` y la memoria "nueva" simplemente se
inserta sola, como cualquier otra activa.
"""

from __future__ import annotations

import os

import httpx
from base import MemoryAdapter

API_URL = os.environ.get("KNOWLEDGEOS_API_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.environ.get("KNOWLEDGEOS_API_TOKEN", "change-me-dev-token")
SEARCH_SCOPE = os.environ.get("KNOWLEDGEOS_SEARCH_SCOPE", "all")

# kind por contexto del corpus (evals/memories.yaml), alineado con la tabla de
# evals/README.md ("proyecto" para expense-tracker/cliente-acme, "dominio" para el
# resto). Cualquier contexto nuevo que aparezca en el corpus y no esté aquí se crea
# igual, con kind="dominio" por defecto.
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


class KnowledgeOSAdapter(MemoryAdapter):
    """Adaptador contra la API real de KnowledgeOS (`src/knowledgeos/api.py`) vía httpx."""

    def __init__(self) -> None:
        self.client: httpx.Client | None = None
        # corpus id (slug del yaml) -> UUID real asignado por la API, y su inverso
        # para poder devolver slugs desde search() como exige MemoryAdapter.search().
        self._uuid_to_slug: dict[str, str] = {}
        # insert() solo almacena; la construcción real (incluida la resolución de
        # pares superseded_by_id, que puede requerir ver ambas mitades del par sin
        # importar el orden en que aparezcan en el corpus) ocurre una sola vez, de
        # forma perezosa, en el primer search() - ver _build_and_flush().
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
                f"KnowledgeOS API no responde en {API_URL}/health ({exc}). "
                "Arráncala primero con `python -m knowledgeos.main` "
                "(y `docker compose up -d` para Postgres)."
            ) from exc

        self._uuid_to_slug = {}
        self._buffered = []
        self._built = False
        self._clean_existing_memories()
        self._ensure_contexts(sorted(CONTEXT_KIND.keys()))

    def _clean_existing_memories(self) -> None:
        """Hard-delete de toda memoria preexistente, para que cada corrida del eval
        empiece desde cero y las métricas sean reproducibles (no hay endpoint
        dedicado de "purgar todo", así que listamos vía búsqueda amplia y borramos
        una por una en duro)."""
        assert self.client is not None
        seen: set[str] = set()
        # Recorre todos los contextos existentes (de una corrida previa) y purga
        # sus memorias, incluidas las superseded (include_superseded=True) para no
        # dejar huérfanas que rompan hard-delete por FK (superseded_by).
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

        # Borra primero las que no son apuntadas por superseded_by de otra activa
        # ya borrada requeriría orden topológico; más simple: reintenta en varias
        # pasadas hasta que no quede nada o deje de progresar (evita el 409 de FK).
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
                break  # no progress, deja de intentar (no debería pasar en la práctica)
            remaining = still_there

        # Nota: esto NO limpia `disambiguation_log` / `context_preferences` (no hay
        # endpoint HTTP para purgarlas y este adaptador se mantiene deliberadamente
        # limitado a HTTP, como cualquier cliente real). Para comparar corridas del
        # benchmark en frío (scope=auto sin aprendizaje acumulado de una corrida
        # previa), trunca esas dos tablas manualmente entre corridas -- ver
        # evals/README.md, sección "Context Engine".

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
            # Corpus trae un contexto no anticipado en CONTEXT_KIND: créalo sobre la
            # marcha con un kind por defecto en vez de fallar.
            self._ensure_contexts([slug])
        self._buffered.append(memory)

    # ------------------------------------------------------------- construcción real

    def _build_and_flush(self) -> None:
        """Materializa todas las memorias bufferadas en la API real, resolviendo
        pares `superseded_by_id` en cadenas de supersedencia genuinas (Tarea 4)."""
        assert self.client is not None

        by_id = {m["id"]: m for m in self._buffered}
        superseded_with_target = {
            m["id"]: m
            for m in self._buffered
            if m.get("status") == "superseded" and m.get("superseded_by_id")
        }
        target_ids = {m["superseded_by_id"] for m in superseded_with_target.values()}

        # 1) Todo lo que no participa en un par superseded_by_id se inserta tal cual.
        for memory in self._buffered:
            if memory["id"] in superseded_with_target or memory["id"] in target_ids:
                continue
            self._insert_plain(memory)

        # 2) Pares reales: POST la vieja, PATCH con el contenido de la nueva.
        for old_id, old in superseded_with_target.items():
            new = by_id.get(old["superseded_by_id"])
            if new is None:
                # La otra mitad del par no está en este run (p.ej. se corrió sin
                # --include-superseded pero igual llegó una superseded suelta, lo
                # cual no debería pasar dado cómo filtra run_eval.py). No la
                # perdemos: se inserta sola en vez de fallar.
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
        """POST la memoria vieja, luego PATCH con el contenido de la nueva: el mismo
        camino que usaría memory_update() en producción. El resultado en la base de
        datos real es old.status='superseded', old.superseded_by=<uuid de new>,
        new.status='active' -- no dos filas activas compitiendo, que era el bug que
        esta tarea corrige (ver docstring del módulo)."""
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
        """Política deliberada (Tarea 5): elige el candidato cuyo TOP resultado
        individual tenga mejor score, SIN mirar `contexto_esperado` del caso de
        evaluación -- el Context Engine real no tiene acceso a esa respuesta, así que
        usarla aquí sería medir un adaptador con trampa. Luego llama
        `POST /disambiguations/{id}/resolve` (igual que haría el servidor MCP al
        detectar que el agente repitió la búsqueda con un contexto explícito) y repite
        la búsqueda ya acotada para obtener resultados reales.

        Nota de calibración: el score RRF del resultado #1 dentro de un candidato es
        casi siempre uno de un puñado de valores discretos (1/(k+1), 2/(k+1)... segun
        en cuántas de las dos listas -vector/fts- cayó en el puesto 1), así que
        empates exactos entre candidatos son el caso COMUN, no la excepción, con un
        `results_by_candidate` de solo 2-3 items. Un agente real que mira esa
        evidencia y ve un empate no tira una moneda: sigue leyendo la señal que ya
        tiene delante -- el score agregado del propio candidato (`candidate['score']`,
        la suma RRF normalizada de TODAS sus memorias en el top preliminar, ya
        calculada por el Context Engine, no derivada de `contexto_esperado`). Por eso
        el criterio real es (top_result_score, candidate_score) en ese orden: el
        resultado individual manda, y el score del candidato solo decide empates.
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
            # Ningún candidato trajo evidencia (no debería pasar en la práctica):
            # cae de vuelta al mejor puntuado por el propio Context Engine.
            best_slug = candidates[0]["slug"]

        if best_slug is None:
            return []

        disambiguation_id = scope_decision.get("disambiguation_id")
        if disambiguation_id:
            resolve_resp = self.client.post(
                f"/disambiguations/{disambiguation_id}/resolve",
                json={"context": best_slug},
            )
            # No es fatal si falla (p.ej. ya fue resuelta) - seguimos con la
            # búsqueda acotada de todas formas para no perder el resultado del caso.
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
