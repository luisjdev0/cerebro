"""
Memory adapters for the cerebro-memory evaluation suite.

Each adapter implements the `MemoryAdapter` interface defined in
`evals/harness/run_eval.py` and is registered here in `ADAPTERS` under the
name used in the runner's `--adapter` flag.

To add a new adapter:
  1. Create `evals/harness/adapters/<name>.py` (you can copy TEMPLATE.py).
  2. Implement `setup`, `insert`, `search` and `teardown`.
  3. Import it and register it below in `ADAPTERS`.
"""

from __future__ import annotations

from .cerebro_memory_adapter import CerebroMemoryAdapter
from .naive_keyword import NaiveKeywordAdapter

# Name used in `--adapter <name>` -> adapter class (not an instance).
ADAPTERS = {
    "naive": NaiveKeywordAdapter,
    "cerebro-memory": CerebroMemoryAdapter,
}

__all__ = ["ADAPTERS", "NaiveKeywordAdapter", "CerebroMemoryAdapter"]
