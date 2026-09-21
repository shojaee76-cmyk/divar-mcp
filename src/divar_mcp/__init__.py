"""divar-mcp: MCP server and client for divar.ir (Iranian classifieds)."""

__version__ = "0.2.0"

from .client import DivarClient, DivarError  # noqa: F401
from .store import Store, get_store  # noqa: F401

__all__ = ["DivarClient", "DivarError", "Store", "get_store", "__version__"]
