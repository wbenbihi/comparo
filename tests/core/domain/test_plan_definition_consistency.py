"""Tests for exact definition consistency at the run-plan boundary."""

from collections.abc import Callable

import msgspec
import pytest

from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.planning import EnvironmentPair
from comparo.core.domain.planning import MatrixAxisPlan
from comparo.core.domain.planning import MatrixSpace
from comparo.core.domain.planning import PlanSlice
from tests.core.domain.planning_factories import _definition
from tests.core.domain.planning_factories import _run_plan_for_slices
from tests.core.domain.planning_factories import _slice


def _refingerprint(definition: DefinitionRef, digest: str) -> DefinitionRef:
    fingerprint = _definition(
        definition.identity.kind,
        definition.identity.resource_id.value,
        digest=digest,
    ).fingerprint
    return msgspec.structs.replace(definition, fingerprint=fingerprint)


def _replace_subject(source: PlanSlice) -> PlanSlice:
    cells = msgspec.structs.replace(
        source.cells,
        subject=_refingerprint(source.cells.subject, "1"),
    )
    return msgspec.structs.replace(source, cells=cells)


def _replace_matrix(source: PlanSlice) -> PlanSlice:
    axis = source.cells.matrix.axes[0]
    changed_axis = msgspec.structs.replace(axis, matrix=_refingerprint(axis.matrix, "2"))
    matrix = msgspec.structs.replace(source.cells.matrix, axes=(changed_axis,))
    return msgspec.structs.replace(
        source,
        cells=msgspec.structs.replace(source.cells, matrix=matrix),
    )


def _replace_environment(source: PlanSlice) -> PlanSlice:
    changed = _refingerprint(source.environments[0], "3")
    candidate = source.environments[1]
    return msgspec.structs.replace(
        source,
        environments=(changed, candidate),
        comparisons=(EnvironmentPair(baseline=changed, candidate=candidate),),
    )


@pytest.mark.parametrize("replace", [_replace_subject, _replace_matrix, _replace_environment])
def test_run_plan_rejects_cross_slice_definition_split_brain(
    replace: Callable[[PlanSlice], PlanSlice],
) -> None:
    first = _slice()
    with pytest.raises(ValueError, match="identity maps to multiple plan fingerprints"):
        _run_plan_for_slices(first, replace(first))


def test_run_plan_accepts_repeated_identity_with_one_fingerprint() -> None:
    source = _slice()
    plan = _run_plan_for_slices(source, source)
    assert plan.slices == (source, source)


def test_run_plan_rejects_split_brain_matrix_attachments_within_one_slice() -> None:
    source = _slice()
    axis = source.cells.matrix.axes[0]
    split_brain_axis = MatrixAxisPlan(
        binding=MatrixBindingId(value="payload"),
        matrix=_refingerprint(axis.matrix, "4"),
        cases=(MatrixCaseId(value="default"),),
    )
    matrix = MatrixSpace(
        axes=(axis, split_brain_axis),
        cell_count=source.cells.matrix.cell_count,
    )
    cells = msgspec.structs.replace(source.cells, matrix=matrix)
    split_brain_slice = msgspec.structs.replace(source, cells=cells)
    with pytest.raises(ValueError, match="identity maps to multiple plan fingerprints"):
        _run_plan_for_slices(split_brain_slice)
