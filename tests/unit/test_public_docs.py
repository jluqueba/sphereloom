"""Public documentation describes the current state, and its diagrams are never stale.

Public documents follow ADR-0016: they describe what SphereLoom does today, in three
capability states, and never name milestones. Diagrams are committed SVG images rendered
from a Mermaid source kept beside them (ADR-0015). These tests turn both rules into checks
that fail, rather than conventions a reviewer has to remember.

Every check is a pure function, run once against the repository and once against an
injected case that must fail, so a check that silently stopped working would be noticed.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPOSITORY_ROOT / "docs" / "assets"
CAPABILITIES = REPOSITORY_ROOT / "docs" / "CAPABILITIES.md"

#: Documents that describe the product. Contributor process documents such as
#: CONTRIBUTING.md may mention milestones, because they describe how the team works.
PUBLIC_DOCUMENTS = ("README.md", "docs/DEVELOPER_GUIDE.md", "docs/CAPABILITIES.md")

#: The only states a capability may be in publicly (ADR-0016).
STATES = frozenset({"Available", "Not yet available", "Not supported by the vendor API"})

#: Every capability identifier the public matrix must list.
CAPABILITY_IDS = frozenset(
    {
        "camera.connect",
        "camera.status",
        "options.read",
        "options.write",
        "photo.capture",
        "video.record",
        "files.list",
        "files.download",
        "files.delete",
        "exposure.control",
        "preview.live",
        "storage.format",
        "media.stitch.video",
        "media.export",
    }
)

_PLANNING = re.compile(r"(?i)\bmilestones?\b|\bM[0-9](?:\.[0-9])?\b|\broadmap\b")
_INLINE_MERMAID = re.compile(r"^\s*```\s*mermaid\b", re.MULTILINE)
_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]+)\)")
_MATRIX_ROW = re.compile(r"^\|[^|]*\|\s*`(?P<id>[^`]+)`\s*\|\s*(?P<status>[^|]*?)\s*\|")


def _load_script() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / "render_diagrams.py"
    spec = importlib.util.spec_from_file_location("render_diagrams", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


render_diagrams = _load_script()


def planning_mentions(markdown: str) -> list[str]:
    """Return every milestone or roadmap reference, and any inline Mermaid block."""
    found = [match.group(0) for match in _PLANNING.finditer(markdown)]
    if _INLINE_MERMAID.search(markdown):
        found.append("inline Mermaid block")
    return found


def image_problems(markdown: str, base: Path) -> list[str]:
    """Return images without alt text, and relative images whose file does not exist."""
    problems: list[str] = []
    for match in _IMAGE.finditer(markdown):
        target = match["target"]
        if not match["alt"].strip():
            problems.append(f"{target} has no alt text")
        if "://" not in target and not (base / target).is_file():
            problems.append(f"{target} does not exist")
    return problems


def diagram_problems(assets: Path) -> list[str]:
    """Return every diagram whose image is missing, unstamped or rendered from older source."""
    sources = {path.stem for path in assets.glob("*.mmd")}
    images = {path.stem for path in assets.glob("*.svg")}
    problems = [f"{name}.mmd has no rendered image" for name in sorted(sources - images)]
    problems += [f"{name}.svg has no Mermaid source" for name in sorted(images - sources)]
    for name in sorted(sources & images):
        expected = render_diagrams.source_digest((assets / f"{name}.mmd").read_bytes())
        recorded = render_diagrams.recorded_digest((assets / f"{name}.svg").read_text("utf-8"))
        if recorded != expected:
            problems.append(f"{name}.svg is stale; run python scripts/render_diagrams.py")
    return problems


def svg_problems(svg: str) -> list[str]:
    """Return constructs that would make an SVG unsafe or unreadable inside `<img>`."""
    markers = {"<foreignobject": "HTML labels", "<script": "a script"}
    lowered = svg.lower()
    return [reason for marker, reason in markers.items() if marker in lowered]


def matrix_problems(markdown: str) -> list[str]:
    """Return capabilities that are missing, repeated or in a state outside the three."""
    seen: list[str] = []
    problems: list[str] = []
    for line in markdown.splitlines():
        match = _MATRIX_ROW.match(line)
        if match is None:
            continue
        seen.append(match["id"])
        if match["status"] not in STATES:
            problems.append(f"{match['id']} has state {match['status']!r}")
    problems += [f"{name} is missing" for name in sorted(CAPABILITY_IDS - set(seen))]
    problems += [f"{name} is unknown" for name in sorted(set(seen) - CAPABILITY_IDS)]
    problems += [
        f"{name} is listed twice" for name in sorted({n for n in seen if seen.count(n) > 1})
    ]
    return problems


def _read(relative: str) -> str:
    return (REPOSITORY_ROOT / relative).read_text("utf-8")


# Public documents describe the current state only.


@pytest.mark.parametrize("document", PUBLIC_DOCUMENTS)
def test_public_documents_name_no_milestones_and_embed_no_mermaid(document: str) -> None:
    assert planning_mentions(_read(document)) == []


def test_milestone_references_and_inline_mermaid_are_reported() -> None:
    text = "Shipping in Milestone 2, after M1.\n\n```mermaid\nflowchart LR\n```\n"

    assert planning_mentions(text) == ["Milestone", "M1", "inline Mermaid block"]


def test_the_readme_embeds_no_images() -> None:
    assert list(_IMAGE.finditer(_read("README.md"))) == []


# Invariant 1: every diagram image matches its current source.


def test_every_diagram_image_is_rendered_from_its_current_source() -> None:
    assert sorted(ASSETS_DIR.glob("*.mmd"))
    assert diagram_problems(ASSETS_DIR) == []


def test_editing_a_source_without_rendering_it_is_reported(tmp_path: Path) -> None:
    source = b"flowchart LR\n  a --> b\n"
    digest = render_diagrams.source_digest(source)
    (tmp_path / "flow.svg").write_text(render_diagrams.stamp("<svg/>", digest), "utf-8")
    (tmp_path / "flow.mmd").write_bytes(source.replace(b"b", b"c"))

    assert diagram_problems(tmp_path) == [
        "flow.svg is stale; run python scripts/render_diagrams.py"
    ]


def test_an_unstamped_image_and_an_image_without_a_source_are_reported(tmp_path: Path) -> None:
    (tmp_path / "flow.mmd").write_text("flowchart LR\n", "utf-8")
    (tmp_path / "flow.svg").write_text("<svg/>", "utf-8")
    (tmp_path / "orphan.svg").write_text("<svg/>", "utf-8")

    assert diagram_problems(tmp_path) == [
        "orphan.svg has no Mermaid source",
        "flow.svg is stale; run python scripts/render_diagrams.py",
    ]


def test_line_endings_do_not_change_a_diagram_digest() -> None:
    source = b"flowchart LR\n  a --> b\n"

    assert render_diagrams.source_digest(source.replace(b"\n", b"\r\n")) == (
        render_diagrams.source_digest(source)
    )


# Invariant 2: images carry no HTML labels and no script.


def test_committed_images_contain_no_html_labels_or_scripts() -> None:
    images = sorted(ASSETS_DIR.glob("*.svg"))

    assert images
    assert {path.name: svg_problems(path.read_text("utf-8")) for path in images} == {
        path.name: [] for path in images
    }


def test_html_labels_and_scripts_in_an_image_are_reported() -> None:
    svg = "<svg><foreignObject><div>x</div></foreignObject><SCRIPT>1</SCRIPT></svg>"

    assert svg_problems(svg) == ["HTML labels", "a script"]


# Invariant 3: embedded images have alt text and exist.


def test_public_images_have_alt_text_and_exist() -> None:
    found = {
        document: image_problems(_read(document), (REPOSITORY_ROOT / document).parent)
        for document in PUBLIC_DOCUMENTS
    }

    assert any(_IMAGE.search(_read(document)) for document in PUBLIC_DOCUMENTS)
    assert found == {document: [] for document in PUBLIC_DOCUMENTS}


def test_an_image_without_alt_text_or_target_is_reported(tmp_path: Path) -> None:
    markdown = "![](missing.svg)\n![A diagram](https://example.invalid/x.svg)\n"

    assert image_problems(markdown, tmp_path) == [
        "missing.svg has no alt text",
        "missing.svg does not exist",
    ]


# Invariant 5: the public capability matrix uses the three states for every capability.


def test_the_capability_matrix_lists_every_capability_in_a_public_state() -> None:
    assert matrix_problems(CAPABILITIES.read_text("utf-8")) == []


def test_an_invented_state_a_missing_and_a_repeated_capability_are_reported() -> None:
    rows = [
        f"| Name | `{name}` | Available | |" for name in sorted(CAPABILITY_IDS - {"files.list"})
    ]
    rows += ["| Name | `camera.status` | Planned for M3 | |"]

    assert matrix_problems("\n".join(rows)) == [
        "camera.status has state 'Planned for M3'",
        "files.list is missing",
        "camera.status is listed twice",
    ]


# Invariant 6: the renderer publishes a complete, stamped image or nothing.


def _fake_cli(svg: str | None) -> object:
    """Stand in for the Mermaid CLI. A failing run writes partial output before failing,
    as a real one can, so writing straight to the final image would be caught."""

    def run(args: Sequence[str], *, check: bool) -> None:
        output = Path(args[list(args).index("--output") + 1])
        if svg is None:
            output.write_text("<svg partial", "utf-8")
            raise subprocess.CalledProcessError(1, list(args))
        output.write_text(svg, "utf-8")

    return run


def test_a_successful_render_replaces_the_image_with_a_stamped_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "flow.mmd"
    source.write_text("flowchart LR\n", "utf-8")
    (tmp_path / "flow.svg").write_text("old", "utf-8")
    monkeypatch.setattr(render_diagrams.subprocess, "run", _fake_cli("<svg/>"))

    render_diagrams.render(source, "npx")

    assert sorted(path.name for path in tmp_path.iterdir()) == ["flow.mmd", "flow.svg"]
    assert diagram_problems(tmp_path) == []


def test_a_failed_render_leaves_the_previous_image_and_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "flow.mmd"
    source.write_text("flowchart LR\n", "utf-8")
    (tmp_path / "flow.svg").write_text("old", "utf-8")
    monkeypatch.setattr(render_diagrams.subprocess, "run", _fake_cli(None))

    with pytest.raises(subprocess.CalledProcessError):
        render_diagrams.render(source, "npx")

    assert sorted(path.name for path in tmp_path.iterdir()) == ["flow.mmd", "flow.svg"]
    assert (tmp_path / "flow.svg").read_text("utf-8") == "old"
