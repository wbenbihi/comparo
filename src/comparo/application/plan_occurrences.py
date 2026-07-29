"""Bounded, value-free projections of admitted execution occurrences."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.preflight import PreparedProjectPlan
from comparo.core.domain.identity import DefinitionRef
from comparo.core.runtime import comparison_work_at
from comparo.core.runtime import iter_execution_work_window
from comparo.core.runtime.models import ComparisonWork
from comparo.core.runtime.models import ExecutionWork


@final
class PlanMatrixCaseInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One selected Matrix case qualified by its attachment and definition."""

    binding: str
    matrix: ResourceCoordinate
    case_id: str

    def __post_init__(self) -> None:
        """Require a complete safe Matrix assignment."""
        if type(self.binding) is not str or not self.binding:
            raise ValueError("plan occurrence Matrix binding must be non-empty text")
        if type(self.matrix) is not ResourceCoordinate or self.matrix.kind != "Matrix":
            raise TypeError("plan occurrence Matrix assignment must identify Matrix")
        if type(self.case_id) is not str or not self.case_id:
            raise ValueError("plan occurrence Matrix case must be non-empty text")


@final
class PlanOccurrenceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One canonical Environment execution without resolving any authored value."""

    ordinal: int
    execution_id: str
    subject: ResourceCoordinate
    environment: ResourceCoordinate
    cell_id: str
    trial_id: str
    trial_ordinal: int
    matrices: tuple[PlanMatrixCaseInspection, ...] = ()

    def __post_init__(self) -> None:
        """Require a complete internally coherent safe occurrence identity."""
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("plan occurrence ordinal must be non-negative")
        if type(self.trial_ordinal) is not int or self.trial_ordinal < 0:
            raise ValueError("plan occurrence trial ordinal must be non-negative")
        if any(
            type(value) is not str or not value
            for value in (self.execution_id, self.cell_id, self.trial_id)
        ):
            raise ValueError("plan occurrence identities must be non-empty text")
        if type(self.subject) is not ResourceCoordinate or self.subject.kind not in {
            "Request",
            "Scenario",
        }:
            raise TypeError("plan occurrence subject must identify Request or Scenario")
        if (
            type(self.environment) is not ResourceCoordinate
            or self.environment.kind != "Environment"
        ):
            raise TypeError("plan occurrence Environment must identify Environment")
        if type(self.matrices) is not tuple or any(
            type(item) is not PlanMatrixCaseInspection for item in self.matrices
        ):
            raise TypeError("plan occurrence Matrix assignments must be immutable and typed")
        bindings = tuple(item.binding for item in self.matrices)
        if len(bindings) != len(set(bindings)):
            raise ValueError("plan occurrence Matrix bindings must be unique")


@final
class PlanOccurrenceWindow(msgspec.Struct, frozen=True, kw_only=True):
    """One finite canonical occurrence page from an exact retained Plan."""

    plan_fingerprint: str
    start_ordinal: int
    total_occurrences: int
    occurrences: tuple[PlanOccurrenceInspection, ...]
    next_ordinal: int | None = None

    def __post_init__(self) -> None:
        """Require a contiguous page and an honest continuation cursor."""
        if (
            type(self.plan_fingerprint) is not str
            or len(self.plan_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in self.plan_fingerprint)
        ):
            raise ValueError("plan occurrence window requires a SHA-256 fingerprint")
        if type(self.start_ordinal) is not int or self.start_ordinal < 0:
            raise ValueError("plan occurrence window start must be non-negative")
        if type(self.total_occurrences) is not int or self.total_occurrences < 0:
            raise ValueError("plan occurrence total must be non-negative")
        if type(self.occurrences) is not tuple or any(
            type(item) is not PlanOccurrenceInspection for item in self.occurrences
        ):
            raise TypeError("plan occurrence window items must be immutable and typed")
        ordinals = tuple(item.ordinal for item in self.occurrences)
        if ordinals != tuple(range(self.start_ordinal, self.start_ordinal + len(ordinals))):
            raise ValueError("plan occurrence window must be contiguous")
        consumed = self.start_ordinal + len(self.occurrences)
        expected_next = consumed if consumed < self.total_occurrences else None
        if self.next_ordinal != expected_next:
            raise ValueError("plan occurrence continuation contradicts the returned page")


@final
class PlanComparisonInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One canonical directed comparison without resolving or evaluating values."""

    ordinal: int
    comparison_id: str
    subject: ResourceCoordinate
    cell_id: str
    trial_id: str
    trial_ordinal: int
    baseline_environment: ResourceCoordinate
    baseline_execution_id: str
    candidate_environment: ResourceCoordinate
    candidate_execution_id: str
    matrices: tuple[PlanMatrixCaseInspection, ...] = ()

    def __post_init__(self) -> None:  # noqa: C901 - complete directed-coordinate invariant
        """Require complete subject, trial, Matrix, and directed-side coordinates."""
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("plan comparison ordinal must be non-negative")
        if type(self.trial_ordinal) is not int or self.trial_ordinal < 0:
            raise ValueError("plan comparison trial ordinal must be non-negative")
        identities = (
            self.comparison_id,
            self.cell_id,
            self.trial_id,
            self.baseline_execution_id,
            self.candidate_execution_id,
        )
        if any(type(value) is not str or not value for value in identities):
            raise ValueError("plan comparison identities must be non-empty text")
        if self.baseline_execution_id == self.candidate_execution_id:
            raise ValueError("plan comparison execution sides must differ")
        if type(self.subject) is not ResourceCoordinate or self.subject.kind not in {
            "Request",
            "Scenario",
        }:
            raise TypeError("plan comparison subject must identify Request or Scenario")
        for environment in (self.baseline_environment, self.candidate_environment):
            if type(environment) is not ResourceCoordinate or environment.kind != "Environment":
                raise TypeError("plan comparison sides must identify Environments")
        if self.baseline_environment == self.candidate_environment:
            raise ValueError("plan comparison Environment sides must differ")
        if type(self.matrices) is not tuple or any(
            type(item) is not PlanMatrixCaseInspection for item in self.matrices
        ):
            raise TypeError("plan comparison Matrix assignments must be immutable and typed")
        bindings = tuple(item.binding for item in self.matrices)
        if len(bindings) != len(set(bindings)):
            raise ValueError("plan comparison Matrix bindings must be unique")


@final
class PlanComparisonWindow(msgspec.Struct, frozen=True, kw_only=True):
    """One finite canonical directed-comparison page from an exact retained Plan."""

    plan_fingerprint: str
    start_ordinal: int
    total_comparisons: int
    comparisons: tuple[PlanComparisonInspection, ...]
    next_ordinal: int | None = None

    def __post_init__(self) -> None:
        """Require a contiguous page and honest continuation cursor."""
        if (
            type(self.plan_fingerprint) is not str
            or len(self.plan_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in self.plan_fingerprint)
        ):
            raise ValueError("plan comparison window requires a SHA-256 fingerprint")
        if type(self.start_ordinal) is not int or self.start_ordinal < 0:
            raise ValueError("plan comparison window start must be non-negative")
        if type(self.total_comparisons) is not int or self.total_comparisons < 0:
            raise ValueError("plan comparison total must be non-negative")
        if type(self.comparisons) is not tuple or any(
            type(item) is not PlanComparisonInspection for item in self.comparisons
        ):
            raise TypeError("plan comparison window items must be immutable and typed")
        ordinals = tuple(item.ordinal for item in self.comparisons)
        if ordinals != tuple(range(self.start_ordinal, self.start_ordinal + len(ordinals))):
            raise ValueError("plan comparison window must be contiguous")
        consumed = self.start_ordinal + len(self.comparisons)
        expected_next = consumed if consumed < self.total_comparisons else None
        if self.next_ordinal != expected_next:
            raise ValueError("plan comparison continuation contradicts the returned page")


def inspect_plan_occurrence_window(
    project: PreparedProjectPlan,
    *,
    start_ordinal: int,
    maximum_occurrences: int,
) -> PlanOccurrenceWindow:
    """Project one lazy occurrence page without resolution, filesystem, or network effects."""
    if type(project) is not PreparedProjectPlan:
        raise TypeError("plan occurrence inspection requires PreparedProjectPlan")
    if type(start_ordinal) is not int or start_ordinal < 0:
        raise ValueError("plan occurrence start must be a non-negative integer")
    if type(maximum_occurrences) is not int or maximum_occurrences < 1:
        raise ValueError("plan occurrence page size must be a positive integer")
    prepared = project.prepared
    total = prepared.plan.cardinality.environment_executions.value
    occurrences = tuple(
        inspect_plan_occurrence(work)
        for work in iter_execution_work_window(
            prepared,
            start_ordinal=start_ordinal,
            maximum_items=maximum_occurrences,
        )
    )
    consumed = start_ordinal + len(occurrences)
    return PlanOccurrenceWindow(
        plan_fingerprint=prepared.plan.fingerprint.digest.hex_value,
        start_ordinal=start_ordinal,
        total_occurrences=total,
        occurrences=occurrences,
        next_ordinal=(consumed if consumed < total else None),
    )


def inspect_plan_comparison_window(
    project: PreparedProjectPlan,
    *,
    start_ordinal: int,
    maximum_comparisons: int,
) -> PlanComparisonWindow:
    """Project one lazy comparison page without resolution, filesystem, or network effects."""
    if type(project) is not PreparedProjectPlan:
        raise TypeError("plan comparison inspection requires PreparedProjectPlan")
    if type(start_ordinal) is not int or start_ordinal < 0:
        raise ValueError("plan comparison start must be a non-negative integer")
    if type(maximum_comparisons) is not int or maximum_comparisons < 1:
        raise ValueError("plan comparison page size must be a positive integer")
    prepared = project.prepared
    total = prepared.plan.cardinality.comparisons.value
    comparisons = tuple(
        _inspect_comparison(work)
        for ordinal in range(start_ordinal, min(total, start_ordinal + maximum_comparisons))
        if (work := comparison_work_at(prepared, ordinal)) is not None
    )
    consumed = start_ordinal + len(comparisons)
    return PlanComparisonWindow(
        plan_fingerprint=prepared.plan.fingerprint.digest.hex_value,
        start_ordinal=start_ordinal,
        total_comparisons=total,
        comparisons=comparisons,
        next_ordinal=(consumed if consumed < total else None),
    )


def inspect_plan_occurrence(work: ExecutionWork) -> PlanOccurrenceInspection:
    """Project one exact core execution work item without resolving values."""
    if type(work) is not ExecutionWork:
        raise TypeError("plan occurrence projection requires ExecutionWork")
    cell = work.trial.cell
    return PlanOccurrenceInspection(
        ordinal=work.ordinal.value,
        execution_id=str(work.scope.execution_id.value),
        subject=_coordinate(work.subject),
        environment=_coordinate(work.environment),
        cell_id=str(cell.cell_id.value),
        trial_id=str(work.trial.trial_id.value),
        trial_ordinal=work.trial.ordinal.value,
        matrices=tuple(
            PlanMatrixCaseInspection(
                binding=item.binding.value,
                matrix=_coordinate(item.matrix),
                case_id=item.case_id.value,
            )
            for item in cell.matrix_cases
        ),
    )


def _inspect_comparison(work: ComparisonWork) -> PlanComparisonInspection:
    cell = work.trial.cell
    return PlanComparisonInspection(
        ordinal=work.ordinal.value,
        comparison_id=str(work.comparison_id.value),
        subject=_coordinate(work.subject),
        cell_id=str(cell.cell_id.value),
        trial_id=str(work.trial.trial_id.value),
        trial_ordinal=work.trial.ordinal.value,
        baseline_environment=_coordinate(work.pair.baseline),
        baseline_execution_id=str(work.baseline_execution_id.value),
        candidate_environment=_coordinate(work.pair.candidate),
        candidate_execution_id=str(work.candidate_execution_id.value),
        matrices=tuple(
            PlanMatrixCaseInspection(
                binding=item.binding.value,
                matrix=_coordinate(item.matrix),
                case_id=item.case_id.value,
            )
            for item in cell.matrix_cases
        ),
    )


def _coordinate(definition: DefinitionRef) -> ResourceCoordinate:
    identity = definition.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=definition.fingerprint.digest.hex_value,
    )


__all__ = (
    "PlanComparisonInspection",
    "PlanComparisonWindow",
    "PlanMatrixCaseInspection",
    "PlanOccurrenceInspection",
    "PlanOccurrenceWindow",
    "inspect_plan_comparison_window",
    "inspect_plan_occurrence",
    "inspect_plan_occurrence_window",
)
