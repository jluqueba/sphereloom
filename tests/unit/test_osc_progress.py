"""Progress reporting from device-supplied payloads.

A camera reports capture progress as a number it chooses. That number reaches a log record,
so anything the JSON encoder cannot render would corrupt the line it appears on.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sphereloom.adapters.osc.commands import _completion, _results
from sphereloom.domain.errors import InternalError


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
