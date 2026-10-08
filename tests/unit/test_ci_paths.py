"""CI runs the test suite for every change except an explicit list of unread paths.

The workflow skips the test matrix when no changed file counts for its `code` filter, and
`ci-gate` still passes. An allow-list of paths fails open: a new file, or one the suite
reads but nobody listed, can change without the test that guards it ever running. The
filter is therefore a skip-list, `**` minus named exclusions, and these tests keep it so.

The model below mirrors the action's `some-with-excludes` quantifier: a path counts when it
matches a pattern and no negated pattern.
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

#: The only paths allowed to skip the test matrix: none of them is read by any test.
#: Adding one is a deliberate decision and must be made here as well as in the workflow.
ALLOWED_SKIPS = frozenset(
    {
        "!docs/internal/**",
        "!.github/instructions/**",
        "!.github/ISSUE_TEMPLATE/**",
        "!.github/copilot-instructions.md",
        "!.github/PULL_REQUEST_TEMPLATE.md",
        "!.github/CODEOWNERS",
        "!.github/dependabot.yml",
        "!.markdownlint.json",
        "!.markdownlint-cli2.jsonc",
    }
)

#: Tracked paths the suite reads, which must never be skipped whatever the exclusions say.
_READ_PATTERNS = (
    re.compile(r"^(?:src|tests|scripts)/"),
    re.compile(r"^[^/]+\.md$"),
    re.compile(r"^docs/[^/]+\.md$"),
    re.compile(r"^docs/assets/"),
    re.compile(r"^\.github/workflows/"),
    re.compile(r"^(?:pyproject\.toml|ruff\.toml|mypy\.ini|\.env\.example|_config\.yml)$"),
)


def filter_step(workflow: Path = WORKFLOW) -> dict[str, object]:
    """Return the `with` block of the change-detection step."""
    jobs = yaml.safe_load(workflow.read_text("utf-8"))["jobs"]
    step = next(s for s in jobs["changes"]["steps"] if s.get("id") == "filter")
    block = step["with"]
    assert isinstance(block, dict)
    return block


def code_filter(workflow: Path = WORKFLOW) -> list[str]:
    """Return the patterns of the `code` change filter."""
    filters = yaml.safe_load(str(filter_step(workflow)["filters"]))
    assert isinstance(filters["code"], list)
    return [str(item) for item in filters["code"]]


def glob_matches(pattern: str, path: str) -> bool:
    """Match a picomatch-style glob: `**` crosses directories, `*` does not."""
    regex = re.escape(pattern).replace(r"\*\*", "\0").replace(r"\*", "[^/]*").replace("\0", ".*")
    return re.fullmatch(regex, path) is not None


def triggers(path: str, patterns: Iterable[str]) -> bool:
    """Whether `path` counts for a filter under the `some-with-excludes` quantifier."""
    rules = list(patterns)
    positive = [p for p in rules if not p.startswith("!")]
    negative = [p[1:] for p in rules if p.startswith("!")]
    return any(glob_matches(p, path) for p in positive) and not any(
        glob_matches(p, path) for p in negative
    )


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
    return sorted(path for path in paths if any(p.match(path) for p in _READ_PATTERNS))


def test_the_filter_uses_the_quantifier_that_honours_exclusions() -> None:
    assert filter_step()["predicate-quantifier"] == "some-with-excludes"


def test_the_code_filter_is_everything_minus_the_allowed_skips() -> None:
    patterns = code_filter()

    assert [p for p in patterns if not p.startswith("!")] == ["**"]
    assert {p for p in patterns if p.startswith("!")} == ALLOWED_SKIPS


def test_a_new_path_runs_the_test_matrix() -> None:
    for path in ("newtool/run.sh", ".nojekyll", "_posts/note.md", "docs/drafts/idea.md"):
        assert triggers(path, code_filter()), path


def test_no_file_the_suite_reads_can_be_skipped() -> None:
    inputs = read_inputs(tracked_paths())

    assert {"pyproject.toml", ".github/workflows/ci.yml", "_config.yml", "README.md"} <= set(inputs)
    assert [path for path in inputs if not triggers(path, code_filter())] == []


def test_every_tracked_path_outside_the_skips_runs_the_test_matrix() -> None:
    patterns = code_filter()
    skipped = [path for path in tracked_paths() if not triggers(path, patterns)]

    assert all(any(glob_matches(s[1:], path) for s in ALLOWED_SKIPS) for path in skipped)


def test_an_exclusion_covering_a_read_file_is_reported() -> None:
    patterns = [*code_filter(), "!pyproject.toml", "!scripts/**"]

    blocked = [p for p in read_inputs(tracked_paths()) if not triggers(p, patterns)]

    assert "pyproject.toml" in blocked
    assert any(path.startswith("scripts/") for path in blocked)


@pytest.mark.parametrize(
    ("patterns", "path", "expected"),
    [
        (["**"], ".env.example", True),
        (["**", "!docs/internal/**"], "docs/internal/adr/README.md", False),
        (["**", "!docs/internal/**"], "docs/CAPABILITIES.md", True),
        (["*.md"], "docs/README.md", False),
        (["**", "!.github/CODEOWNERS"], ".github/CODEOWNERS", False),
    ],
)
def test_matching_follows_some_with_excludes(
    patterns: list[str], path: str, expected: bool
) -> None:
    assert triggers(path, patterns) is expected
