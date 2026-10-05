"""Validating device-supplied values before they reach domain code.

A camera chooses what it sends. Progress numbers reach log records, so anything the JSON
encoder cannot render would corrupt the line; identifiers and deadlines reach validators,
which must answer with the error taxonomy rather than an exception of their own.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sphereloom.adapters.osc.commands import (
    _completion,
    _results,
    _validated_deadline,
    validated_command_id,
)
from sphereloom.domain.errors import InternalError, InvalidArgumentError


@pytest.mark.parametrize(
    "value",
    [0.0, 0.5, 1.0, 0, 1],
)
def test_a_usable_progress_value_is_reported(value: float) -> None:
    assert _completion({"progress": {"completion": value}}) == pytest.approx(float(value))


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
)
def test_a_non_finite_progress_value_is_dropped(value: float) -> None:
    """`json.dumps` renders these bare, producing a log line no JSON parser accepts."""
    assert _completion({"progress": {"completion": value}}) is None


@pytest.mark.parametrize(
    "value",
    [True, False, "0.5", None, [0.5], {"completion": 0.5}],
)
def test_a_value_that_is_not_a_number_is_dropped(value: Any) -> None:
    assert _completion({"progress": {"completion": value}}) is None


@pytest.mark.parametrize(
    "payload",
    [{}, {"progress": None}, {"progress": "half"}, {"progress": {}}, {"progress": []}],
)
def test_a_missing_or_malformed_progress_block_is_dropped(payload: dict[str, Any]) -> None:
    assert _completion(payload) is None


@pytest.mark.parametrize(
    "value",
    [0.0, 0.5, 1.0, float("nan"), float("inf"), "0.5", None, True],
)
def test_whatever_is_reported_is_always_json_serialisable(value: Any) -> None:
    """The point of the filtering: the result can always be written to a log line."""
    result = _completion({"progress": {"completion": value}})

    assert json.loads(json.dumps({"job_progress": result}, allow_nan=False)) == {
        "job_progress": result
    }


# ---------------------------------------------------------------- terminal response shape


@pytest.mark.parametrize("results", [[], [1, 2], "done", 5, True])
def test_a_non_object_results_field_is_not_reported_as_success(results: Any) -> None:
    """Coercing it to `{}` would report success while discarding what the camera said."""
    with pytest.raises(InternalError):
        _results({"results": results}, command="camera.takePicture")


@pytest.mark.parametrize("payload", [{}, {"results": None}])
def test_an_absent_results_field_is_an_empty_result(payload: dict[str, Any]) -> None:
    """Several commands legitimately report success with no payload."""
    assert _results(payload, command="camera.takePicture") == {}


def test_an_object_results_field_is_returned() -> None:
    assert _results({"results": {"fileUrl": "x"}}, command="camera.takePicture") == {"fileUrl": "x"}


def test_an_oversized_integer_completion_does_not_raise() -> None:
    """`math.isfinite` converts to float and raises OverflowError on a large integer.

    The value is valid JSON, so a hostile payload could crash polling instead of being
    dropped.
    """
    assert _completion({"progress": {"completion": 10**400}}) is None


@pytest.mark.parametrize("value", [-0.1, 1.1, 2, -1, 10**400, -(10**400)])
def test_a_completion_outside_the_documented_range_is_dropped(value: Any) -> None:
    """OSC defines completion as 0..1, so anything else is not a progress report."""
    assert _completion({"progress": {"completion": value}}) is None


# ---------------------------------------------------------------- validator robustness


def test_an_enormous_padded_command_id_is_rejected_without_being_normalised() -> None:
    """`strip()` copies the whole string, so the bound must be checked on the raw value.

    The assertion is the verdict, not a stopwatch: `strip()` on fifty million spaces takes
    about forty milliseconds, which no timing threshold can separate from the correct
    behaviour on a shared CI runner. The ordering is what the test pins down.
    """
    padded = " " * 50_000_000 + "abc"

    assert validated_command_id(padded) is None


@pytest.mark.parametrize(
    "value",
    [10**400, -(10**400), 10**5000, -(10**5000)],
    # Explicit ids because pytest renders a parameter with `repr()`, which raises for an
    # integer this wide -- the same hazard the values are here to test.
    ids=["10**400", "-10**400", "10**5000", "-10**5000"],
)
def test_an_oversized_integer_deadline_stays_inside_the_taxonomy(value: int) -> None:
    """`math.isfinite` converts to float first and raises OverflowError on a large int.

    Above 4300 digits `repr()` raises too, so a message built with `!r` would throw a bare
    ValueError out of the handler that exists to prevent exactly that.
    """
    with pytest.raises(InvalidArgumentError):
        _validated_deadline(value)


def test_a_rejected_deadline_does_not_carry_an_unbounded_message() -> None:
    """The message reaches the wire envelope, so it must not copy the input verbatim."""
    oversized: Any = "x" * 5_000_000

    with pytest.raises(InvalidArgumentError) as caught:
        _validated_deadline(oversized)

    assert len(caught.value.message) < 1000


@pytest.mark.parametrize("value", [True, False, "5", None, [5]])
def test_a_deadline_that_is_not_a_number_is_rejected(value: Any) -> None:
    """`True` is an `int`, so without an explicit check it becomes a one-second deadline."""
    with pytest.raises(InvalidArgumentError):
        _validated_deadline(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_an_unusable_deadline_is_rejected(value: float) -> None:
    with pytest.raises(InvalidArgumentError):
        _validated_deadline(value)


@pytest.mark.parametrize("value", [0, 0.5, 30, 3600.0])
def test_a_usable_deadline_is_accepted(value: float) -> None:
    assert _validated_deadline(value) == value
