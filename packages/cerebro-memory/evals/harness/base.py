"""
Interfaz común que debe implementar cualquier sistema de memoria evaluado por
la suite de cerebro-memory (evals/harness/run_eval.py).

Vive en su propio módulo (en vez de dentro de run_eval.py) para que los
adaptadores en evals/harness/adapters/ puedan importarla sin crear un import
circular con el runner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MemoryAdapter(ABC):
    """Contrato mínimo entre el harness de evaluación y un sistema de memoria.

    El runner (run_eval.py) llama a estos métodos en este orden exacto para
    cada corrida:

        adapter.setup()
        for memory in memories:
            adapter.insert(memory)
        for case in cases:
            ids = adapter.search(case["query"], k=5)
        adapter.teardown()

    Un adaptador puede envolver un índice en memoria (como NaiveKeywordAdapter),
    una llamada HTTP a un servicio externo, o una librería como mem0/graphiti/letta.
    """

    @abstractmethod
    def setup(self) -> None:
        """Prepara el adaptador antes de insertar memorias.

        Úsalo para abrir conexiones, crear colecciones/índices, autenticar
        contra un servicio, etc. Se llama una sola vez al inicio de la corrida.
        """
        raise NotImplementedError

    @abstractmethod
    def insert(self, memory: dict) -> None:
        """Inserta una memoria en el sistema bajo prueba.

        `memory` es un dict con, como mínimo, las claves del corpus en
        evals/memories.yaml: id, context, type, title, content, status.
        El adaptador decide cómo mapear esos campos a su propio modelo
        (metadata, namespace, colección, etc.).
        """
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, k: int) -> list[str]:
        """Busca `query` y devuelve hasta `k` ids de memoria, mejor resultado primero.

        Debe devolver una lista de los `id` tal como se insertaron (no objetos
        completos), en orden de relevancia descendente. Longitud <= k.
        """
        raise NotImplementedError

    @abstractmethod
    def teardown(self) -> None:
        """Libera recursos al final de la corrida (cerrar conexiones, borrar índices temporales, etc.)."""
        raise NotImplementedError
