# MCP Gateway Plugin System

The MCP Gateway includes a flexible plugin system that allows for extending functionality through custom plugins. This document explains how to create and use plugins with the gateway.

## Plugin Types

The gateway supports two main types of plugins:

1. **Guardrail Plugins**: These plugins can modify or block requests and responses based on security or compliance rules.
2. **Tracing Plugins**: These plugins observe requests and responses for logging, monitoring, or auditing purposes.

## Creating Custom Plugins

To create a custom plugin, follow these steps:

1. Create a new Python file in an appropriate location
2. Import the required base classes and the registration decorator
3. Extend the appropriate base class (`GuardrailPlugin` or `TracingPlugin`)
4. Apply the `@register_plugin` decorator to your class
5. Implement the required methods
6. Set the `plugin_name` attribute for easy identification

### Example Plugin

```python
from typing import Any, Dict, Optional
from mcp_gateway.plugins.base import GuardrailPlugin, PluginContext
from mcp_gateway.plugins.manager import register_plugin

@register_plugin
class MyCustomGuardrailPlugin(GuardrailPlugin):
    """A custom guardrail plugin that does something useful."""
    
    plugin_name = "my-custom"  # Used for identification in configuration
    
    def __init__(self):
        # Initialize your plugin
        self.config = {}
        
    def load(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Load plugin configuration."""
        # Handle configuration here
        self.config = config or {}
        
    def process_request(self, context: PluginContext) -> Optional[Dict[str, Any]]:
        """Process the request."""
        # Modify or validate request arguments
        return context.arguments
        
    def process_response(self, context: PluginContext) -> Any:
        """Process the response."""
        # Modify or validate the response
        return context.response
```

## Plugin Registry Pattern

The MCP Gateway uses the Registry Pattern with decorator-based registration for plugins. This approach offers several benefits:

1. **Self-Registration**: Plugins register themselves when imported, eliminating the need for manual registration
2. **No Filesystem Scanning**: The system doesn't depend on directory structure for plugin discovery
3. **Type Safety**: Better IDE support and error checking through type hints
4. **Explicit Dependencies**: Registration makes dependencies clear and explicit
5. **Extensibility**: New plugin types can be added without modifying the manager

### How It Works

1. The `@register_plugin` decorator adds plugin classes to a central registry
2. The `PluginManager` loads plugins from this registry based on configuration
3. Plugins are instantiated only when enabled in the configuration
4. Processing flows through enabled plugins based on their type

## Plugin Configuration

Plugins are configured and loaded through command-line arguments or configuration files.

### Command-line Example

```bash
python -m mcp_gateway.server --enable-guardrails my-custom,example_guardrail --enable-tracing simple_timing
```

### JSON Configuration Example

```json
{
  "mcpServers": {
    "mcp-gateway": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/mcp-proxy",
        "run",
        "mcp_gateway/server.py",
        "--enable-guardrails", "my-custom,example_guardrail",
        "--enable-tracing", "simple_timing"
      ],
      "env": {
        "PYTHONPATH": "/path/to/mcp-proxy"
      }
    }
  }
}
```

## Plugin Loading Process

1. The `PluginManager` is initialized with configuration (enabled types and plugins)
2. It references the central registry to find registered plugin classes
3. For each enabled plugin type, it instantiates and loads the specified plugins
4. Plugins are then used to process requests and responses as they flow through the system

## Example Plugins

The gateway includes several example plugins:

- **`example_guardrail`**: A basic guardrail plugin demonstrating the registration pattern
- **`simple_timing`**: A tracing plugin that logs request/response timing information
