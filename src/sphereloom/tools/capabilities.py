"""The `capabilities_list` tool: what the active backend can do, before anything is tried."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from sphereloom.capabilities.registry import CapabilityRegistry
from sphereloom.domain.capabilities import CapabilityStatus
from sphereloom.server.context import AppContext

DESCRIPTION = """\
List every capability SphereLoom knows about and whether the active camera connection can \
use it. Call this first when planning, so you never attempt something that cannot work.

Each entry has a `capability` identifier such as `photo.capture` and a `status`:
- `available`: it works now.
- `not_yet_available`: SphereLoom does not provide it yet; do not retry it.
- `unsupported_by_vendor`: the camera's API for this connection does not offer it; stop \
asking for it on this connection.

Every entry that is not available includes a `reason` to tell the user and a `docs_url`. \
This tool never contacts the camera and has no side effects.\
"""


class CapabilitiesListResult(BaseModel):
    """Every capability of the active backend, in a fixed order."""

    model_config = ConfigDict(frozen=True)

    backend: str = Field(description="The camera connection the statuses apply to, such as `osc`.")
    capabilities: list[CapabilityStatus] = Field(
        description="One entry per capability, each exactly once."
    )


def register_capabilities_tool(server: MCPServer[AppContext], registry: CapabilityRegistry) -> None:
    """Register `capabilities_list`, which reads the registry and nothing else."""

    @server.tool(
        name="capabilities_list",
        title="List what SphereLoom can do",
        description=DESCRIPTION,
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def capabilities_list() -> CapabilitiesListResult:
        return CapabilitiesListResult(backend=registry.backend, capabilities=registry.resolve_all())
