"""Rules that must hold across the whole codebase, enforced rather than written down.

Every rule here exists because the same defect was found, fixed at the one line a reviewer
named, and then found again somewhere else. A rule recorded in a document is a rule that
gets violated by the next commit; a rule recorded as a failing test is not.

Exemptions are inline markers rather than a central allowlist:

    message = f"...{constant!r}"  # rule-exempt(repr): the decoder passes three fixed tokens

A central list keyed by function would hide a *new* offender added to a function that was
already exempt, which is the same instance-versus-class mistake these rules exist to catch.
A marker exempts one line, states its reason next to the code, and shows up in review.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (REPO_ROOT / "src", REPO_ROOT / "scripts")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        if root.exists():
            files.extend(sorted(root.rglob("*.py")))
    return files


def _relative(path: Path) -> str:
    """Path relative to the repository root.

    Computed with `relative_to` rather than by splitting on the project name: CI checks the
    repository out at `/home/runner/work/sphereloom/sphereloom`, so splitting on the first
    occurrence of the name yields a different key there than it does locally.
    """
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def _comments(path: Path) -> dict[int, str]:
    """Real comment tokens by line number.

    Searching the raw text for `#` would accept a hash inside a string literal, which is
    how a rule that demands an explanation gets satisfied without one.
    """
    found: dict[int, str] = {}
    source = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            found[token.start[0]] = token.string
    return found


def _is_exempt(comments: dict[int, str], lineno: int, rule: str) -> bool:
    """Whether a marker for `rule` sits on this line or the line above it."""
    marker = f"rule-exempt({rule})"
    return any(marker in comments.get(line, "") for line in (lineno, lineno - 1))


def _fail(rule: str, guidance: str, offenders: list[str]) -> None:
    assert not offenders, (
        f"{guidance}\n\nIf a site is provably safe, mark it with "
        f"`# rule-exempt({rule}): <reason>` so the reason sits next to the code.\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------- repr in messages


def _repr_offenders(tree: ast.AST) -> list[int]:
    """Every way of rendering a value with `repr()`, not only `!r` in an f-string."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FormattedValue) and node.conversion == ord("r"):
            lines.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "repr":
                lines.append(node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            left = node.left
            if (
                isinstance(left, ast.Constant)
                and isinstance(left.value, str)
                and "%r" in left.value
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            target = node.func.value
            if (
                node.func.attr == "format"
                and isinstance(target, ast.Constant)
                and isinstance(target.value, str)
                and "!r" in target.value
            ):
                lines.append(node.lineno)
    return lines


def test_no_unbounded_repr_reaches_a_message() -> None:
    """`repr()` on untrusted input is neither bounded nor total.

    It copies the value whole into a message that reaches an error envelope, a log line or
    the terminal, and `repr()` of an integer wider than 4300 digits raises. Use
    `bounded_text`, which truncates and returns a marker instead of raising.
    """
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        comments = _comments(path)
        for lineno in _repr_offenders(tree):
            if not _is_exempt(comments, lineno, "repr"):
                offenders.append(f"{_relative(path)}:{lineno}")

    _fail(
        "repr",
        "`repr()` must not render a value that could come from a device, a tool call or "
        "the environment. Wrap it in `bounded_text`.",
        offenders,
    )


# ---------------------------------------------------------------- lenient JSON decoding


#: What may be taken from the `json` package. Everything else in it decodes -- `loads`,
#: `load`, `JSONDecoder` and its `decode` and `raw_decode`, the `decoder` and `scanner`
#: modules -- and Python's decoder accepts `NaN`, `Infinity` and `1e400`.
_JSON_ENCODING_ONLY = frozenset({"dumps", "dump", "JSONEncoder", "JSONDecodeError"})

#: Methods that decode JSON whatever object they are called on. `.json()` on a response or
#: a request caused both defects this rule was written for; `raw_decode` is the decoder's
#: second entry point and is reachable from an instance however its class was imported.
#: pydantic's parsers accept the same non-finite values by default, and pydantic is the
#: model library here, so `Model.model_validate_json(body)` is the natural line to write.
_DECODING_METHODS = frozenset(
    {"json", "raw_decode", "model_validate_json", "validate_json", "from_json"}
)


def _json_aliases(tree: ast.Module) -> set[str]:
    """Local names bound to the `json` package. `import json as _j` must not evade the rule."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(alias.asname or "json" for alias in node.names if alias.name == "json")
    return aliases


def _lenient_decode_offenders(tree: ast.Module) -> list[int]:
    """Every reference to a JSON decoding entry point, not only every call shape.

    Matching calls let each new shape through: `JSONDecoder().decode` was caught, then
    `JSONDecoder.raw_decode` and an aliased decoder were not. A decoder has to be reached
    by importing it or by reading it off the package, so those references are the offence;
    an alias then changes nothing, because the import that created it is already flagged.
    """
    aliases = _json_aliases(tree)
    lines: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "json":
            submodule = node.module != "json"
            if submodule or any(alias.name not in _JSON_ENCODING_ONLY for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            # A decoding function imported by name, such as `from pydantic_core import
            # from_json`, is then called bare, where no attribute is left to match.
            if any(alias.name in _DECODING_METHODS for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name.startswith("json.") for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Attribute):
            on_package = isinstance(node.value, ast.Name) and node.value.id in aliases
            if (on_package and node.attr not in _JSON_ENCODING_ONLY) or (
                node.attr in _DECODING_METHODS
            ):
                lines.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in aliases
        ):
            lines.append(node.lineno)

    return lines


@pytest.mark.parametrize(
    "source",
    [
        "import json\njson.loads(b)",
        "import json\njson.load(f)",
        "import json as j\nj.loads(b)",
        "from json import loads\nloads(b)",
        "from json import loads as parse\nparse(b)",
        "import json\njson.JSONDecoder().decode(b)",
        "import json\njson.JSONDecoder().raw_decode(b)",
        "from json import JSONDecoder\nJSONDecoder().raw_decode(b)",
        "from json import JSONDecoder as Decoder\nDecoder().decode(b)",
        "from json.decoder import JSONDecoder\nJSONDecoder().decode(b)",
        "import json.decoder\njson.decoder.JSONDecoder().decode(b)",
        "import json\nparse = json.loads",
        "import json\ngetattr(json, 'loads')(b)",
        "decoder.raw_decode(b)",
        "response.json()",
        "from pydantic import BaseModel\nModel.model_validate_json(b)",
        "from pydantic import TypeAdapter\nTypeAdapter(int).validate_json(b)",
        "from pydantic_core import from_json\nfrom_json(b)",
        "import pydantic_core\npydantic_core.from_json(b)",
    ],
)
def test_the_json_rule_catches_every_way_to_reach_a_decoder(source: str) -> None:
    """A gate that misses a shape stays green while the defect it guards against ships."""
    assert _lenient_decode_offenders(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "import json\njson.dumps(v)",
        "from json import dumps, JSONDecodeError",
        "import json\nexcept_type = json.JSONDecodeError",
        "data.decode('utf-8')",
        "from sphereloom.domain.payloads import strict_json_loads\nstrict_json_loads(b)",
    ],
)
def test_the_json_rule_leaves_encoding_and_text_decoding_alone(source: str) -> None:
    """A rule that flags everything gets exempted everywhere, which is the same as none."""
    assert not _lenient_decode_offenders(ast.parse(source))


def test_json_is_never_decoded_leniently() -> None:
    """Python's decoder accepts `NaN`, `Infinity` and overflowing literals such as `1e400`.

    None of these is valid JSON, and all of them defeat every numeric comparison and break
    re-serialisation. Decoding must go through `strict_json_loads`, which refuses them.
    """
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        comments = _comments(path)
        for lineno in _lenient_decode_offenders(tree):
            if not _is_exempt(comments, lineno, "json"):
                offenders.append(f"{_relative(path)}:{lineno}")

    _fail(
        "json",
        "Decode with `sphereloom.domain.payloads.strict_json_loads`, which refuses the "
        "non-finite values Python accepts by default.",
        offenders,
    )


# ---------------------------------------------------------------- broad except clauses


def _is_broad(caught: ast.expr | None) -> bool:
    """Whether this handler catches everything, including the tuple form."""
    if caught is None:
        return True
    candidates = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return any(
        isinstance(item, ast.Name) and item.id in {"Exception", "BaseException"}
        for item in candidates
    )


def test_no_broad_exception_is_swallowed_without_a_reason() -> None:
    """`except Exception` is occasionally right, but never silently.

    A handler that catches everything and says nothing hides the defect it was written for.
    Each one must carry a real comment, on the handler or immediately around it, naming
    what it absorbs and why that is safe.
    """
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        comments = _comments(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not _is_broad(node.type):
                continue
            # A comment may sit above the handler, on it, or as the first line of its body.
            window = range(node.lineno - 2, node.lineno + 3)
            if not any(line in comments for line in window):
                offenders.append(f"{_relative(path)}:{node.lineno}")

    _fail(
        "broad-except",
        "A broad `except` must carry a comment naming what it absorbs and why that is safe.",
        offenders,
    )


# ---------------------------------------------------------------- or-defaulting


def _is_default_value(node: ast.expr) -> bool:
    """Whether the right-hand side of an `or` looks like a supplied default.

    A literal (including a negated number such as `-1`), a container display, or any call:
    `SystemMonotonic()`, `set()`, `Path.cwd()`, `datetime.now(UTC)`, `_default_clock()`.
    Whether the `or` is defaulting at all is decided by where it is used, in
    `_in_boolean_context`, which is what lets every call count here.
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        return True
    return isinstance(node, ast.Constant | ast.Dict | ast.List | ast.Set | ast.Tuple | ast.Call)


def _in_boolean_context(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether an expression's value is only ever tested for truth, never used.

    `if not kept or kept.isspace():` is boolean logic, while `x = value or SystemMonotonic()`
    produces a value. The difference is the position, not the operands, so a call on the
    right is only suspicious where the result is kept.
    """
    child, parent = node, parents.get(node)
    while parent is not None:
        if isinstance(parent, ast.If | ast.While | ast.IfExp | ast.Assert) and (
            getattr(parent, "test", None) is child
        ):
            return True
        if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
            return True
        if isinstance(parent, ast.comprehension) and child in parent.ifs:
            return True
        if not isinstance(parent, ast.BoolOp):
            return False
        child, parent = parent, parents.get(parent)
    return False


def test_defaulting_uses_is_none_rather_than_or() -> None:
    """`x or default` silently replaces an explicit `0`, `""`, `False` or empty collection.

    Even where today's values happen to be truthy, the construct works by accident: it
    breaks the moment a type grows a `__bool__` or `__len__`. Defaulting must test for
    `None`, which is what "not supplied" actually means.

    Every `or` whose value is used is considered, not only those in an assignment:
    `return x or default`, `d.get(k) or default` and `f(timeout=x or 5)` are the same
    defect. An `or` that is only tested for truth is boolean logic and is left alone.

    Scope: the right-hand side must be a literal, a container display or a call, which is
    what a supplied default looks like. A choice between two existing values, such as
    `command or path`, is not checked.
    """
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        comments = _comments(path)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
                continue
            if not _is_default_value(node.values[-1]) or _in_boolean_context(node, parents):
                continue
            # `a == b or c.is_relative_to(d)` returns a truth value: a comparison or a
            # negation on the left means every operand is a condition, not a value.
            if isinstance(node.values[0], ast.Compare) or (
                isinstance(node.values[0], ast.UnaryOp) and isinstance(node.values[0].op, ast.Not)
            ):
                continue
            if not _is_exempt(comments, node.lineno, "or-default"):
                offenders.append(f"{_relative(path)}:{node.lineno}")

    _fail(
        "or-default",
        "Use `default if x is None else x` rather than `x or default`, so an explicit "
        "falsy value survives.",
        offenders,
    )


# ---------------------------------------------------------------- documented bounds


@pytest.mark.parametrize(
    ("module", "symbol"),
    [
        ("sphereloom.domain.payloads", "MAX_STRING_LENGTH"),
        ("sphereloom.domain.payloads", "MAX_ITEMS"),
        ("sphereloom.domain.payloads", "MAX_DEPTH"),
        ("sphereloom.domain.payloads", "MAX_NODES"),
        ("sphereloom.domain.payloads", "MAX_INT_BITS"),
        ("sphereloom.domain.payloads", "MAX_SCAN_MULTIPLE"),
        ("sphereloom.security.workspace", "MAX_PATH_LENGTH"),
        ("sphereloom.security.workspace", "MAX_COMPONENT_BYTES"),
        ("sphereloom.security.workspace", "TEMP_NAME_OVERHEAD"),
        ("sphereloom.adapters.osc.client", "MAX_RESPONSE_BYTES"),
        ("sphereloom.adapters.fake.camera_server", "MAX_REQUEST_BYTES"),
    ],
)
def test_every_documented_bound_still_exists(module: str, symbol: str) -> None:
    """Each bound was added because something unbounded caused a defect.

    Naming them here means a refactor that quietly drops one fails rather than silently
    removing the limit.
    """
    imported = __import__(module, fromlist=[symbol])

    assert hasattr(imported, symbol), f"{module}.{symbol} has been removed"
    assert isinstance(getattr(imported, symbol), int)
    assert getattr(imported, symbol) > 0
