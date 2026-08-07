"""
Adaptadores de memoria para la suite de evaluación de KnowledgeOS.

Cada adaptador implementa la interfaz `MemoryAdapter` definida en
`evals/harness/run_eval.py` y se registra aquí en `ADAPTERS` bajo el nombre
que se usa en la flag `--adapter` del runner.

Para añadir un adaptador nuevo:
  1. Crea `evals/harness/adapters/<nombre>.py` (puedes copiar TEMPLATE.py).
  2. Implementa `setup`, `insert`, `search` y `teardown`.
  3. Impórtalo y regístralo abajo en `ADAPTERS`.
"""

from __future__ import annotations

from .knowledgeos_adapter import KnowledgeOSAdapter
from .naive_keyword import NaiveKeywordAdapter

# Nombre usado en `--adapter <nombre>` -> clase del adaptador (no instancia).
ADAPTERS = {
    "naive": NaiveKeywordAdapter,
    "knowledgeos": KnowledgeOSAdapter,
}

__all__ = ["ADAPTERS", "NaiveKeywordAdapter", "KnowledgeOSAdapter"]
