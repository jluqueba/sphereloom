"""The public documentation site publishes exactly what it should. See ADR-0015.

GitHub Pages builds `main` with Jekyll, which only offers an exclude list: anything not
excluded is published. These tests turn that into a fail-closed rule, so a new directory or
an internal document cannot reach the public site by omission, and so text that Jekyll
would evaluate as a template cannot break the build or vanish from a page.

Each check is a pure function, run once against the repository and once against an
injected case that must fail, so a check that silently stopped working would be noticed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SITE_CONFIG = REPOSITORY_ROOT / "_config.yml"

#: Top-level entries published on purpose. Everything else must be excluded in `_config.yml`
#: or be a dotfile or underscore file, which Jekyll skips unless it is listed in `include`.
TOP_LEVEL_PUBLISHED = frozenset(
    {"README.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "SECURITY.md"}
    | {"LICENSE", "docs"}
)

#: Entries directly under `docs/` published on purpose.
DOCS_PUBLISHED = frozenset({"CAPABILITIES.md", "DEVELOPER_GUIDE.md", "assets"})

#: Dotfiles that may be listed under `include`, because they are safe to publish.
SAFE_INCLUDES = frozenset({".env.example"})

#: Exclude patterns this module evaluates exactly: a plain path, optionally ending in a slash,
#: or `*.<extension>`. Anything else is rejected rather than approximated.
_PATTERN_FORM = re.compile(r"^(?:[\w.-]+(?:/[\w.-]+)*/?|\*\.\w+)$")

#: Liquid output and tag delimiters. GitHub's Jekyll renders Markdown pages through Liquid,
#: so either sequence in a published page breaks the build or silently drops text.
_LIQUID = re.compile(r"\{\{|\{%")


def load_config(path: Path = SITE_CONFIG) -> dict[str, list[str]]:
    """Read the `include` and `exclude` lists from a Jekyll configuration file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    lists: dict[str, list[str]] = {}
    for key in ("include", "exclude"):
        value = raw.get(key, [])
        assert isinstance(value, list)
        assert all(isinstance(item, str) for item in value)
        lists[key] = [str(item) for item in value]
    return lists


def tracked_paths() -> list[str]:
    """Return every path git tracks, failing rather than skipping if git cannot answer."""
    git = shutil.which("git")
    if git is None:
        pytest.fail("git is required to list the tracked files the site could publish.")
    # Fixed arguments; nothing from outside the test reaches the command line.
    result = subprocess.run(  # noqa: S603
        [git, "ls-files", "-z"], cwd=REPOSITORY_ROOT, capture_output=True, check=False
    )
    if result.returncode != 0 or not result.stdout:
        pytest.fail("git ls-files returned nothing; the site check cannot run blind.")
    return [path for path in result.stdout.decode("utf-8").split("\0") if path]


def is_excluded(path: str, patterns: Iterable[str]) -> bool:
    """Whether Jekyll's exclude list removes `path` from the site."""
    for pattern in patterns:
        if pattern.startswith("*."):
            if fnmatchcase(path.rsplit("/", 1)[-1], pattern):
                return True
            continue
        base = pattern.rstrip("/")
        if path == base or path.startswith(f"{base}/"):
            return True
    return False


def is_visible(name: str, includes: Iterable[str]) -> bool:
    """Whether Jekyll considers an entry at all: it skips dot and underscore names."""
    return not name.startswith((".", "_")) or name in set(includes)


def unclassified_paths(paths: Iterable[str], config: Mapping[str, list[str]]) -> list[str]:
    """Return entries that would be published without anyone having decided so."""
    excludes, includes = config["exclude"], config["include"]
    problems: set[str] = set()
    for path in paths:
        parts = path.split("/")
        top = parts[0]
        if top in TOP_LEVEL_PUBLISHED:
            if top == "docs" and len(parts) > 1:
                entry = f"docs/{parts[1]}"
                if parts[1] not in DOCS_PUBLISHED and not is_excluded(entry, excludes):
                    problems.add(entry)
            continue
        if top.startswith((".", "_")):
            # Skipped by Jekyll unless forced in; forcing one in is a decision of its own.
            if top in includes and top not in SAFE_INCLUDES:
                problems.add(top)
            continue
        if not is_excluded(top, excludes):
            problems.add(top)
    return sorted(problems)


def published_markdown(paths: Iterable[str], config: Mapping[str, list[str]]) -> list[str]:
    """Return the tracked Markdown files the site publishes."""
    return [
        path
        for path in paths
        if path.endswith(".md")
        and all(is_visible(part, config["include"]) for part in path.split("/"))
        and not is_excluded(path, config["exclude"])
    ]


def liquid_markers(markdown: str) -> list[int]:
    """Return the line numbers that contain a Liquid delimiter."""
    return [number for number, line in enumerate(markdown.splitlines(), 1) if _LIQUID.search(line)]


# Invariant 1: nothing is published by omission.


def test_every_tracked_path_is_published_on_purpose_or_excluded() -> None:
    assert unclassified_paths(tracked_paths(), load_config()) == []


@pytest.mark.parametrize(("returncode", "stdout"), [(128, b""), (0, b"")], ids=["error", "empty"])
def test_the_check_fails_rather_than_skips_when_git_cannot_list_files(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: bytes
) -> None:
    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, returncode, stdout, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Catch any outcome, then require a failure: a skip would also leave this block, and a
    # check that skips when it cannot read its input would report success.
    with pytest.raises(BaseException) as outcome:  # noqa: PT011
        tracked_paths()

    assert isinstance(outcome.value, pytest.fail.Exception)


def test_a_new_top_level_directory_is_reported_until_classified() -> None:
    paths = [*tracked_paths(), "newtool/run.sh"]

    assert unclassified_paths(paths, load_config()) == ["newtool"]


def test_a_new_entry_under_docs_is_reported_until_classified() -> None:
    paths = [*tracked_paths(), "docs/drafts/idea.md"]

    assert unclassified_paths(paths, load_config()) == ["docs/drafts"]


# Invariant 2: internal documents never reach the site.


def test_internal_documents_are_excluded_from_the_site() -> None:
    config = load_config()

    assert is_excluded("docs/internal/adr/README.md", config["exclude"])
    assert all(
        not path.startswith("docs/internal/")
        for path in published_markdown(tracked_paths(), config)
    )


def test_dropping_the_internal_exclusion_is_reported() -> None:
    config = load_config()
    config["exclude"] = [item for item in config["exclude"] if item != "docs/internal/"]

    assert "docs/internal" in unclassified_paths(tracked_paths(), config)


# Invariant 3: only safe dotfiles are forced into the site.


def test_only_safe_dotfiles_are_included() -> None:
    assert set(load_config()["include"]) <= SAFE_INCLUDES


def test_including_a_dotfile_outside_the_safe_list_is_reported() -> None:
    config = load_config()
    config["include"] = [*config["include"], ".agerecipients"]

    assert ".agerecipients" in unclassified_paths(tracked_paths(), config)


# Invariant 4: the exclude list uses only forms this module evaluates exactly.


def test_exclude_patterns_use_only_forms_the_check_evaluates_exactly() -> None:
    assert [p for p in load_config()["exclude"] if not _PATTERN_FORM.match(p)] == []


@pytest.mark.parametrize("pattern", ["docs/[a-z]*", "**/*.py", "src/*/"])
def test_glob_forms_the_check_cannot_evaluate_are_rejected(pattern: str) -> None:
    assert _PATTERN_FORM.match(pattern) is None


# Invariant 5: published Markdown contains nothing Jekyll would evaluate as Liquid.


def test_published_markdown_contains_no_liquid_delimiters() -> None:
    config = load_config()
    documents = published_markdown(tracked_paths(), config)
    found = {
        path: liquid_markers((REPOSITORY_ROOT / path).read_text("utf-8")) for path in documents
    }

    assert "README.md" in found
    assert found == {path: [] for path in documents}


def test_liquid_delimiters_are_reported() -> None:
    markdown = "Plain text.\nThe gate reads ${{ toJSON(needs) }}.\n{% raw %}\n"

    assert liquid_markers(markdown) == [2, 3]
