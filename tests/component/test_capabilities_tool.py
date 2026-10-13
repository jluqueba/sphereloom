"""The `capabilities_list` tool through the real MCP server (FR-2.1, AC-2.1.1 to AC-2.1.3)."""

from __future__ import annotations

import re
from typing import Any, cast

import pytest
from mcp.types import CallToolResult

from sphereloom.config import Settings
from sphereloom.domain.capabilities import Capability
from sphereloom.server.app import build_server

_PLANNING = re.compile(r"(?i)\bmilestones?\b|\bM[0-9](?:\.[0-9])?\b|\broadmap\b")


async def _capabilities(settings: Settings) -> dict[str, Any]:
    result = await build_server(settings).call_tool("capabilities_list", {})
    assert isinstance(result, CallToolResult)
    assert not result.is_error
    assert result.structured_content is not None
    return cast("dict[str, Any]", result.structured_content)


async def test_every_capability_appears_exactly_once_with_a_public_state(
    settings: Settings,
) -> None:
    payload = await _capabilities(settings)

    entries = payload["capabilities"]
    assert payload["backend"] == "osc"
    assert [entry["capability"] for entry in entries] == [c.value for c in Capability]
    assert {entry["status"] for entry in entries} <= {
        "available",
        "not_yet_available",
        "unsupported_by_vendor",
    }


async def test_every_unavailable_capability_explains_itself(settings: Settings) -> None:
    for entry in (await _capabilities(settings))["capabilities"]:
        if entry["status"] != "available":
            assert entry["reason"], entry["capability"]
            assert str(entry["docs_url"]).startswith("https://"), entry["capability"]


@pytest.mark.parametrize(
    ("capability", "status"),
    [
        ("exposure.control", "unsupported_by_vendor"),
        ("preview.live", "unsupported_by_vendor"),
        ("media.stitch.video", "not_yet_available"),
        ("media.export", "not_yet_available"),
    ],
)
async def test_documented_gaps_are_reported(
    settings: Settings, capability: str, status: str
) -> None:
    """AC-2.1.2 and AC-2.1.3."""
    entries = {
        entry["capability"]: entry for entry in (await _capabilities(settings))["capabilities"]
    }

    assert entries[capability]["status"] == status


async def test_the_output_names_no_milestone(settings: Settings) -> None:
    """ADR-0016: tool output describes the current state only."""
    server = build_server(settings)
    tool = next(tool for tool in await server.list_tools() if tool.name == "capabilities_list")

    assert not _PLANNING.search(str(await _capabilities(settings)))
    assert not _PLANNING.search(tool.description or "")


async def test_the_tool_is_declared_read_only(settings: Settings) -> None:
    server = build_server(settings)
    tool = next(tool for tool in await server.list_tools() if tool.name == "capabilities_list")

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False


@pytest.mark.parametrize("backend", ["osc", "fake"])
async def test_every_configured_backend_lists_its_capabilities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, backend: str
) -> None:
    """The result names the configured backend, as `server_health` and errors do."""
    monkeypatch.setenv("SPHERELOOM_CAMERA_BACKEND", backend)
    monkeypatch.setenv("SPHERELOOM_WORKSPACE_DIR", str(tmp_path))

    payload = await _capabilities(Settings())

    assert payload["backend"] == backend
    assert len(payload["capabilities"]) == len(Capability)
