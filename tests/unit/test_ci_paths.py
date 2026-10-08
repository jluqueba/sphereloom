"""CI runs the test suite whenever a file the suite reads changes.

The CI workflow skips the test matrix when a pull request touches no path in its `code`
filter, and `ci-gate` still passes. A file the suite reads but the filter omits can
therefore change, and break the rule a test guards, without that test ever running.

The inputs are derived from the repository rather than listed by hand, so a new script,
public document or diagram is covered as soon as it is tracked.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"

#: Single files outside the code tree that tests read.
READ_FILES = frozenset({".env.example", "_config.yml"})

#: Tracked paths the suite reads: tests, the package, maintainer scripts it imports, and the
#: public documentation it checks (top-level and `docs/` Markdown, and `docs/assets`).
_READ_PATTERNS = (
    re.compile(r"^(?:src|tests|scripts)/"),
    re.compile(r"^[^/]+\.md$"),
    re.compile(r"^docs/[^/]+\.md$"),
    re.compile(r"^docs/assets/"),
)


def code_filter(workflow: Path = WORKFLOW) -> list[str]:
    """Return the globs of the `code` change filter in the CI workflow."""
    jobs = yaml.safe_load(workflow.read_text("utf-8"))["jobs"]
    step = next(s for s in jobs["changes"]["steps"] if s.get("id") == "filter")
    filters = yaml.safe_load(step["with"]["filters"])
    assert isinstance(filters["code"], list)
    return [str(item) for item in filters["code"]]


def glob_matches(pattern: str, path: str) -> bool:
    """Match a path-filter glob: `**` crosses directories, `*` does not."""
    regex = re.escape(pattern).replace(r"\*\*", "\0").replace(r"\*", "[^/]*").replace("\0", ".*")
    return re.fullmatch(regex, path) is not None


def tracked_paths() -> list[str]:
    """Return every path git tracks, failing rather than skipping if git cannot answer."""
    git = shutil.which("git")
    if git is None:
        pytest.fail("git is required to list the files the suite reads.")
    # Fixed arguments; nothing from outside the test reaches the command line.
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z"], cwd=REPOSITORY_ROOT, capture_output=True, check=False
    )
    if result.returncode != 0 or not result.stdout:
        pytest.fail("git ls-files returned nothing; the filter check cannot run blind.")
    return [path for path in result.stdout.decode("utf-8").split("\0") if path]


def read_inputs(paths: Iterable[str]) -> list[str]:
    """Return the tracked paths the test suite reads."""
    return sorted(
        path
        for path in paths
        if path in READ_FILES or any(pattern.match(path) for pattern in _READ_PATTERNS)
    )


def uncovered(paths: Iterable[str], globs: Iterable[str]) -> list[str]:
    """Return the inputs no `code` filter glob matches."""
    patterns = list(globs)
    return [path for path in read_inputs(paths) if not any(glob_matches(g, path) for g in patterns)]


def test_every_file_the_suite_reads_triggers_the_test_matrix() -> None:
    paths = tracked_paths()

    assert {"_config.yml", ".env.example", "README.md", "docs/CAPABILITIES.md"} <= set(
        read_inputs(paths)
    )
    assert uncovered(paths, code_filter()) == []


def test_a_read_file_missing_from_the_filter_is_reported() -> None:
    globs = [g for g in code_filter() if g not in {"scripts/**", "_config.yml"}]

    found = uncovered(tracked_paths(), globs)

    assert "_config.yml" in found
    assert any(path.startswith("scripts/") for path in found)


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("docs/assets/**", "docs/assets/a/b.svg", True),
        ("_config.yml", "_config.yml", True),
        ("_config.yml", "x_config.yml", False),
    ],
)
def test_glob_semantics_match_the_path_filter(pattern: str, path: str, expected: bool) -> None:
    assert glob_matches(pattern, path) is expected
