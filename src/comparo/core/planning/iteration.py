"""Lazy deterministic enumeration of cells and trials from compact plan recipes."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import product
from uuid import uuid5

from comparo.core.domain.identity import CellId
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


def _slice_cells(
    plan: RunPlan,
    plan_slice: PlanSlice,
    slice_index: int,
) -> Iterator[PlannedCell]:
    case_spaces = tuple(axis.cases for axis in plan_slice.cells.matrix.axes)
    for case_ids in product(*case_spaces):
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
        coordinate_key = ",".join(
            f"{item.binding.value}={item.case_id.value}" for item in coordinates
        )
        yield PlannedCell(
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
