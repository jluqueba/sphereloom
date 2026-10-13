"""The public capability page is generated from the registry and cannot drift (AC-2.1.4)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from sphereloom.capabilities.data import STATIC_CAPABILITIES
from sphereloom.capabilities.matrix import (
    BEGIN_MARKER,
    END_MARKER,
    LABELS,
    STATE_LABELS,
    render_section,
    replace_section,
)
from sphereloom.capabilities.registry import CapabilityRegistry
from sphereloom.domain.capabilities import Capability, CapabilityState, CapabilityStatus

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPOSITORY_ROOT / "docs" / "CAPABILITIES.md"


def _load_script() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / "render_capability_matrix.py"
    spec = importlib.util.spec_from_file_location("render_capability_matrix", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


def _document(section: str) -> str:
    return f"# Capabilities\n\nIntro.\n\n{section}\n\n## After\n\nOutro.\n"


def test_the_committed_page_matches_the_registry() -> None:
    """Fails when a capability changes state without regenerating the page."""
    committed = PAGE.read_text("utf-8").replace("\r\n", "\n")
    registry = CapabilityRegistry(script.PUBLIC_BACKEND)

    assert replace_section(committed, render_section(registry)) == committed, (
        "docs/CAPABILITIES.md is stale; run: uv run python scripts/render_capability_matrix.py"
    )


def test_a_state_change_without_regeneration_is_detected() -> None:
    committed = PAGE.read_text("utf-8").replace("\r\n", "\n")
    table = dict(STATIC_CAPABILITIES["osc"])
    table[Capability.CAMERA_CONNECT] = CapabilityStatus(
        capability=Capability.CAMERA_CONNECT, status=CapabilityState.AVAILABLE
    )
    changed = CapabilityRegistry("osc", {"osc": table})

    assert replace_section(committed, render_section(changed)) != committed


def test_the_table_lists_every_capability_once_in_its_public_state() -> None:
    section = render_section(CapabilityRegistry("osc"))

    for status in CapabilityRegistry("osc").resolve_all():
        row = f"| {LABELS[status.capability]} | `{status.capability.value}` | "
        assert section.count(row) == 1
        assert f"{row}{STATE_LABELS[status.status]} |" in section


def test_every_capability_and_state_has_a_public_label() -> None:
    assert set(LABELS) == set(Capability)
    assert set(STATE_LABELS) == set(CapabilityState)


def test_only_the_generated_section_is_replaced() -> None:
    document = _document(f"{BEGIN_MARKER}\nold table\n{END_MARKER}")

    updated = replace_section(document, f"{BEGIN_MARKER}\nnew table\n{END_MARKER}")

    assert updated == _document(f"{BEGIN_MARKER}\nnew table\n{END_MARKER}")


@pytest.mark.parametrize(
    "section",
    [
        "no markers at all",
        f"{BEGIN_MARKER}\nonly the start",
        f"only the end\n{END_MARKER}",
        f"{END_MARKER}\nreversed\n{BEGIN_MARKER}",
        f"{BEGIN_MARKER}\n{BEGIN_MARKER}\nrepeated\n{END_MARKER}",
    ],
    ids=["missing", "no-end", "no-begin", "reversed", "repeated"],
)
def test_malformed_markers_are_refused(section: str) -> None:
    with pytest.raises(ValueError, match="marker"):
        replace_section(_document(section), f"{BEGIN_MARKER}\n{END_MARKER}")


def test_the_script_rewrites_a_stale_page_and_leaves_a_current_one_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = tmp_path / "CAPABILITIES.md"
    page.write_text(_document(f"{BEGIN_MARKER}\nstale\n{END_MARKER}"), "utf-8")
    monkeypatch.setattr(script, "PAGE", page)

    assert script.main() == 0
    regenerated = page.read_text("utf-8")
    assert "stale" not in regenerated
    assert "`camera.connect`" in regenerated

    assert script.main() == 0
    assert page.read_text("utf-8") == regenerated
