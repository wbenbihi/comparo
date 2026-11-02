"""Lazy deterministic enumeration of cells and trials from compact plan recipes."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import product
from uuid import uuid5

from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.planning import MatrixCaseCoordinate
from comparo.core.domain.planning import PlannedCell
from comparo.core.domain.planning import PlanSlice
from comparo.core.domain.planning import RunPlan
from comparo.core.domain.primitives import Count
from comparo.core.planning.models import PlannedTrial


def iter_plan_cells(plan: RunPlan) -> Iterator[PlannedCell]:
    """Yield cells in the plan's versioned subject/Matrix order without eager expansion."""
    if type(plan) is not RunPlan:
        raise TypeError("cell iteration requires a RunPlan")
    for slice_index, plan_slice in enumerate(plan.slices):
        yield from _slice_cells(plan, plan_slice, slice_index)


def iter_plan_trials(plan: RunPlan) -> Iterator[PlannedTrial]:
    """Yield stable zero-based trial occurrences lazily for every planned cell."""
    if type(plan) is not RunPlan:
        raise TypeError("trial iteration requires a RunPlan")
    for slice_index, plan_slice in enumerate(plan.slices):
        for cell in _slice_cells(plan, plan_slice, slice_index):
            for ordinal in range(plan_slice.trial_count.value):
                yield PlannedTrial(
                    trial_id=TrialId(
                        value=uuid5(
                            plan.run_id.value,
                            f"trial:{cell.cell_id.value}:{ordinal}",
                        )
                    ),
                    cell=cell,
                    ordinal=Count(value=ordinal),
                )


def plan_cell_at(
    plan: RunPlan,
    *,
    slice_index: int,
    cell_ordinal: int,
) -> PlannedCell | None:
    """Resolve one slice-local cell by mixed-radix ordinal without scanning predecessors."""
    if type(plan) is not RunPlan:
        raise TypeError("cell lookup requires a RunPlan")
    if type(slice_index) is not int or slice_index < 0:
        raise ValueError("cell lookup slice index must be a non-negative integer")
    if type(cell_ordinal) is not int or cell_ordinal < 0:
        raise ValueError("cell lookup ordinal must be a non-negative integer")
    if slice_index >= len(plan.slices):
        return None
    plan_slice = plan.slices[slice_index]
    if cell_ordinal >= plan_slice.cells.matrix.cell_count.value:
        return None
    case_ids = _case_ids_at(plan_slice, cell_ordinal)
    return _planned_cell(plan, plan_slice, slice_index, case_ids)


def _slice_cells(
    plan: RunPlan,
    plan_slice: PlanSlice,
    slice_index: int,
) -> Iterator[PlannedCell]:
    case_spaces = tuple(axis.cases for axis in plan_slice.cells.matrix.axes)
    for case_ids in product(*case_spaces):
        yield _planned_cell(plan, plan_slice, slice_index, case_ids)


def _case_ids_at(plan_slice: PlanSlice, cell_ordinal: int) -> tuple[MatrixCaseId, ...]:
    selected: list[MatrixCaseId] = []
    remaining = cell_ordinal
    for axis in reversed(plan_slice.cells.matrix.axes):
        remaining, case_index = divmod(remaining, len(axis.cases))
        selected.append(axis.cases[case_index])
    selected.reverse()
    return tuple(selected)


def _planned_cell(
    plan: RunPlan,
    plan_slice: PlanSlice,
    slice_index: int,
    case_ids: tuple[MatrixCaseId, ...],
) -> PlannedCell:
    coordinates = tuple(
        MatrixCaseCoordinate(
            binding=axis.binding,
            matrix=axis.matrix,
            case_id=case_id,
        )
        for axis, case_id in zip(
            plan_slice.cells.matrix.axes,
            case_ids,
            strict=True,
        )
    )
    coordinate_key = ",".join(f"{item.binding.value}={item.case_id.value}" for item in coordinates)
    return PlannedCell(
        cell_id=CellId(
            value=uuid5(
                plan.run_id.value,
                f"cell:{slice_index}:{plan_slice.cells.subject.fingerprint.digest.hex_value}:"
                f"{coordinate_key}",
            )
        ),
        recipe=plan_slice.cells,
        matrix_cases=coordinates,
    )
