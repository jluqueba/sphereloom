"""The server must be a real, runnable MCP server before any camera code exists."""

from __future__ import annotations

from typing import Any, cast

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from sphereloom.config import Settings
from sphereloom.server.app import build_server
from sphereloom.server.context import AppContext


async def _call_tool(
    server: MCPServer[AppContext], name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call a tool and return its structured payload.

    Tools return structured content rather than prose: an agent should branch on fields,
    not parse English.
    """
    result = await server.call_tool(name, arguments or {})
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert result.structured_content is not None
    return cast("dict[str, Any]", result.structured_content)


async def test_the_server_exposes_its_tools(settings: Settings) -> None:
    server = build_server(settings)

    tools = await server.list_tools()

    assert [tool.name for tool in tools] == ["server_health"]


async def test_every_tool_describes_itself_for_a_model(settings: Settings) -> None:
    """Tool descriptions are the only instructions a model gets, so they cannot be empty."""
    server = build_server(settings)

    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 40, f"{tool.name} has a uselessly terse description"


async def test_health_reports_configuration_without_touching_a_camera(
    settings: Settings,
) -> None:
    server = build_server(settings)

    payload = await _call_tool(server, "server_health")

    assert payload["status"] == "ok"
    assert payload["transport"] == "stdio"
    assert payload["camera_backend"] == "osc"
    assert payload["destructive_tools_enabled"] is False


async def test_health_does_not_leak_the_workspace_path(settings: Settings) -> None:
    server = build_server(settings)

    payload = await _call_tool(server, "server_health")

    assert str(settings.resolved_workspace()) not in str(payload)


def test_the_server_advertises_usage_instructions(settings: Settings) -> None:
    """An agent reads these before anything else, so they must set expectations."""
    server = build_server(settings)

    assert server.instructions is not None
    assert "capabilities_list" in server.instructions
    assert "job" in server.instructions.lower()


@pytest.mark.parametrize("backend", ["osc", "fake"])
def test_the_server_builds_for_every_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, backend: str
) -> None:
    monkeypatch.setenv("SPHERELOOM_CAMERA_BACKEND", backend)
    monkeypatch.setenv("SPHERELOOM_WORKSPACE_DIR", str(tmp_path))

    server = build_server(Settings())

    assert server.name == "sphereloom"
