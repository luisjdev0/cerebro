"""
Esqueleto de adaptador para conectar un sistema de memoria real
(mem0, graphiti, letta, o el propio cerebro-memory) a la suite de evaluación.

CÓMO USAR ESTE ARCHIVO
-----------------------
1. Copia este archivo a `evals/harness/adapters/<nombre_sistema>.py`
   (p.ej. `mem0.py`, `graphiti.py`, `letta.py`).
2. Renombra la clase `TemplateAdapter` a algo como `Mem0Adapter`.
3. Rellena los TODOs de cada método con las llamadas reales a la API/SDK
   del sistema.
4. Regístralo en `evals/harness/adapters/__init__.py`:

       from .mem0 import Mem0Adapter
       ADAPTERS = {
           "naive": NaiveKeywordAdapter,
           "mem0": Mem0Adapter,
       }

5. Corre `python evals/harness/run_eval.py --adapter mem0`.

NO implementes lógica real en ESTE archivo (TEMPLATE.py) — es solo el
esqueleto de referencia. No se registra en ADAPTERS ni se ejecuta.
"""

from __future__ import annotations

from base import MemoryAdapter


class TemplateAdapter(MemoryAdapter):
    """Reemplaza este docstring con una descripción del sistema real
    (qué API/SDK usa, si corre local o remoto, requisitos de auth, etc.)."""

    def __init__(self) -> None:
        # TODO: guarda aquí config del cliente (api_key, base_url, project_id,
        # nombre de colección/namespace a usar para este run, etc.). No
        # hardcodees credenciales: léelas de variables de entorno.
        #
        # Ejemplo (mem0):
        #   self.client = None  # se crea en setup()
        #   self.user_id = "eval-run"  # namespace/aislamiento para no
        #                               # mezclar con memorias reales
        #
        # Ejemplo (graphiti):
        #   self.graphiti = None
        #   self.group_id = "eval-run"
        #
        # Ejemplo (letta):
        #   self.client = None
        #   self.agent_id = None
        pass

    def setup(self) -> None:
        """Inicializa el cliente/conexión y deja el sistema listo para insertar.

        TODO:
          - mem0: instanciar `Memory()` o `MemoryClient(api_key=...)`.
          - graphiti: instanciar `Graphiti(neo4j_uri, user, password)` y
            llamar a `build_indices_and_constraints()` si aplica.
          - letta: crear cliente `Letta(...)` y, si el adaptador prueba
            memoria de un agente, crear/recuperar el agente de evaluación.

        Importante: si el sistema persiste datos entre corridas, usa un
        namespace/colección dedicado a evals (ver `self.user_id` /
        `self.group_id` arriba) para no contaminar memorias reales ni
        arrastrar resultados de una corrida anterior.
        """
        raise NotImplementedError("TODO: inicializar el cliente del sistema real")

    def insert(self, memory: dict) -> None:
        """Inserta una memoria del corpus (evals/memories.yaml) en el sistema real.

        `memory` trae: id, context, type, title, content, status (y cualquier
        otro campo que se añada al corpus más adelante).

        TODO:
          - Decide cómo mapear `context` (p.ej. "cliente-acme") al concepto
            de aislamiento del sistema (namespace, tag, subgrafo, agent_id...).
          - Decide cómo mapear `type` (semantic/episodic/procedural/decision)
            si el sistema distingue tipos de memoria.
          - Guarda `memory["id"]` como metadata para poder devolverlo tal
            cual en `search()` — el harness compara por ese id, no por texto.

        Ejemplo (mem0, muy simplificado):
            self.client.add(
                memory["content"],
                user_id=self.user_id,
                metadata={"eval_id": memory["id"], "context": memory["context"]},
            )
        """
        raise NotImplementedError("TODO: insertar la memoria en el sistema real")

    def search(self, query: str, k: int) -> list[str]:
        """Busca `query` en el sistema real y devuelve hasta `k` ids del corpus.

        TODO:
          - Llama al endpoint/método de búsqueda del sistema (p.ej.
            `self.client.search(query, user_id=self.user_id, limit=k)`).
          - Extrae `eval_id` de la metadata de cada resultado (el id que
            guardaste en `insert()`), NO el id interno del sistema.
          - Devuelve la lista en orden de relevancia descendente, largo <= k.

        Si el sistema real no soporta un top-k exacto, trunca aquí.
        """
        raise NotImplementedError("TODO: buscar y mapear resultados a ids del corpus")

    def teardown(self) -> None:
        """Limpia el estado creado en setup()/insert() para dejar el sistema limpio.

        TODO:
          - Borra el namespace/colección/grupo de evaluación si el sistema
            persiste en disco o en un servicio remoto (para que la próxima
            corrida empiece desde cero y las métricas sean reproducibles).
          - Cierra conexiones (drivers de DB, sesiones HTTP, etc.).
        """
        raise NotImplementedError("TODO: limpiar datos de la corrida y cerrar conexiones")
