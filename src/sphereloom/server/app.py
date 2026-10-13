"""MCP application assembly.

Milestone 0 wires the container, the lifespan and one health tool, so the server is a real,
runnable MCP server before any camera code exists. Camera, media and job tools arrive in
later milestones and register through the same path.

Note on naming: the MCP Python SDK renamed `FastMCP` to `MCPServer` in version 2.0. Earlier
planning documents use the old name; the class below is the current one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict

from sphereloom import __version__
from sphereloom.capabilities.registry import CapabilityRegistry
from sphereloom.config import CameraBackend, Settings, load_settings
from sphereloom.domain.clock import SystemClock
from sphereloom.domain.ids import SecureIdFactory
from sphereloom.logging import LogEvent, log_event
from sphereloom.security.workspace import Workspace
from sphereloom.server.context import AppContext
from sphereloom.tools.capabilities import register_capabilities_tool

#: The capability declarations each configured backend is served by. The fake camera speaks
#: the same Wi-Fi protocol as a real one, so both resolve to the `osc` declarations.
CAPABILITY_BACKENDS: dict[CameraBackend, str] = {
    CameraBackend.OSC: "osc",
    CameraBackend.FAKE: "osc",
}

SERVER_INSTRUCTIONS = """\
SphereLoom controls 360 cameras and processes 360 media on this machine.

Two things to know before you call anything:

1. Not every capability exists on every connection. Call `capabilities_list` to see what the
   active backend supports. When something is unavailable you get a structured error that
   explains why, so report that reason rather than retrying.
2. Long operations such as downloads and exports run as background jobs. The tool returns a
   job identifier immediately; poll `jobs_get` for progress. Media files are never returned
   inside a response, only paths within the configured workspace.
"""


class HealthReport(BaseModel):
    """What the server can tell you about itself without touching a camera."""

    model_config = ConfigDict(frozen=True)

    status: str
    version: str
    transport: str
    camera_backend: str
    workspace_ready: bool
    destructive_tools_enabled: bool


def build_server(settings: Settings | None = None) -> MCPServer[AppContext]:
    """Construct the MCP server and register its tools."""
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(_: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        workspace = Workspace(resolved.resolved_workspace())
        workspace.ensure()
        context = AppContext(
            settings=resolved,
            workspace=workspace,
            clock=SystemClock(),
            id_factory=SecureIdFactory(),
        )
        log_event(
            LogEvent.SERVER_STARTED,
            transport=resolved.transport,
            camera_backend=resolved.camera_backend,
        )
        try:
            yield context
        finally:
            log_event(LogEvent.SERVER_STOPPED)

    server: MCPServer[AppContext] = MCPServer(
        name="sphereloom",
        version=__version__,
        instructions=SERVER_INSTRUCTIONS,
        lifespan=lifespan,
    )

    _register_health_tool(server, resolved)
    register_capabilities_tool(
        server, CapabilityRegistry(CAPABILITY_BACKENDS[resolved.camera_backend])
    )
    return server


def _register_health_tool(server: MCPServer[AppContext], settings: Settings) -> None:
    @server.tool(
        name="server_health",
        title="Check SphereLoom server health",
        description=(
            "Report whether the SphereLoom server itself is running and how it is "
            "configured. This never contacts a camera, so it is the right first call when "
            "something is not working."
        ),
    )
    def server_health() -> HealthReport:
        workspace = Workspace(settings.resolved_workspace())
        return HealthReport(
            status="ok",
            version=__version__,
            transport=settings.transport.value,
            camera_backend=settings.camera_backend.value,
            workspace_ready=workspace.root.is_dir(),
            destructive_tools_enabled=settings.enable_destructive_tools,
        )
