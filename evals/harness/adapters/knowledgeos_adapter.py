"""
Adaptador de KnowledgeOS (el sistema propio, Fase 1) contra la API HTTP real.

A diferencia de NaiveKeywordAdapter (índice en RAM) o de un mem0/graphiti/letta
(SDK en proceso), este adaptador habla con `knowledgeos.api` por HTTP -- exactamente
como lo haría el servidor MCP (`knowledgeos.mcp_server`) o cualquier otro cliente
real. Requiere que la API esté corriendo (ver README.md, `python -m knowledgeos.main`)
y que Postgres/pgvector esté arriba (`docker compose up -d`).

Config por variables de entorno (mismos nombres que el servidor MCP, para que un
solo `.env` sirva para todo):
    KNOWLEDGEOS_API_URL     default http://localhost:8000
    KNOWLEDGEOS_API_TOKEN   default "change-me-dev-token" (el default de .env.example)

Nota deliberada sobre `search()`: la Fase 1 no hace scoping automático de contexto
(eso es la Fase 2, el "Context Engine" de plan_v2.md SS7). Este adaptador busca SIN
pasar `context`, tal como lo haría hoy un agente que todavía no sabe a qué contexto
pertenece la pregunta del usuario -- así medimos el retrieval híbrido (vector +
full-text) puro, sin la ayuda de un filtro que en producción el agente todavía no
tiene forma de aplicar de antemano.
"""

from __future__ import annotations

import os

import httpx
from base import MemoryAdapter

API_URL = os.environ.get("KNOWLEDGEOS_API_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.environ.get("KNOWLEDGEOS_API_TOKEN", "change-me-dev-token")

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
                params={"q": "a", "context": ctx["slug"], "limit": 50, "include_superseded": True},
            )
            search_resp.raise_for_status()
            for mem in search_resp.json():
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
        assert self.client is not None
        slug = memory["context"]
        if slug not in CONTEXT_KIND:
            # Corpus trae un contexto no anticipado en CONTEXT_KIND: créalo sobre la
            # marcha con un kind por defecto en vez de fallar.
            self._ensure_contexts([slug])

        resp = self.client.post(
            "/memories",
            json={
                "content": memory["content"],
                "context": slug,
                "type": memory["type"],
                "title": memory.get("title"),
            },
        )
        resp.raise_for_status()
        created = resp.json()
        self._uuid_to_slug[created["id"]] = memory["id"]

    def search(self, query: str, k: int) -> list[str]:
        assert self.client is not None
        # Deliberadamente SIN `context`: ver docstring del módulo. Fase 1 mide
        # retrieval puro, el scoping automático es Fase 2.
        resp = self.client.get(
            "/memories/search",
            params={"q": query, "limit": k},
        )
        resp.raise_for_status()
        results = resp.json()
        return [self._uuid_to_slug[r["id"]] for r in results if r["id"] in self._uuid_to_slug][:k]

    def teardown(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
