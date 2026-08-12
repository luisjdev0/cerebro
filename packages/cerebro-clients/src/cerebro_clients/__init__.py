"""cerebro-clients: SDK delgado httpx compartido por cerebro-mcp y cerebro-cli.

Ver `ecosistema-cerebro.md` SS4/SS14: unico caso legitimo de codigo compartido entre
los clientes -- mismas llamadas HTTP, dos transportes distintos (MCP stdio, CLI).
"""

from cerebro_clients.base import CerebroAPIError, CerebroConnectionError
from cerebro_clients.docs_client import DocsClient
from cerebro_clients.memory_client import MemoryClient

__all__ = [
    "CerebroAPIError",
    "CerebroConnectionError",
    "DocsClient",
    "MemoryClient",
]
