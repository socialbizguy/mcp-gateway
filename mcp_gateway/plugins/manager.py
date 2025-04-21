import inspect
import logging
from typing import Any, Dict, List, Optional, Type, TypeVar

from mcp_gateway.plugins.base import (
    Plugin,
    PluginContext,
    GuardrailPlugin,
    TracingPlugin,
)

logger = logging.getLogger(__name__)

# Type variable for plugins
PluginType = TypeVar("PluginType", bound=Plugin)

# Plugin registry to store all registered plugins
_PLUGIN_REGISTRY: Dict[str, List[Type[Plugin]]] = {
    GuardrailPlugin.plugin_type: [],
    TracingPlugin.plugin_type: [],
}


def register_plugin(plugin_cls: Type[PluginType]) -> Type[PluginType]:
    """Decorator for registering plugin classes.

    Args:
        plugin_cls: The plugin class to register

    Returns:
        The original plugin class
    """
    plugin_type = getattr(plugin_cls, "plugin_type", None)

    if not plugin_type:
        logger.warning(
            f"Plugin {plugin_cls.__name__} has no plugin_type. Skipping registration."
        )
        return plugin_cls

    # Add plugin type to registry if not exists
    if plugin_type not in _PLUGIN_REGISTRY:
        _PLUGIN_REGISTRY[plugin_type] = []

    # Register the plugin class
    _PLUGIN_REGISTRY[plugin_type].append(plugin_cls)
    logger.info(f"Registered plugin: {plugin_cls.__name__} (type: {plugin_type})")

    return plugin_cls


class PluginManager:
    """Manages plugins using the Registry Pattern."""

    def __init__(
        self,
        enabled_types: Optional[List[str]] = None,
        enabled_plugins: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """Initializes the PluginManager with configured plugins.

        Args:
            enabled_types: List of plugin types to enable (e.g., ['guardrail', 'tracing']).
                          If None or empty, no plugins will be loaded.
            enabled_plugins: Dictionary mapping plugin types to lists of plugin names to enable
                          (e.g., {'guardrail': ['basic', 'lasso']}).
                          If a type has an empty list or contains 'all', all plugins of that type are enabled.
        """
        self.enabled_types = enabled_types or []
        self.enabled_plugins = enabled_plugins or {}

        # Dictionary to store instantiated plugin objects
        self._plugins: Dict[str, List[Plugin]] = {}

        # Load enabled plugins
        self._load_plugins()

    def _load_plugins(self) -> None:
        """Load and instantiate all enabled plugins from the registry."""
        if not self.enabled_types:
            logger.info("No plugin types enabled.")
            return

        # Initialize plugin containers
        for plugin_type in _PLUGIN_REGISTRY:
            self._plugins[plugin_type] = []

        # Load enabled plugin types
        for plugin_type in self.enabled_types:
            if plugin_type not in _PLUGIN_REGISTRY:
                logger.warning(f"Unknown plugin type: {plugin_type}")
                continue

            # Get enabled plugin names for this type
            enabled_names = self.enabled_plugins.get(plugin_type, [])
            load_all_of_type = not enabled_names or "all" in enabled_names

            # Load plugins of this type
            for plugin_cls in _PLUGIN_REGISTRY[plugin_type]:
                plugin_name = plugin_cls.__name__

                # Skip if not enabled and we're not loading all
                if not load_all_of_type:
                    if plugin_name.lower() not in [p.lower() for p in enabled_names]:
                        logger.debug(
                            f"Skipping plugin {plugin_name} - not explicitly enabled"
                        )
                        continue

                # Instantiate and load the plugin
                try:
                    plugin_instance = plugin_cls()
                    plugin_instance.load({})  # Empty config by default
                    self._plugins[plugin_type].append(plugin_instance)
                    logger.info(f"Loaded plugin: {plugin_name} (type: {plugin_type})")
                except Exception as e:
                    logger.error(
                        f"Failed to load plugin {plugin_name}: {e}", exc_info=True
                    )

        # Log summary of loaded plugins
        for p_type, p_list in self._plugins.items():
            if p_type in self.enabled_types:
                logger.info(f"Loaded {len(p_list)} plugins of type '{p_type}'")

    def get_plugins(self, plugin_type: str) -> List[Plugin]:
        """Returns loaded plugins of a specific type.

        Args:
            plugin_type: Type of plugins to return

        Returns:
            List of loaded plugins of the specified type
        """
        return self._plugins.get(plugin_type, [])

    async def process_request(self, context: PluginContext) -> Optional[Dict[str, Any]]:
        """Processes a request through all relevant plugins.

        Args:
            context: The plugin context containing request information

        Returns:
            The modified arguments after all plugins, or None if blocked
        """
        current_args = context.arguments

        # Run Tracing plugins (for monitoring)
        for plugin in self.get_plugins(TracingPlugin.plugin_type):
            try:
                context_for_plugin = PluginContext(
                    server_name=context.server_name,
                    capability_type=context.capability_type,
                    capability_name=context.capability_name,
                    arguments=current_args,
                    mcp_context=context.mcp_context,
                )

                if inspect.iscoroutinefunction(plugin.process_request):
                    await plugin.process_request(context_for_plugin)
                else:
                    plugin.process_request(context_for_plugin)
            except Exception as e:
                logger.error(
                    f"Error in tracing request plugin {plugin.__class__.__name__}: {e}",
                    exc_info=True,
                )

        # Run Guardrail plugins (can modify or block)
        for plugin in self.get_plugins(GuardrailPlugin.plugin_type):
            if current_args is None:  # If a previous guardrail blocked
                break

            try:
                context_for_plugin = PluginContext(
                    server_name=context.server_name,
                    capability_type=context.capability_type,
                    capability_name=context.capability_name,
                    arguments=current_args,
                    mcp_context=context.mcp_context,
                )

                if inspect.iscoroutinefunction(plugin.process_request):
                    current_args = await plugin.process_request(context_for_plugin)
                else:
                    current_args = plugin.process_request(context_for_plugin)
            except Exception as e:
                logger.error(
                    f"Error in guardrail request plugin {plugin.__class__.__name__}: {e}",
                    exc_info=True,
                )

        return current_args

    async def process_response(self, context: PluginContext) -> Any:
        """Processes a response through all relevant plugins.

        Args:
            context: The plugin context containing response information

        Returns:
            The modified response after all plugins
        """
        current_response = context.response

        # Run Guardrail plugins for response (can modify)
        for plugin in self.get_plugins(GuardrailPlugin.plugin_type):
            try:
                context_for_plugin = PluginContext(
                    server_name=context.server_name,
                    capability_type=context.capability_type,
                    capability_name=context.capability_name,
                    arguments=context.arguments,
                    response=current_response,
                    mcp_context=context.mcp_context,
                )

                if inspect.iscoroutinefunction(plugin.process_response):
                    current_response = await plugin.process_response(context_for_plugin)
                else:
                    current_response = plugin.process_response(context_for_plugin)
            except Exception as e:
                logger.error(
                    f"Error in guardrail response plugin {plugin.__class__.__name__}: {e}",
                    exc_info=True,
                )

        # Run Tracing plugins for response (for monitoring)
        for plugin in self.get_plugins(TracingPlugin.plugin_type):
            try:
                context_for_plugin = PluginContext(
                    server_name=context.server_name,
                    capability_type=context.capability_type,
                    capability_name=context.capability_name,
                    arguments=context.arguments,
                    response=current_response,
                    mcp_context=context.mcp_context,
                )

                if inspect.iscoroutinefunction(plugin.process_response):
                    await plugin.process_response(context_for_plugin)
                else:
                    plugin.process_response(context_for_plugin)
            except Exception as e:
                logger.error(
                    f"Error in tracing response plugin {plugin.__class__.__name__}: {e}",
                    exc_info=True,
                )

        return current_response
