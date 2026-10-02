"""End-to-end check that SphereLoom is a real MCP server, not just a library.

This is the Milestone 0 exit criterion: a genuine MCP client spawns the installed
`sphereloom` process, speaks the protocol over stdio, and gets a usable tool listing back.
Everything else in the test suite calls the server in-process, so this is the only test that
would catch a packaging, entry-point or transport regression.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.anyio

#: Variables the child process needs merely to start on each platform.
_PASSTHROUGH_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server_parameters(tmp_path: Path) -> StdioServerParameters:
    """Launch the server as a subprocess with an isolated workspace."""
    env = {name: os.environ[name] for name in _PASSTHROUGH_ENV if name in os.environ}
    env.update(
        {
            "SPHERELOOM_WORKSPACE_DIR": str(tmp_path / "workspace"),
            "SPHERELOOM_TRANSPORT": "stdio",
            # Keep the subprocess quiet so its logs do not clutter test output.
            "SPHERELOOM_LOG_LEVEL": "ERROR",
        }
    )
    return StdioServerParameters(command=sys.executable, args=["-m", "sphereloom"], env=env)


async def test_a_real_client_can_list_tools_over_stdio(
    server_parameters: StdioServerParameters,
) -> None:
    async with (
        stdio_client(server_parameters) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        tools = await session.list_tools()

    assert "server_health" in {tool.name for tool in tools.tools}


async def test_a_real_client_can_call_a_tool_over_stdio(
    server_parameters: StdioServerParameters,
) -> None:
    async with (
        stdio_client(server_parameters) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        result = await session.call_tool("server_health", {})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["status"] == "ok"


async def test_the_server_sends_its_instructions_to_the_client(
    server_parameters: StdioServerParameters,
) -> None:
    """Instructions are how an agent learns the capability and job model up front."""
    async with (
        stdio_client(server_parameters) as (read, write),
        ClientSession(read, write) as session,
    ):
        initialised = await session.initialize()

    assert initialised.instructions is not None
    assert "capabilities_list" in initialised.instructions
