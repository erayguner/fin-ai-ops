"""Custom MCP Server for FinOps Automation Hub.

Provides MCP tools for:
- Policy management (CRUD, evaluation)
- Alert management (query, acknowledge, resolve)
- Report generation and retrieval
- Audit trail querying and compliance export
- Cost monitoring control (start, stop, status)
"""

from .server import MCP_TOOLS, handle_tool_call, list_tools

__all__ = ["MCP_TOOLS", "handle_tool_call", "list_tools"]
