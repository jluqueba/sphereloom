"""The server core must not depend on an agent framework or an LLM client.

This is the executable form of ADR-0010. The reasoning is worth restating, because a future
contributor will be tempted: SphereLoom's value is that it is a deterministic, auditable
device driver. If a model ever runs inside the server, then starting it needs credentials,
every status call costs tokens, and deleting footage flows through a non-deterministic
decision. The model belongs in the client.

The example agent in `examples/` deliberately does use Microsoft Agent Framework. This check
covers `src/sphereloom/` only.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ROOT = _REPO_ROOT / "src" / "sphereloom"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

#: Top-level modules the server core may never import.
BANNED_MODULES = frozenset(
    {
        "agent_framework",
        "autogen",
        "semantic_kernel",
        "langchain",
        "langgraph",
        "llama_index",
        "openai",
        "anthropic",
    }
)

#: Distributions that may never appear in the runtime dependency set.
BANNED_DISTRIBUTIONS = frozenset(
    {
        "agent-framework",
        "agent-framework-core",
        "agent-framework-devui",
        "autogen-agentchat",
        "semantic-kernel",
        "langchain",
        "llama-index",
        "openai",
        "anthropic",
    }
)


def _source_files() -> list[Path]:
    return sorted(_PACKAGE_ROOT.rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_package_has_source_files_to_check() -> None:
    """Guards against this check silently passing because it found nothing."""
    assert _source_files(), "no source files found; the boundary check would be vacuous"


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_no_module_imports_an_agent_framework(path: Path) -> None:
    offending = _imported_roots(path) & BANNED_MODULES

    assert not offending, (
        f"{path.relative_to(_REPO_ROOT).as_posix()} imports {sorted(offending)}. "
        "The SphereLoom server core stays free of agent and LLM libraries so it remains "
        "deterministic and credential-free. See ADR-0010."
    )


def test_runtime_dependencies_contain_no_agent_framework() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    requirements = project.get("dependencies", [])
    declared = {_distribution_name(requirement) for requirement in requirements}

    offending = declared & BANNED_DISTRIBUTIONS

    assert not offending, (
        f"pyproject.toml declares {sorted(offending)} as a runtime dependency. "
        "Agent frameworks belong to the example agent extra, never to the server. "
        "See ADR-0010."
    )


def _distribution_name(requirement: str) -> str:
    for separator in ("[", ">", "<", "=", "!", "~", ";", " "):
        requirement = requirement.split(separator)[0]
    return requirement.strip().lower()
