"""Repository-wide rules that a reviewer should never have to check by hand.

Each rule is a pure function, run once against the repository and once against an injected
case that must fail, so a rule that silently stopped working would be noticed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"

#: The only accepted reference: `owner/repo[/path]@<full lowercase commit SHA>`.
_PINNED_REF = re.compile(r"[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}")
#: Actions inside this repository are reviewed with it, so they need no pin.
_LOCAL_REF = re.compile(r"\./\S+")
#: The full release a pin corresponds to, which Dependabot reads and updates.
_RELEASE_COMMENT = re.compile(r"#\s+v\d+\.\d+\.\d+\s*$")


def unpinned_actions(text: str) -> list[str]:
    """Return every `uses` reference in a workflow that is not pinned with its release.

    The workflow is read as YAML nodes rather than as lines, so quoting a key, using a flow
    mapping or putting `uses:` text inside a `run` block cannot hide an action or fake one.
    Every `uses` key anywhere in the document is checked, which errs towards reporting. The
    release comment is read from the source line of the value itself, because YAML parsing
    drops comments.
    """
    lines = text.splitlines()
    problems: list[str] = []

    def walk(node: yaml.Node) -> None:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.value == "uses":
                    check(value)
                walk(value)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                walk(item)

    def check(value: yaml.Node) -> None:
        if not isinstance(value, yaml.ScalarNode):
            problems.append(f"line {value.start_mark.line + 1}: `uses` is not a string")
            return
        reference = value.value
        if _LOCAL_REF.fullmatch(reference):
            return
        rest_of_line = lines[value.end_mark.line][value.end_mark.column :]
        if not _PINNED_REF.fullmatch(reference) or not _RELEASE_COMMENT.search(rest_of_line):
            problems.append(f"line {value.start_mark.line + 1}: {reference}")

    root = yaml.compose(text)
    if root is not None:
        walk(root)
    return problems


def workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, "no workflow found; the pinning rule would pass on nothing"
    return files


def workflow(*steps: str) -> str:
    """Build a one-job workflow from step lines, indented as list items."""
    body = "\n".join(f"      {step}" for step in steps)
    return f"on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n{body}\n"


PINNED = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


# Rule: every action a workflow runs is pinned to a full commit SHA (ADR-0009).


@pytest.mark.parametrize("path", workflow_files(), ids=lambda path: path.name)
def test_every_action_is_pinned_to_a_commit_sha(path: Path) -> None:
    text = path.read_text("utf-8")

    assert "uses" in text
    assert unpinned_actions(text) == []


@pytest.mark.parametrize(
    "step",
    [
        "- uses: actions/checkout@v7 # v7.0.1",
        "- uses: astral-sh/setup-uv@v10.2.0",
        "- uses: actions/checkout@main # v7.0.1",
        f"- uses: {PINNED}",
        f"- uses: {PINNED[:-1]} # v7.0.1",
        f"- uses: {PINNED.upper()} # v7.0.1",
        f"- uses: {PINNED} # v7",
        f"- uses: {PINNED} # v7.0",
        "- uses: docker://alpine:3.20",
        '- "uses": evil/action@main',
        "- 'uses': evil/action@main",
        "- {uses: evil/action@main}",
        "- uses: [evil/action@main]",
    ],
    ids=[
        "major-tag",
        "exact-tag",
        "branch",
        "no-release-comment",
        "short-sha",
        "upper",
        "major-only-comment",
        "minor-only-comment",
        "docker",
        "double-quoted-key",
        "single-quoted-key",
        "flow-mapping",
        "not-a-string",
    ],
)
def test_an_unpinned_action_is_reported(step: str) -> None:
    assert len(unpinned_actions(workflow(step))) == 1


def test_uses_text_inside_a_run_block_neither_hides_nor_fakes_an_action() -> None:
    text = workflow(
        '- "uses": evil/action@main',
        "- run: |",
        f"    echo uses: {PINNED} # v7.0.1",
        f"    uses: {PINNED} # v7.0.1",
    )

    assert unpinned_actions(text) == ["line 6: evil/action@main"]


def test_a_reusable_workflow_job_is_checked() -> None:
    text = "on: push\njobs:\n  call:\n    uses: org/repo/.github/workflows/x.yml@main\n"

    assert unpinned_actions(text) == ["line 4: org/repo/.github/workflows/x.yml@main"]


@pytest.mark.parametrize(
    "step",
    [
        f"- uses: {PINNED} # v7.0.1",
        "- uses: github/codeql-action/init@3d3c42e5aac5ba805825da76410c181273ba90b1 # v3.0.0",
        "- uses: ./.github/actions/setup",
        "- name: not an action",
    ],
    ids=["pinned", "pinned-subpath", "local", "other-key"],
)
def test_a_pinned_or_local_action_is_accepted(step: str) -> None:
    assert unpinned_actions(workflow(step)) == []


# Rule: JSON from a device is decoded only by the strict, bounded decoder (ADR-0017).

#: Where the rule applies: everything that can talk to a camera.
DECODING_ROOTS = (REPOSITORY_ROOT / "src" / "sphereloom", REPOSITORY_ROOT / "scripts")

#: Paths allowed to decode JSON themselves, each with the reason. A directory ends in `/`.
DECODING_EXEMPTIONS = {
    "src/sphereloom/domain/payloads.py": "it is the strict decoder",
    "src/sphereloom/adapters/fake/": (
        "the fake camera parses requests from SphereLoom's own client, not device responses"
    ),
    "scripts/copilot_review_gate.py": (
        "it runs with the system Python before the package is installed, and reads the "
        "GitHub API, not a device"
    ),
}

#: Names that decode JSON when imported from the `json` package or reached through it.
_JSON_DECODERS = frozenset({"load", "loads", "JSONDecoder", "decoder", "scanner"})


def lenient_json_decoding(source: str) -> list[str]:
    """Return every place `source` decodes JSON without the strict decoder.

    That is `JSONDecoder` reached as an attribute (`json.JSONDecoder`,
    `json.decoder.JSONDecoder`, or through a module alias); `load`, `loads`, `decoder` or
    `scanner` on the `json` module under any alias; an import from `json.decoder` or
    `json.scanner`, a star import from `json`, or an import of a decoding name from it; and
    any call to a `.json(...)` method, which is how HTTP and web libraries decode a body.
    It errs towards reporting.
    """
    tree = ast.parse(source)
    modules = {"json"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname for alias in node.names if alias.name == "json" and alias.asname
            )

    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith("json."):
                problems.append(f"line {node.lineno}: from {node.module} import")
            elif node.module == "json":
                problems.extend(
                    f"line {node.lineno}: from json import {alias.name}"
                    for alias in node.names
                    if alias.name in _JSON_DECODERS or alias.name == "*"
                )
        elif isinstance(node, ast.Attribute) and (
            node.attr == "JSONDecoder"
            or (
                node.attr in _JSON_DECODERS
                and isinstance(node.value, ast.Name)
                and node.value.id in modules
            )
        ):
            problems.append(f"line {node.lineno}: .{node.attr}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "json"
        ):
            problems.append(f"line {node.lineno}: .json()")
    return problems


def _is_exempt(relative: str) -> bool:
    return any(
        relative.startswith(path) if path.endswith("/") else relative == path
        for path in DECODING_EXEMPTIONS
    )


def decoding_sources() -> list[Path]:
    files = sorted(path for root in DECODING_ROOTS for path in root.rglob("*.py"))
    assert files, "no source found; the decoding rule would pass on nothing"
    return [path for path in files if not _is_exempt(path.relative_to(REPOSITORY_ROOT).as_posix())]


@pytest.mark.parametrize(
    "path", decoding_sources(), ids=lambda path: path.relative_to(REPOSITORY_ROOT).as_posix()
)
def test_json_is_decoded_only_by_the_strict_decoder(path: Path) -> None:
    assert lenient_json_decoding(path.read_text("utf-8")) == []


@pytest.mark.parametrize("path", sorted(DECODING_EXEMPTIONS))
def test_every_decoding_exemption_names_an_existing_path(path: str) -> None:
    assert (REPOSITORY_ROOT / path).exists()


@pytest.mark.parametrize(
    "source",
    [
        "import json\njson.loads(body)\n",
        "import json\njson.load(handle)\n",
        "import json\njson.JSONDecoder().decode(body)\n",
        "import json as j\nj.loads(body)\n",
        "from json import loads\n",
        "from json import loads as parse\n",
        "response.json()\n",
        "await request.json()\n",
        "response.json(object_hook=dict)\n",
        "import json\njson.decoder.JSONDecoder().decode(body)\n",
        "from json import decoder\ndecoder.JSONDecoder().decode(body)\n",
        "import json.decoder as d\nd.JSONDecoder().decode(body)\n",
        "from json.decoder import JSONDecoder\n",
        "from json import JSONDecoder\nJSONDecoder().decode(body)\n",
        "from json import *\nloads(body)\n",
    ],
    ids=[
        "loads",
        "load",
        "decoder",
        "aliased-module",
        "from-import",
        "aliased-from-import",
        "response-json",
        "awaited-json",
        "json-with-arguments",
        "nested-module",
        "imported-submodule",
        "aliased-submodule",
        "from-submodule",
        "imported-decoder-class",
        "star-import",
    ],
)
def test_lenient_json_decoding_is_reported(source: str) -> None:
    assert lenient_json_decoding(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "import json\njson.dumps(value)\n",
        "from json import dumps\n",
        "decode_device_json(body)\n",
        "settings.json_path\n",
    ],
    ids=["dumps", "from-import-dumps", "strict-decoder", "attribute-named-like-json"],
)
def test_strict_decoding_and_encoding_are_accepted(source: str) -> None:
    assert lenient_json_decoding(source) == []


# Rule: only `sphereloom.logging` touches the standard logging module, and every event it
# logs uses allow-listed fields (ADR-0018).

SOURCE_ROOT = REPOSITORY_ROOT / "src" / "sphereloom"
LOGGING_MODULE = SOURCE_ROOT / "logging.py"

#: Enumeration types whose functional API builds a new enumeration from data. The logging
#: module trusts SphereLoom's enumerations because their values are written in code.
_ENUM_TYPES = frozenset({"Enum", "StrEnum", "IntEnum", "Flag", "IntFlag"})


def logging_violations(source: str) -> list[str]:
    """Return every way `source` could log text outside the allow-list.

    That is an import of the standard `logging` module or anything from it; a `log_event`
    call whose event is not written as `LogEvent.<NAME>`, that unpacks keywords with `**`,
    or that passes a keyword neither in `FIELDS` nor `error`; a `WorkspacePath` built
    anywhere but the logging module, which bypasses `workspace_relative`; an enumeration
    built at run time, whose values could be data the logging module would then trust; and a
    uvicorn server
    configured without `log_config=None`, which installs uvicorn's own text handlers.
    """
    from sphereloom.logging import FIELDS

    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            problems.extend(
                f"line {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name == "logging" or alias.name.startswith("logging.")
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            if node.module == "logging" or node.module.startswith("logging."):
                problems.append(f"line {node.lineno}: from {node.module} import")
        elif isinstance(node, ast.Call):
            name = _called_name(node)
            if name == "log_event":
                problems.extend(_log_event_problems(node, FIELDS))
            elif name == "WorkspacePath":
                problems.append(
                    f"line {node.lineno}: WorkspacePath built outside the logging module"
                )
            elif name in _ENUM_TYPES and (node.args or node.keywords):
                problems.append(f"line {node.lineno}: enumeration built at run time")
            elif name in {"Config", "run"} and _is_uvicorn(node) and not _disables_log_config(node):
                problems.append(f"line {node.lineno}: uvicorn without log_config=None")
    return problems


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_uvicorn(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
    )


def _disables_log_config(node: ast.Call) -> bool:
    return any(
        keyword.arg == "log_config"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is None
        for keyword in node.keywords
    )


def _log_event_problems(node: ast.Call, fields: frozenset[str]) -> list[str]:
    problems: list[str] = []
    event = node.args[0] if node.args else None
    if not (
        isinstance(event, ast.Attribute)
        and isinstance(event.value, ast.Name)
        and event.value.id == "LogEvent"
    ):
        problems.append(f"line {node.lineno}: log_event without a literal LogEvent member")
    if len(node.args) > 1:
        problems.append(f"line {node.lineno}: log_event with extra positional arguments")
    for keyword in node.keywords:
        if keyword.arg is None:
            problems.append(f"line {node.lineno}: log_event with **keywords")
        elif keyword.arg not in fields and keyword.arg != "error":
            problems.append(f"line {node.lineno}: log_event field {keyword.arg}")
    return problems


def logging_sources() -> list[Path]:
    files = sorted(SOURCE_ROOT.rglob("*.py"))
    assert LOGGING_MODULE in files, "the logging module moved; the rule would check nothing"
    return [path for path in files if path != LOGGING_MODULE]


@pytest.mark.parametrize(
    "path", logging_sources(), ids=lambda path: path.relative_to(REPOSITORY_ROOT).as_posix()
)
def test_logging_goes_only_through_allow_listed_events(path: Path) -> None:
    assert logging_violations(path.read_text("utf-8")) == []


def test_the_rule_sees_every_log_event_call() -> None:
    """At least one checked module logs, so the call check is not vacuous."""
    assert any("log_event(" in path.read_text("utf-8") for path in logging_sources())


@pytest.mark.parametrize(
    "source",
    [
        "import logging\n",
        "import logging.handlers\n",
        "import logging as log\n",
        "from logging import getLogger\n",
        "from logging.handlers import RotatingFileHandler\n",
        "log_event(event)\n",
        'log_event("server_started")\n',
        "log_event(LogEvent.SERVER_STARTED, details)\n",
        "log_event(LogEvent.SERVER_STARTED, **fields)\n",
        "log_event(LogEvent.SERVER_STARTED, serial=value)\n",
        "logging_module.log_event(LogEvent.SERVER_STARTED, message=text)\n",
        "uvicorn.run(app)\n",
        "uvicorn.Config(app, log_config=LOGGING_CONFIG)\n",
        "WorkspacePath('/home/alice/clip.mp4')\n",
        "logging_module.WorkspacePath(text)\n",
        "StrEnum('Device', {'VALUE': value}, module='sphereloom.adapters')\n",
        "enum.Enum('Device', names)\n",
    ],
    ids=[
        "import",
        "import-submodule",
        "aliased-import",
        "from-import",
        "from-submodule",
        "event-variable",
        "event-string",
        "positional-field",
        "keyword-unpacking",
        "unknown-field",
        "attribute-call",
        "uvicorn-run-default",
        "uvicorn-config-custom",
        "workspace-path",
        "workspace-path-attribute",
        "functional-str-enum",
        "functional-enum",
    ],
)
def test_logging_outside_the_allow_list_is_reported(source: str) -> None:
    assert logging_violations(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "from sphereloom.logging import LogEvent, log_event\n",
        "from .logging import log_event\n",
        "log_event(LogEvent.SERVER_STARTED, transport=Transport.STDIO)\n",
        "log_event(LogEvent.SERVER_STOPPED, error=exc)\n",
        "uvicorn.run(app, host=host, log_config=None)\n",
        "server.run('stdio')\n",
        "class Transport(StrEnum):\n    STDIO = 'stdio'\n",
        "Transport('stdio')\n",
    ],
    ids=[
        "project-logging",
        "relative-import",
        "allowed-field",
        "error",
        "uvicorn-without-logging",
        "other-run",
        "enum-class",
        "enum-lookup",
    ],
)
def test_allow_listed_logging_is_accepted(source: str) -> None:
    assert logging_violations(source) == []
