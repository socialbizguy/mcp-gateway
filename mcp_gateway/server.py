# server.py
import asyncio
import logging
import os
import json
import argparse
import sys
from contextlib import asynccontextmanager, AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, AsyncIterator, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP, Context
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from mcp_gateway.config import load_config
from mcp_gateway.sanitizers import (
    SanitizationError,
    sanitize_tool_call_args,
    sanitize_tool_call_result,
    sanitize_resource_read,
    sanitize_response,
)
from mcp_gateway.plugins.manager import PluginManager

# --- Global Config for Args ---
cli_args = None
log_level = os.environ.get("LOGLEVEL", "INFO").upper()

# Configure logging
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Server:
    """Manages the connection and interaction with a single proxied MCP server."""

    def __init__(self, name: str, config: Dict[str, Any]):
        """Initializes the Proxied Server.

        Args:
            name: The unique name identifier for this server.
            config: The configuration dictionary for this server (command, args, env).
        """
        self.name = name
        self.config = config
        self._session: Optional[ClientSession] = None
        self._client_cm: Optional[
            AsyncIterator[Tuple[asyncio.StreamReader, asyncio.StreamWriter]]
        ] = None
        self._server_info: Optional[types.InitializeResult] = None
        self._exit_stack = AsyncExitStack()
        # Store fetched capabilities for easier access later
        self._tools: List[types.Tool] = []
        self._resources: List[types.Resource] = []
        self._prompts: List[types.Prompt] = []
        logger.info(f"Initialized Proxied Server: {self.name}")

    @property
    def session(self) -> ClientSession:
        """Returns the active ClientSession, raising an error if not started."""
        if self._session is None:
            raise RuntimeError(f"Server '{self.name}' session not started.")
        return self._session

    async def start(self) -> None:
        """Starts the underlying MCP server process, establishes a client session,
        and fetches initial capabilities."""
        if self._session is not None:
            logger.warning(f"Server '{self.name}' already started.")
            return

        logger.info(f"Starting proxied server: {self.name}...")
        try:
            server_params = StdioServerParameters(
                command=self.config.get("command", ""),
                args=self.config.get("args", []),
                env=self.config.get("env", None),
            )

            # Use AsyncExitStack to manage the stdio_client context
            self._client_cm = stdio_client(server_params)
            read, write = await self._exit_stack.enter_async_context(self._client_cm)

            # Use AsyncExitStack to manage the ClientSession context
            session_cm = ClientSession(read, write)
            self._session = await self._exit_stack.enter_async_context(session_cm)

            # Capture and store the InitializeResult
            self._server_info = await self._session.initialize()
            logger.info(
                f"Proxied server '{self.name}' started and initialized successfully."
            )

            # Fetch and store initial lists of tools, resources, prompts
            await self._fetch_initial_capabilities()

        except Exception as e:
            logger.error(f"Failed to start server '{self.name}': {e}", exc_info=True)
            self._server_info = None  # Ensure server_info is None on failure
            await self.stop()  # Attempt cleanup if start failed
            raise

    async def _fetch_initial_capabilities(self):
        """Fetches and stores the initial lists of tools, resources, and prompts."""
        if not self.session:
            logger.warning(
                f"Cannot fetch capabilities for {self.name}, session inactive."
            )
            return

        try:
            # Fetch tools, resources, prompts simultaneously
            tools_res, resources_res, prompts_res = await asyncio.gather(
                self.session.list_tools(),
                self.session.list_resources(),
                self.session.list_prompts(),
                return_exceptions=True,
            )

            # Process Tools
            if isinstance(tools_res, Exception):
                logger.error(f"Failed to list tools for {self.name}: {tools_res}")
                self._tools = []
            else:
                self._tools = self._extract_list(tools_res, "tools", types.Tool)

            # Process Resources
            if isinstance(resources_res, Exception):
                logger.error(
                    f"Failed to list resources for {self.name}: {resources_res}"
                )
                self._resources = []
            else:
                self._resources = self._extract_list(
                    resources_res, "resources", types.Resource
                )

            # Process Prompts
            if isinstance(prompts_res, Exception):
                logger.error(f"Failed to list prompts for {self.name}: {prompts_res}")
                self._prompts = []
            else:
                self._prompts = self._extract_list(prompts_res, "prompts", types.Prompt)

            logger.info(
                f"Fetched initial capabilities for {self.name}: "
                f"{len(self._tools)} tools, "
                f"{len(self._resources)} resources, "
                f"{len(self._prompts)} prompts."
            )

        except Exception as e:
            logger.error(
                f"Unexpected error fetching capabilities for {self.name}: {e}",
                exc_info=True,
            )
            self._tools, self._resources, self._prompts = [], [], []

    def _extract_list(
        self, result: Any, attribute_name: str, expected_type: type
    ) -> List[Any]:
        """Helper to extract list of items from potentially structured MCP results."""
        if hasattr(result, attribute_name):
            items = getattr(result, attribute_name)
        elif isinstance(result, list):
            items = result
        else:
            logger.warning(
                f"Unexpected result type {type(result)} when extracting {attribute_name} for {self.name}"
            )
            return []

        if isinstance(items, list):
            # Basic check if items are of the expected type (or can be treated as such)
            # More robust validation could be added here if needed
            return [item for item in items if isinstance(item, expected_type)]
        else:
            logger.warning(
                f"Extracted items for {attribute_name} is not a list for {self.name}: {type(items)}"
            )
            return []

    async def stop(self) -> None:
        """Stops the underlying MCP server process and closes the client session."""
        logger.info(f"Stopping proxied server: {self.name}...")
        await self._exit_stack.aclose()
        self._session = None
        self._client_cm = None
        self._server_info = None  # Clear server info on stop
        self._tools, self._resources, self._prompts = [], [], []  # Clear cached caps
        logger.info(f"Proxied server '{self.name}' stopped.")

    # --- MCP Interaction Methods (called by dynamic handlers) ---

    async def list_prompts(self) -> List[types.Prompt]:
        """Lists available prompts from the proxied server (uses cached list)."""
        # Return the cached list fetched during startup/refresh
        # For full dynamic support (listChanged), this would need to re-fetch
        return self._prompts

    async def get_prompt(
        self,
        plugin_manager: PluginManager,
        name: str,
        arguments: Optional[Dict[str, str]] = None,
        mcp_context: Optional[Context] = None,
    ) -> types.GetPromptResult:
        """Gets a specific prompt from the proxied server, processing through plugins."""
        logger.info(f"Getting prompt {self.name}/{name} with arguments {arguments}")

        # Use original arguments for the actual call
        result = await self.session.get_prompt(name, arguments=arguments)

        # Sanitize Response
        # Note: sanitize_response is designed generically. Ensure it handles GetPromptResult.
        try:
            sanitized_result = await sanitize_response(
                plugin_manager=plugin_manager,
                server_name=self.name,
                capability_type="prompt",
                name=name,
                response=result,
                request_arguments=arguments,
                mcp_context=mcp_context,  # Pass gateway context
            )

            # Ensure the result is still the correct type
            if isinstance(sanitized_result, types.GetPromptResult):
                return sanitized_result
            else:
                logger.error(
                    f"Response plugin for prompt {self.name}/{name} returned unexpected type {type(sanitized_result)}. Returning original."
                )
                return result  # Or potentially craft an error GetPromptResult
        except SanitizationError as se:
            logger.error(
                f"Sanitization error processing prompt response for {self.name}/{name}: {se}"
            )
            # Return an error message within the GetPromptResult structure
            return types.GetPromptResult(
                messages=[
                    types.PromptMessage(
                        role="assistant",
                        content=types.TextContent(
                            type="text", text=f"Gateway policy violation: {se}"
                        ),
                    )
                ]
            )
        except Exception as e:
            logger.error(
                f"Error processing prompt response for {self.name}/{name}: {e}",
                exc_info=True,
            )
            return types.GetPromptResult(
                messages=[
                    types.PromptMessage(
                        role="assistant",
                        content=types.TextContent(
                            type="text", text=f"Error processing prompt response: {e}"
                        ),
                    )
                ]
            )

    async def list_resources(self) -> List[types.Resource]:
        """Lists available resources from the proxied server (uses cached list)."""
        return self._resources

    async def read_resource(
        self,
        plugin_manager: PluginManager,
        uri: str,
        mcp_context: Optional[Context] = None,
    ) -> Tuple[bytes, Optional[str]]:
        """Reads a resource from the proxied server after processing through plugins."""
        # No request args to sanitize for read_resource itself

        content, mime_type = await self.session.read_resource(uri)

        # Sanitize the response content using the dedicated function
        sanitized_content, sanitized_mime_type = await sanitize_resource_read(
            plugin_manager=plugin_manager,
            server_name=self.name,
            uri=uri,
            content=content,
            mime_type=mime_type,
            mcp_context=mcp_context,  # Pass gateway context
        )
        return sanitized_content, sanitized_mime_type

    async def list_tools(self) -> List[types.Tool]:
        """Lists available tools from the proxied server (uses cached list)."""
        return self._tools

    async def call_tool(
        self,
        plugin_manager: PluginManager,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        mcp_context: Optional[Context] = None,
    ) -> types.CallToolResult:
        """Calls a tool on the proxied server after processing args and result through plugins."""
        logger.debug(f"Calling tool {self.name}/{name}")
        # 1. Sanitize request arguments
        sanitized_args = await sanitize_tool_call_args(
            plugin_manager=plugin_manager,
            server_name=self.name,
            tool_name=name,
            arguments=arguments,
            mcp_context=mcp_context,  # Pass gateway context
        )

        if sanitized_args is None:
            # Handle blocked request appropriately
            logger.warning(
                f"Tool call {self.name}/{name} blocked by request sanitizer plugin."
            )
            # Raise specific error to be caught by dynamic handler
            raise SanitizationError(
                f"Request blocked by gateway policy for tool '{self.name}/{name}'."
            )

        # 2. Call the tool with sanitized arguments
        result = await self.session.call_tool(name, arguments=sanitized_args)

        # 3. Sanitize the response result
        # Pass original request arguments for context if needed by plugins
        sanitized_result = await sanitize_tool_call_result(
            plugin_manager=plugin_manager,
            server_name=self.name,
            tool_name=name,
            result=result,
            request_arguments=arguments,  # Pass original args for context
            mcp_context=mcp_context,  # Pass gateway context
        )

        return sanitized_result

    async def get_capabilities(self) -> Optional[types.ServerCapabilities]:
        """Gets the capabilities of the proxied server from the stored InitializeResult."""
        if self._server_info is None:
            logger.warning(
                f"Server '{self.name}' InitializeResult not available (initialization failed or pending?)."
            )
            return None
        if self._server_info.capabilities is None:
            # MCP spec says capabilities is required, but handle gracefully
            logger.warning(
                f"Server '{self.name}' did not report capabilities in InitializeResult."
            )
            return None
        # No sanitization typically needed for capabilities object itself
        # Plugins *could* be added here if needed (e.g., filtering reported capabilities)
        return self._server_info.capabilities


@dataclass
class GetewayContext:
    """Context holding the managed proxied servers and plugin manager."""

    proxied_servers: Dict[str, Server] = field(default_factory=dict)
    plugin_manager: Optional[PluginManager] = None
    # Store dynamic capability handlers/metadata on the gateway context
    # Using FastMCP internal attributes is fragile, store here instead.
    gateway_tools: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gateway_prompts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # gateway_resources: Dict[str, Dict[str, Any]] = field(default_factory=dict) # For future use


# --- Dynamic Capability Registration ---


async def register_dynamic_tool(
    gateway_mcp: FastMCP,
    gateway_context: GetewayContext,
    server_name: str,
    tool: types.Tool,
    proxied_server: Server,
    plugin_manager: PluginManager,
):
    """Registers a dynamic tool handler on the gateway."""
    dynamic_tool_name = f"{server_name}_{tool.name}"
    logger.debug(f"Attempting to register dynamic tool: {dynamic_tool_name}")

    # Define the handler function within this scope to capture variables
    async def dynamic_tool_impl(ctx: Context, **kwargs: Any) -> types.CallToolResult:
        logger.info(
            f"Executing dynamic tool '{dynamic_tool_name}' (proxied from {server_name}/{tool.name})"
        )
        # Retrieve the specific proxied_server and plugin_manager captured at registration time.
        # Using gateway_context.proxied_servers.get(server_name) is also possible but captured ones are direct.
        try:
            # Delegate to the Server.call_tool which handles sanitization via plugin_manager
            result = await proxied_server.call_tool(
                plugin_manager=plugin_manager,
                name=tool.name,
                arguments=kwargs,
                mcp_context=ctx,  # Pass gateway context
            )
            return result
        except SanitizationError as se:
            # Log the error and return a structured error response
            logger.error(
                f"Sanitization policy violation for dynamic tool '{dynamic_tool_name}': {se}"
            )
            return types.CallToolResult(
                outputs=[
                    {"type": "error", "message": f"Gateway policy violation: {se}"}
                ]
            )
        except Exception as e:
            logger.error(
                f"Error executing dynamic tool '{dynamic_tool_name}': {e}",
                exc_info=True,
            )
            return types.CallToolResult(
                outputs=[
                    {
                        "type": "error",
                        "message": f"Error executing dynamic tool '{dynamic_tool_name}': {e}",
                    }
                ]
            )

    # Store the handler and metadata in the gateway context
    # We assume FastMCP's standard handlers will find these later via list_tools etc.
    # This avoids direct manipulation of FastMCP internals.
    gateway_context.gateway_tools[dynamic_tool_name] = {
        "handler": dynamic_tool_impl,
        "schema": types.Tool(
            name=dynamic_tool_name,
            description=tool.description or f"Proxied tool from {server_name}",
            arguments=tool.arguments,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
        ),
    }
    logger.info(f"Stored registration info for dynamic tool '{dynamic_tool_name}'")


async def register_dynamic_prompt(
    gateway_mcp: FastMCP,
    gateway_context: GetewayContext,
    server_name: str,
    prompt: types.Prompt,
    proxied_server: Server,
    plugin_manager: PluginManager,
):
    """Registers a dynamic prompt handler on the gateway."""
    dynamic_prompt_name = f"{server_name}_{prompt.name}"
    logger.debug(f"Attempting to register dynamic prompt: {dynamic_prompt_name}")

    async def dynamic_prompt_impl(ctx: Context, **kwargs: Any) -> types.GetPromptResult:
        logger.info(
            f"Executing dynamic prompt '{dynamic_prompt_name}' (proxied from {server_name}/{prompt.name})"
        )
        try:
            # Delegate to the Server.get_prompt which handles response sanitization
            result = await proxied_server.get_prompt(
                plugin_manager=plugin_manager,
                name=prompt.name,
                arguments=kwargs,
                mcp_context=ctx,  # Pass gateway context
            )
            return result  # Server.get_prompt already wraps sanitization errors
        except Exception as e:
            # Catch unexpected errors during the call itself
            logger.error(
                f"Error executing dynamic prompt '{dynamic_prompt_name}': {e}",
                exc_info=True,
            )
            return types.GetPromptResult(
                messages=[
                    types.PromptMessage(
                        role="assistant",
                        content=types.TextContent(
                            type="text",
                            text=f"Error executing prompt '{dynamic_prompt_name}': {e}",
                        ),
                    )
                ]
            )

    # Store handler and metadata in gateway context
    gateway_context.gateway_prompts[dynamic_prompt_name] = {
        "handler": dynamic_prompt_impl,
        "schema": types.Prompt(
            name=dynamic_prompt_name,
            description=prompt.description or f"Proxied prompt from {server_name}",
            arguments=prompt.arguments,
        ),
    }
    logger.info(f"Stored registration info for dynamic prompt '{dynamic_prompt_name}'")


async def register_proxied_capabilities(gateway_mcp: FastMCP, context: GetewayContext):
    """Fetches capabilities from proxied servers and registers them dynamically."""
    logger.info("Dynamically registering capabilities from proxied servers...")
    plugin_manager = context.plugin_manager
    if not plugin_manager:
        logger.error(
            "PluginManager missing during dynamic registration. Cannot register."
        )
        return

    registration_tasks = []
    for server_name, proxied_server in context.proxied_servers.items():
        if proxied_server.session:  # Only register for active sessions
            # Register tools for this server
            for tool in proxied_server._tools:  # Use cached list
                registration_tasks.append(
                    register_dynamic_tool(
                        gateway_mcp,
                        context,
                        server_name,
                        tool,
                        proxied_server,
                        plugin_manager,
                    )
                )
            # Register prompts for this server
            for prompt in proxied_server._prompts:  # Use cached list
                registration_tasks.append(
                    register_dynamic_prompt(
                        gateway_mcp,
                        context,
                        server_name,
                        prompt,
                        proxied_server,
                        plugin_manager,
                    )
                )
            # Note: Dynamic resource registration is deferred
            if proxied_server._resources:
                logger.warning(
                    f"Dynamic resource registration for server '{server_name}' is not yet implemented. Resources will not be exposed via gateway."
                )
        else:
            logger.warning(
                f"Skipping dynamic registration for inactive server: {server_name}"
            )

    if registration_tasks:
        await asyncio.gather(*registration_tasks)
        logger.info(
            f"Dynamic registration process complete. Registered {len(context.gateway_tools)} tools and {len(context.gateway_prompts)} prompts."
        )
    else:
        logger.info("No active proxied servers found or no capabilities to register.")


# --- Lifespan Management ---


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[GetewayContext]:
    """Manages the lifecycle of proxied MCP servers, plugins, and dynamic registration."""
    global cli_args
    logger.info("MCP gateway lifespan starting...")

    # --- Plugin Manager Setup ---
    # (Plugin setup code remains the same as before)
    enabled_plugin_types = []
    enabled_plugins = {}

    if cli_args and cli_args.enable_guardrails:
        enabled_plugin_types.append("guardrail")
        enabled_plugins["guardrail"] = cli_args.enable_guardrails
        logger.info(f"Guardrail plugins ENABLED: {cli_args.enable_guardrails}")
    else:
        logger.info("Guardrail plugins DISABLED.")

    if cli_args and cli_args.enable_tracing:
        enabled_plugin_types.append("tracing")
        enabled_plugins["tracing"] = cli_args.enable_tracing
        logger.info(f"Tracing plugins ENABLED: {cli_args.enable_tracing}")
    else:
        logger.info("Tracing plugins DISABLED by command line flag.")

    plugin_dir = (
        cli_args.plugin_dir
        if cli_args and cli_args.plugin_dir
        else os.path.join(os.path.dirname(__file__), "plugins")
    )
    logger.info(f"Using plugin directory: {plugin_dir}")

    if not os.path.isdir(plugin_dir):
        logger.warning(
            f"Plugin directory '{plugin_dir}' not found. No plugins will be loaded."
        )
        plugin_dirs_to_scan = []
    else:
        plugin_dirs_to_scan = []
        try:
            for d in os.listdir(plugin_dir):
                dir_path = os.path.join(plugin_dir, d)
                if os.path.isdir(dir_path) and not d.startswith("_"):
                    plugin_dirs_to_scan.append(dir_path)
                    logger.debug(f"Added plugin directory to scan: {dir_path}")
        except Exception as e:
            logger.error(f"Error scanning plugin directories: {e}", exc_info=True)

    plugin_manager = PluginManager(plugin_dirs=plugin_dirs_to_scan)
    plugin_configs = {}  # Load from config if needed
    plugin_manager.discover_and_load(
        enabled_types=enabled_plugin_types,
        plugin_configs=plugin_configs,
        enabled_plugins=enabled_plugins,
    )
    # --- End Plugin Manager Setup ---

    # Load proxied server configs
    proxied_server_configs = load_config(cli_args.mcp_json_path)

    # Initialize context WITHOUT starting servers yet
    context = GetewayContext(plugin_manager=plugin_manager)

    # Create Server instances but don't start them yet
    for name, server_config in proxied_server_configs.items():
        logger.info(f"Creating client instance for proxied server: {name}")
        proxied_server = Server(name, server_config)
        context.proxied_servers[name] = proxied_server

    # Start all servers concurrently
    if context.proxied_servers:
        logger.info("Starting all configured proxied servers...")
        start_tasks = [
            asyncio.create_task(server.start())
            for server in context.proxied_servers.values()
        ]
        if start_tasks:
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            # Check results for errors during startup
            failed_servers = []
            for i, result in enumerate(results):
                server_name = list(context.proxied_servers.keys())[i]
                if isinstance(result, Exception):
                    logger.error(
                        f"Failed to start server '{server_name}' during gather: {result}",
                        exc_info=result
                        if logger.isEnabledFor(logging.DEBUG)
                        else None,  # Avoid verbose traceback unless DEBUG
                    )
                    failed_servers.append(server_name)
                else:
                    logger.info(f"Successfully started server '{server_name}'.")

            # Remove failed servers from context so we don't try to register them
            for name in failed_servers:
                context.proxied_servers.pop(name, None)

            logger.info("Attempted to start all configured proxied servers.")
    else:
        logger.warning(
            "No proxied MCP servers configured. Running in standalone mode (plugins still active)."
        )

    # --- DYNAMIC REGISTRATION ---
    # Call registration function *after* servers are started and initial caps fetched
    # Pass the FastMCP instance `server` to the registration function
    await register_proxied_capabilities(server, context)
    # --- END DYNAMIC REGISTRATION ---

    try:
        # Yield the context containing servers, plugin manager, and registration info
        yield context
    finally:
        logger.info("MCP gateway lifespan shutting down...")
        # Stop only the servers that were successfully started and potentially registered
        stop_tasks = [
            asyncio.create_task(server.stop())
            for name, server in context.proxied_servers.items()
            if server._session is not None  # Check if session was ever active
        ]
        if stop_tasks:
            await asyncio.gather(
                *stop_tasks, return_exceptions=True
            )  # Log errors on stop too
            logger.info("All active proxied servers stopped.")
        context.gateway_tools.clear()  # Clear dynamic registrations
        context.gateway_prompts.clear()
        logger.info("MCP gateway shutdown complete.")


# Initialize the MCP gateway server
# Pass description and version if desired
mcp = FastMCP("MCP Gateway", lifespan=lifespan, version="0.1.0")


# --- Gateway's Own Capability Implementations ---


@mcp.list_tools()
async def gateway_list_tools(ctx: Context) -> List[types.Tool]:
    """Lists all tools dynamically registered from proxied servers, plus gateway-specific tools."""
    gateway_context: GetewayContext = ctx.request_context.lifespan_context
    registered_tools: List[types.Tool] = []

    # Add dynamically registered tools
    for tool_info in gateway_context.gateway_tools.values():
        registered_tools.append(tool_info["schema"])

    # Add gateway's own tools (get_metadata)
    # Manually define its schema
    get_metadata_tool_schema = types.Tool(
        name="get_metadata",
        description=get_metadata.__doc__ or "Provides metadata about proxied MCPs.",
        arguments=[],  # get_metadata takes only ctx
    )
    registered_tools.append(get_metadata_tool_schema)

    logger.debug(f"Gateway list_tools returning: {[t.name for t in registered_tools]}")
    return registered_tools


@mcp.list_prompts()
async def gateway_list_prompts(ctx: Context) -> List[types.Prompt]:
    """Lists all prompts dynamically registered from proxied servers."""
    gateway_context: GetewayContext = ctx.request_context.lifespan_context
    registered_prompts: List[types.Prompt] = [
        prompt_info["schema"]
        for prompt_info in gateway_context.gateway_prompts.values()
    ]
    logger.debug(
        f"Gateway list_prompts returning: {[p.name for p in registered_prompts]}"
    )
    return registered_prompts


@mcp.list_resources()
async def gateway_list_resources(ctx: Context) -> List[types.Resource]:
    """Lists resources (currently returns empty as dynamic resource registration is not implemented)."""
    logger.warning(
        "Gateway list_resources called, but dynamic resource registration is not implemented."
    )
    return []


# --- Gateway Specific Tools ---


@mcp.tool()  # Keep get_metadata as it provides original server details
async def get_metadata(ctx: Context) -> Dict[str, Any]:
    """Provides metadata about all available proxied MCPs, including their original capabilities."""
    geteway_context: GetewayContext = ctx.request_context.lifespan_context
    metadata: Dict[str, Any] = {}

    if not geteway_context.proxied_servers:
        return {"status": "standalone_mode", "message": "No proxied MCPs configured"}

    # Iterate through potentially *all* configured servers, even if start failed, to report status
    all_configured_servers = load_config(
        cli_args.mcp_json_path if cli_args else None
    )  # Reload to get names if needed
    if not all_configured_servers:
        all_configured_servers = {}  # Handle case where config path is missing

    for name in all_configured_servers.keys():
        server = geteway_context.proxied_servers.get(name)
        server_metadata: Dict[str, Any] = {
            "status": "inactive",
            "capabilities": None,
            "original_tools": [],
            "original_resources": [],
            "original_prompts": [],
        }

        if not server or not server.session:
            server_metadata["error"] = "Server session not active or start failed"
            metadata[name] = server_metadata
            continue

        try:
            server_metadata["status"] = "active"
            # 1. Get Capabilities
            capabilities = (
                await server.get_capabilities()
            )  # Use the stored capabilities
            server_metadata["capabilities"] = (
                capabilities.model_dump() if capabilities else None
            )

            # 2. List Original Tools (use cached list)
            if capabilities and capabilities.tools:
                server_metadata["original_tools"] = [
                    tool.model_dump() for tool in server._tools
                ]
            else:
                logger.debug(
                    f"Server '{name}' does not support tools, skipping list_tools in metadata."
                )

            # 3. List Original Resources (use cached list)
            if capabilities and capabilities.resources:
                server_metadata["original_resources"] = [
                    res.model_dump() for res in server._resources
                ]
            else:
                logger.debug(
                    f"Server '{name}' does not support resources, skipping list_resources in metadata."
                )

            # 4. List Original Prompts (use cached list)
            if capabilities and capabilities.prompts:
                server_metadata["original_prompts"] = [
                    p.model_dump() for p in server._prompts
                ]
            else:
                logger.debug(
                    f"Server '{name}' does not support prompts, skipping list_prompts in metadata."
                )

            metadata[name] = server_metadata

        except Exception as e:
            # Catch general errors during metadata retrieval for this specific server
            logger.error(
                f"General error getting metadata for server '{name}': {e}",
                exc_info=True,
            )
            metadata[name] = {
                "status": "error",
                "error": f"Failed to retrieve metadata: {e}",
                "capabilities": server_metadata.get(
                    "capabilities"
                ),  # Include caps if fetched before error
                "original_tools": server_metadata.get("original_tools", []),
                "original_resources": server_metadata.get("original_resources", []),
                "original_prompts": server_metadata.get("original_prompts", []),
            }

    return metadata


# --- Tool/Prompt Execution (Handled by FastMCP routing to dynamic handlers) ---
# FastMCP needs a way to call the handlers stored in gateway_context.
# This usually happens via its router. We need to ensure the router uses
# the handlers we stored. This might require a custom router or adapting FastMCP.

# Let's try adding generic handlers that dispatch based on the context.
# This brings back the dispatcher pattern, but uses the stored handlers.


@mcp.call_tool()  # Generic handler for all dynamically registered tools
async def gateway_call_tool_dispatcher(
    name: str, arguments: dict, ctx: Context
) -> types.CallToolResult:
    """Dispatches tool calls to dynamically registered handlers."""
    gateway_context: GetewayContext = ctx.request_context.lifespan_context
    tool_info = gateway_context.gateway_tools.get(name)

    if tool_info and "handler" in tool_info:
        handler = tool_info["handler"]
        # Directly call the stored handler
        return await handler(ctx, **arguments)
    else:
        # Handle case where tool is not found (e.g., if called before registration or invalid name)
        logger.error(
            f"Gateway received call for unknown or unregistered dynamic tool: {name}"
        )
        return types.CallToolResult(
            outputs=[{"type": "error", "message": f"Unknown tool: {name}"}]
        )


@mcp.get_prompt()  # Generic handler for all dynamically registered prompts
async def gateway_get_prompt_dispatcher(
    name: str, arguments: dict | None, ctx: Context
) -> types.GetPromptResult:
    """Dispatches prompt requests to dynamically registered handlers."""
    gateway_context: GetewayContext = ctx.request_context.lifespan_context
    prompt_info = gateway_context.gateway_prompts.get(name)

    if prompt_info and "handler" in prompt_info:
        handler = prompt_info["handler"]
        # Directly call the stored handler
        return await handler(ctx, **(arguments or {}))
    else:
        logger.error(
            f"Gateway received request for unknown or unregistered dynamic prompt: {name}"
        )
        # Return an error within the prompt structure
        return types.GetPromptResult(
            messages=[
                types.PromptMessage(
                    role="assistant",
                    content=types.TextContent(
                        type="text", text=f"Unknown prompt: {name}"
                    ),
                )
            ]
        )


# --- Argument Parsing & Main ---
# (Argument parsing and main function remain the same)
def parse_args(args=None):
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description="MCP Gateway Server")
    parser.add_argument(
        "--mcp-json-path",
        type=str,
        required=True,
        help="Path to the mcp.json configuration file",
    )
    parser.add_argument(
        "--enable-guardrails",
        action="append",
        help="Enable specific guardrail plugins (e.g., 'basic', 'lasso'). Multiple plugins can be enabled by repeating the argument. If used without a value, all guardrail plugins are enabled.",
        nargs="?",
        const="all",
        default=[],
    )
    parser.add_argument(
        "--enable-tracing",
        action="append",
        help="Enable specific tracing plugins. Multiple plugins can be enabled by repeating the argument. If used without a value, all tracing plugins are enabled.",
        nargs="?",
        const="all",
        default=[],
    )
    parser.add_argument(
        "--plugin-dir",
        type=str,
        default=None,
        help="Path to the directory containing plugin subdirectories (e.g., 'guardrails', 'tracing'). Defaults to './plugins' relative to server.py.",
    )
    if args is None:
        args = sys.argv[1:]
    return parser.parse_args(args)


def main():
    global cli_args
    cli_args = parse_args()

    logger.info("Starting MCP gateway server directly...")
    mcp.run()


if __name__ == "__main__":
    main()
