"""Tracing plugins for MCP Gateway.

These plugins help monitor system activity by logging requests and responses.
"""

# Import all plugins to ensure they register
from mcp_gateway.plugins.tracing.basic import BasicTracingPlugin
from mcp_gateway.plugins.tracing.xetrack import XetrackTracingPlugin
from mcp_gateway.plugins.tracing.example import SimpleTimingPlugin

__all__ = [
    "BasicTracingPlugin",
    "XetrackTracingPlugin",
    "SimpleTimingPlugin",
]
