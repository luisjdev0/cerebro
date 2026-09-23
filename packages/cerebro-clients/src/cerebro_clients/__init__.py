"""cerebro-clients: thin httpx SDK shared by cerebro-mcp and cerebro-cli.

See `ecosistema-cerebro.md` SS4/SS14: the only legitimate case of code shared between
the clients -- same HTTP calls, two different transports (MCP stdio, CLI).
"""

from cerebro_clients.base import CerebroAPIError, CerebroConnectionError
from cerebro_clients.docs_client import DocsClient
from cerebro_clients.flows_client import FlowsClient
from cerebro_clients.memory_client import MemoryClient

__all__ = [
    "CerebroAPIError",
    "CerebroConnectionError",
    "DocsClient",
    "FlowsClient",
    "MemoryClient",
]
