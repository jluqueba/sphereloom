"""Repository-wide rules that a reviewer should never have to check by hand.

Each rule is a pure function, run once against the repository and once against an injected
case that must fail, so a rule that silently stopped working would be noticed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"

#: A `uses:` line pinned the only accepted way: `owner/repo[/path]@<full commit SHA>`
#: followed by a comment naming the release, which is what Dependabot reads and updates.
_PINNED = re.compile(
    r"^\s*(?:-\s+)?uses:\s+[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}\s+#\s+v\d+(?:\.\d+)*\s*$"
)
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s")
#: Actions inside this repository are reviewed with it, so they need no pin.
_LOCAL = re.compile(r"^\s*(?:-\s+)?uses:\s+\./")


def unpinned_actions(lines: Iterable[str]) -> list[str]:
    """Return every `uses:` line that is not pinned to a commit SHA with its release."""
    return [
        line.strip()
        for line in lines
        if _USES.match(line) and not _LOCAL.match(line) and not _PINNED.match(line)
    ]


def workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, "no workflow found; the pinning rule would pass on nothing"
    return files


# Rule: every action a workflow runs is pinned to a full commit SHA (ADR-0009).


@pytest.mark.parametrize("workflow", workflow_files(), ids=lambda path: path.name)
def test_every_action_is_pinned_to_a_commit_sha(workflow: Path) -> None:
    assert unpinned_actions(workflow.read_text("utf-8").splitlines()) == []


def test_the_rule_sees_every_action_the_workflows_use() -> None:
    # Counted from the parsed YAML, independently of the line patterns above, so a pattern
    # that stopped matching would not let the pinning rule pass on nothing.
    expected = 0
    detected = 0
    for path in workflow_files():
        text = path.read_text("utf-8")
        for job in yaml.safe_load(text)["jobs"].values():
            expected += "uses" in job
            expected += sum("uses" in step for step in job.get("steps", []))
        detected += sum(1 for line in text.splitlines() if _USES.match(line))

    assert expected > 0
    assert detected == expected


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: actions/checkout@v7 # v7.0.1",
        "        uses: astral-sh/setup-uv@v10.2.0",
        "      - uses: actions/checkout@main",
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b # v7.0.1",
        "      - uses: actions/checkout@3D3C42E5AAC5BA805825DA76410C181273BA90B1 # v7.0.1",
        "      - uses: docker://alpine:3.20",
    ],
    ids=["major-tag", "exact-tag", "branch", "no-release-comment", "short-sha", "upper", "docker"],
)
def test_an_unpinned_action_is_reported(line: str) -> None:
    assert unpinned_actions([line]) == [line.strip()]


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
        "        uses: github/codeql-action/init@3d3c42e5aac5ba805825da76410c181273ba90b1 # v3",
        "      - uses: ./.github/actions/setup",
        "      - name: not an action",
    ],
    ids=["pinned", "pinned-subpath", "local", "other-key"],
)
def test_a_pinned_or_local_action_is_accepted(line: str) -> None:
    assert unpinned_actions([line]) == []
