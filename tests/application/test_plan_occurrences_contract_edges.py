"""Occurrence-catalog DTO and boundary validation tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import msgspec
import pytest

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.plan_occurrences import PlanComparisonInspection
from comparo.application.plan_occurrences import PlanComparisonWindow
from comparo.application.plan_occurrences import PlanMatrixCaseInspection
from comparo.application.plan_occurrences import PlanOccurrenceInspection
from comparo.application.plan_occurrences import PlanOccurrenceWindow
from comparo.application.plan_occurrences import inspect_plan_comparison_window
from comparo.application.plan_occurrences import inspect_plan_occurrence_window
from comparo.application.preflight import PreparedProjectPlan

_FINGERPRINT = "a" * 64


def _coordinate(kind: str, resource_id: str) -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="local",
        kind=kind,
        resource_id=resource_id,
        fingerprint=_FINGERPRINT,
    )


def _matrix(binding: str = "payload") -> PlanMatrixCaseInspection:
    return PlanMatrixCaseInspection(
        binding=binding,
        matrix=_coordinate("Matrix", "messages"),
        case_id="short",
    )


def _occurrence(ordinal: int = 0) -> PlanOccurrenceInspection:
    return PlanOccurrenceInspection(
        ordinal=ordinal,
        execution_id=f"execution-{ordinal}",
        subject=_coordinate("Request", "chat"),
        environment=_coordinate("Environment", "staging"),
        cell_id="cell-1",
        trial_id="trial-1",
        trial_ordinal=0,
        matrices=(_matrix(),),
    )


def _comparison(ordinal: int = 0) -> PlanComparisonInspection:
    return PlanComparisonInspection(
        ordinal=ordinal,
        comparison_id=f"comparison-{ordinal}",
        subject=_coordinate("Request", "chat"),
        cell_id="cell-1",
        trial_id="trial-1",
        trial_ordinal=0,
        baseline_environment=_coordinate("Environment", "staging"),
        baseline_execution_id="execution-staging",
        candidate_environment=_coordinate("Environment", "prod"),
        candidate_execution_id="execution-prod",
        matrices=(_matrix(),),
    )


@pytest.mark.parametrize(
    ("factory", "error", "message"),
    [
        (lambda: msgspec.structs.replace(_matrix(), binding=""), ValueError, "binding"),
        (
            lambda: msgspec.structs.replace(
                _matrix(),
                matrix=_coordinate("Environment", "staging"),
            ),
            TypeError,
            "identify Matrix",
        ),
        (lambda: msgspec.structs.replace(_matrix(), case_id=""), ValueError, "case"),
    ],
)
def test_matrix_case_inspection_rejects_invalid_state(
    factory: Callable[[], object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        factory()


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"ordinal": -1}, ValueError, "ordinal"),
        ({"trial_ordinal": -1}, ValueError, "trial ordinal"),
        ({"execution_id": ""}, ValueError, "identities"),
        ({"subject": _coordinate("Environment", "staging")}, TypeError, "subject"),
        ({"environment": _coordinate("Request", "chat")}, TypeError, "Environment"),
        ({"matrices": []}, TypeError, "Matrix assignments"),
        ({"matrices": (object(),)}, TypeError, "Matrix assignments"),
        ({"matrices": (_matrix(), _matrix())}, ValueError, "bindings"),
    ],
)
def test_occurrence_inspection_rejects_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        msgspec.structs.replace(_occurrence(), **changes)


@pytest.mark.parametrize(
    ("factory", "error", "message"),
    [
        (
            lambda: PlanOccurrenceWindow(
                plan_fingerprint="short",
                start_ordinal=0,
                total_occurrences=0,
                occurrences=(),
            ),
            ValueError,
            "SHA-256",
        ),
        (
            lambda: PlanOccurrenceWindow(
                plan_fingerprint="z" * 64,
                start_ordinal=0,
                total_occurrences=0,
                occurrences=(),
            ),
            ValueError,
            "SHA-256",
        ),
        (
            lambda: PlanOccurrenceWindow(
                plan_fingerprint=_FINGERPRINT,
                start_ordinal=-1,
                total_occurrences=0,
                occurrences=(),
            ),
            ValueError,
            "start",
        ),
        (
            lambda: PlanOccurrenceWindow(
                plan_fingerprint=_FINGERPRINT,
                start_ordinal=0,
                total_occurrences=-1,
                occurrences=(),
            ),
            ValueError,
            "total",
        ),
        (
            lambda: PlanOccurrenceWindow(
                plan_fingerprint=_FINGERPRINT,
                start_ordinal=0,
                total_occurrences=1,
                occurrences=cast("tuple[PlanOccurrenceInspection, ...]", []),
            ),
            TypeError,
            "items",
        ),
        (
            lambda: PlanOccurrenceWindow(
                plan_fingerprint=_FINGERPRINT,
                start_ordinal=0,
                total_occurrences=1,
                occurrences=cast("tuple[PlanOccurrenceInspection, ...]", (object(),)),
            ),
            TypeError,
            "items",
        ),
        (
            lambda: PlanOccurrenceWindow(
                plan_fingerprint=_FINGERPRINT,
                start_ordinal=0,
                total_occurrences=2,
                occurrences=(_occurrence(1),),
                next_ordinal=1,
            ),
            ValueError,
            "contiguous",
        ),
        (
            lambda: PlanOccurrenceWindow(
                plan_fingerprint=_FINGERPRINT,
                start_ordinal=0,
                total_occurrences=2,
                occurrences=(_occurrence(),),
            ),
            ValueError,
            "continuation",
        ),
    ],
)
def test_occurrence_window_rejects_invalid_state(
    factory: Callable[[], object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        factory()


def test_occurrence_inspection_rejects_untyped_project() -> None:
    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        inspect_plan_occurrence_window(
            cast("PreparedProjectPlan", object()),
            start_ordinal=0,
            maximum_occurrences=1,
        )


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"ordinal": -1}, ValueError, "ordinal"),
        ({"trial_ordinal": -1}, ValueError, "trial ordinal"),
        ({"comparison_id": ""}, ValueError, "identities"),
        ({"candidate_execution_id": "execution-staging"}, ValueError, "sides must differ"),
        ({"subject": _coordinate("Environment", "staging")}, TypeError, "subject"),
        (
            {"baseline_environment": _coordinate("Request", "chat")},
            TypeError,
            "identify Environments",
        ),
        (
            {"candidate_environment": _coordinate("Environment", "staging")},
            ValueError,
            "Environment sides must differ",
        ),
        ({"matrices": cast("tuple[PlanMatrixCaseInspection, ...]", [])}, TypeError, "Matrix"),
        (
            {"matrices": cast("tuple[PlanMatrixCaseInspection, ...]", (object(),))},
            TypeError,
            "Matrix",
        ),
        ({"matrices": (_matrix(), _matrix())}, ValueError, "bindings"),
    ],
)
def test_comparison_inspection_rejects_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        msgspec.structs.replace(_comparison(), **changes)


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"plan_fingerprint": "short"}, ValueError, "SHA-256"),
        ({"plan_fingerprint": "z" * 64}, ValueError, "SHA-256"),
        ({"start_ordinal": -1}, ValueError, "start"),
        ({"total_comparisons": -1}, ValueError, "total"),
        ({"comparisons": cast("tuple[PlanComparisonInspection, ...]", [])}, TypeError, "items"),
        (
            {"comparisons": cast("tuple[PlanComparisonInspection, ...]", (object(),))},
            TypeError,
            "items",
        ),
        ({"comparisons": (_comparison(1),)}, ValueError, "contiguous"),
        ({"next_ordinal": None}, ValueError, "continuation"),
    ],
)
def test_comparison_window_rejects_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    valid = PlanComparisonWindow(
        plan_fingerprint=_FINGERPRINT,
        start_ordinal=0,
        total_comparisons=2,
        comparisons=(_comparison(),),
        next_ordinal=1,
    )
    with pytest.raises(error, match=message):
        msgspec.structs.replace(valid, **changes)


def test_comparison_inspection_rejects_invalid_boundaries() -> None:
    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        inspect_plan_comparison_window(
            cast("PreparedProjectPlan", object()),
            start_ordinal=0,
            maximum_comparisons=1,
        )
