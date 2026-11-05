"""Bounded Matrix case selection over the planner's canonical compact topology."""

from dataclasses import dataclass

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.plan_inspection_topology import PlanInspection
from comparo.application.plan_inspection_topology import PlanSliceInspection


@dataclass(frozen=True)
class MatrixBrowseSelection:
    """Exact revision context plus a bounded case page and companion occurrence."""

    matrix: ResourceCoordinate
    subject: ResourceCoordinate
    environment: ResourceCoordinate
    binding: str | None = None
    case_id: str | None = None
    start: int = 0
    occurrence: int = 0

    def __post_init__(self) -> None:
        """Reject invalid selection coordinates before preparation or resolution."""
        for coordinate, kind in (
            (self.matrix, "Matrix"),
            (self.subject, "Request"),
            (self.environment, "Environment"),
        ):
            if type(coordinate) is not ResourceCoordinate or coordinate.kind != kind:
                raise ValueError("Matrix browsing requires exact Matrix, Request and Environment")
        if any(type(value) is not int or value < 0 for value in (self.start, self.occurrence)):
            raise ValueError("Matrix page and occurrence offsets must be nonnegative integers")
        for value in (self.binding, self.case_id):
            if value is not None and (
                type(value) is not str or not value or len(value) > 255 or not value.isprintable()
            ):
                raise ValueError("Matrix identities must be bounded printable text")


@dataclass(frozen=True)
class MatrixCaseWindow:
    """Value-free case page and exact execution ordinal for one preview."""

    plan_fingerprint: str
    bindings: tuple[str, ...]
    binding: str | None = None
    cases: tuple[str, ...] = ()
    total_cases: int = 0
    start: int = 0
    case_id: str | None = None
    execution_ordinal: int | None = None
    occurrence: int = 0
    matching_occurrences: int = 0


MATRIX_PAGE_SIZE = 50


def inspect_matrix_cases(
    plan: PlanInspection, selection: MatrixBrowseSelection
) -> MatrixCaseWindow:
    """Select without expanding the Cartesian product or guessing from row ordinals."""
    if type(plan) is not PlanInspection or type(selection) is not MatrixBrowseSelection:
        raise TypeError("Matrix browsing requires a typed plan and selection")
    matching = [
        part
        for part in plan.slices
        if part.subject == selection.subject and selection.environment in part.environments
    ]
    if len(matching) != 1:
        raise ValueError("Matrix consumer or Environment revision is unavailable")
    part = matching[0]
    axes = [axis for axis in part.matrices if axis.matrix == selection.matrix]
    bindings = tuple(axis.binding for axis in axes)
    if not bindings:
        raise ValueError("Matrix revision is not attached to this consumer")
    binding = selection.binding
    if binding is None:
        if len(bindings) != 1:
            return MatrixCaseWindow(plan.fingerprint, bindings)
        binding = bindings[0]
    axis = next((item for item in axes if item.binding == binding), None)
    if axis is None:
        raise ValueError("Matrix binding is not attached to this consumer")
    if selection.start >= len(axis.cases):
        raise ValueError("Matrix case page is outside the plan")
    cases = axis.cases[selection.start : selection.start + MATRIX_PAGE_SIZE]
    case_id = selection.case_id or cases[0]
    if case_id not in cases:
        raise ValueError("Selected Matrix case is outside the current page")
    case_index = axis.cases.index(case_id)
    count = part.cardinality.cells // len(axis.cases) * part.trials
    if selection.occurrence >= count:
        raise ValueError("Matrix companion occurrence is outside the plan")
    ordinal = _execution_ordinal(
        plan, part, binding, case_index, selection.occurrence, selection.environment
    )
    return MatrixCaseWindow(
        plan.fingerprint,
        bindings,
        binding,
        cases,
        len(axis.cases),
        selection.start,
        case_id,
        ordinal,
        selection.occurrence,
        count,
    )


def _execution_ordinal(
    plan: PlanInspection,
    part: PlanSliceInspection,
    binding: str,
    case_index: int,
    occurrence: int,
    environment: ResourceCoordinate,
) -> int:
    """Invert the canonical lazy Matrix product using mixed-radix arithmetic."""
    # Last Matrix axis varies fastest, then trial, then Environment.
    remaining, trial = divmod(occurrence, part.trials)
    indices: list[int] = []
    for item in reversed(part.matrices):
        if item.binding == binding:
            indices.append(case_index)
        else:
            remaining, index = divmod(remaining, len(item.cases))
            indices.append(index)
    cell = 0
    for item, index in zip(part.matrices, reversed(indices), strict=True):
        cell = cell * len(item.cases) + index
    offset = 0
    for candidate in plan.slices:
        if candidate is part:
            break
        offset += candidate.cardinality.environment_executions
    ordinal = offset + (cell * part.trials + trial) * len(part.environments)
    ordinal += part.environments.index(environment)
    return ordinal
