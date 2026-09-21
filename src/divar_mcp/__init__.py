"""divar-mcp: MCP server and client for divar.ir (Iranian classifieds)."""

__version__ = "0.1.0"

from .client import DivarClient, DivarError  # noqa: F401

__all__ = ["DivarClient", "DivarError", "__version__"]
