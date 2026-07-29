"""Direct ordinal execution-work lookup and bounded-window invariants."""

from __future__ import annotations

from typing import Any
from typing import cast

import pytest

from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.planning import PlannedCell
from comparo.core.domain.planning import RunPlan
from comparo.core.planning import iter_plan_cells
from comparo.core.planning import plan_cell_at
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.planner import plan_run
from comparo.core.runtime import CancelledExecution
from comparo.core.runtime import ExecutionRecord
from comparo.core.runtime import comparison_work_at
from comparo.core.runtime import engine as runtime_engine
from comparo.core.runtime import execution_work_at
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime import iter_execution_work_window
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _many_slice_run(*, subject_count: int = 25) -> PreparedRun:
    subjects = ", ".join(f"{{$use: Request/request-{index}}}" for index in range(subject_count))
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {"
            f"subjects: [{subjects}], "
            "baseline: {$use: staging}, candidates: [{$use: prod}], trials: 2, "
            "matrices: [{$use: dimensions}]}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document(
            "Matrix",
            "dimensions",
            "{cases: [{id: one, values: {value: 1}}, "
            "{id: two, values: {value: 2}}, {id: three, values: {value: 3}}]}",
        ),
        *(
            document(
                "Request",
                f"request-{index}",
                f"{{request: {{method: GET, endpoint: /request-{index}}}}}",
            )
            for index in range(subject_count)
        ),
    )
    outcome = plan_run(project, invocation(), authority())
    assert outcome.prepared is not None, outcome.diagnostics
    return outcome.prepared


def test_direct_cell_lookup_matches_canonical_iteration_for_every_slice() -> None:
    prepared = _many_slice_run(subject_count=4)
    canonical = tuple(iter_plan_cells(prepared.plan))
    cells_per_slice = 3

    for slice_index in range(4):
        for cell_ordinal in range(cells_per_slice):
            expected = canonical[slice_index * cells_per_slice + cell_ordinal]
            assert (
                plan_cell_at(
                    prepared.plan,
                    slice_index=slice_index,
                    cell_ordinal=cell_ordinal,
                )
                == expected
            )
    assert plan_cell_at(prepared.plan, slice_index=4, cell_ordinal=0) is None
    assert plan_cell_at(prepared.plan, slice_index=0, cell_ordinal=3) is None


@pytest.mark.parametrize(
    ("slice_index", "cell_ordinal"),
    [(-1, 0), (cast("int", True), 0), (0, -1), (0, cast("int", True))],
)
def test_direct_cell_lookup_rejects_invalid_coordinates(
    slice_index: int,
    cell_ordinal: int,
) -> None:
    prepared = _many_slice_run(subject_count=1)
    with pytest.raises(ValueError, match="cell lookup"):
        plan_cell_at(
            prepared.plan,
            slice_index=slice_index,
            cell_ordinal=cell_ordinal,
        )
    with pytest.raises(TypeError, match="RunPlan"):
        plan_cell_at(
            cast("Any", object()),
            slice_index=0,
            cell_ordinal=0,
        )


def test_execution_lookup_and_windows_match_the_complete_canonical_order() -> None:
    prepared = _many_slice_run(subject_count=4)
    canonical = tuple(iter_execution_work(prepared))

    assert len(canonical) == 48
    assert tuple(execution_work_at(prepared, ordinal) for ordinal in range(48)) == canonical
    assert execution_work_at(prepared, 48) is None
    assert (
        tuple(
            iter_execution_work_window(
                prepared,
                start_ordinal=43,
                maximum_items=3,
            )
        )
        == canonical[43:46]
    )
    assert (
        tuple(
            iter_execution_work_window(
                prepared,
                start_ordinal=11,
                maximum_items=3,
            )
        )
        == canonical[11:14]
    )
    assert (
        tuple(
            iter_execution_work_window(
                prepared,
                start_ordinal=47,
                maximum_items=100,
            )
        )
        == canonical[47:]
    )
    assert (
        tuple(
            iter_execution_work_window(
                prepared,
                start_ordinal=48,
                maximum_items=3,
            )
        )
        == ()
    )


def test_comparison_lookup_matches_the_runtime_canonical_order() -> None:
    prepared = _many_slice_run(subject_count=4)
    records = tuple(
        ExecutionRecord(
            work=work,
            outcome=CancelledExecution(source=CancellationSource.USER),
        )
        for work in iter_execution_work(prepared)
    )
    canonical = runtime_engine._comparison_work(prepared, records)

    assert len(canonical) == 24
    assert tuple(comparison_work_at(prepared, ordinal) for ordinal in range(24)) == canonical
    assert comparison_work_at(prepared, 24) is None


@pytest.mark.parametrize("ordinal", [-1, cast("int", True)])
def test_comparison_lookup_rejects_invalid_ordinals(ordinal: int) -> None:
    prepared = _many_slice_run(subject_count=1)
    with pytest.raises(ValueError, match="ordinal"):
        comparison_work_at(prepared, ordinal)
    with pytest.raises(TypeError, match="PreparedRun"):
        comparison_work_at(cast("Any", object()), 0)


def test_deep_window_locates_once_then_advances_without_rescanning_slices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _many_slice_run()
    canonical = tuple(iter_execution_work(prepared))
    cell_lookups: list[tuple[int, int]] = []
    original = plan_cell_at

    def record_lookup(
        plan: RunPlan,
        *,
        slice_index: int,
        cell_ordinal: int,
    ) -> PlannedCell | None:
        cell_lookups.append((slice_index, cell_ordinal))
        return original(
            plan,
            slice_index=slice_index,
            cell_ordinal=cell_ordinal,
        )

    def reject_one_off_lookup(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("bounded windows must not call execution_work_at per occurrence")

    monkeypatch.setattr("comparo.core.runtime.scheduling.plan_cell_at", record_lookup)
    monkeypatch.setattr(
        "comparo.core.runtime.scheduling.execution_work_at",
        reject_one_off_lookup,
    )

    page = tuple(
        iter_execution_work_window(
            prepared,
            start_ordinal=297,
            maximum_items=2,
        )
    )

    assert page == canonical[297:299]
    assert cell_lookups == [(24, 2)]


@pytest.mark.parametrize("ordinal", [-1, cast("int", True)])
def test_execution_lookup_rejects_invalid_ordinals(ordinal: int) -> None:
    prepared = _many_slice_run(subject_count=1)
    with pytest.raises(ValueError, match="ordinal"):
        execution_work_at(prepared, ordinal)
    with pytest.raises(TypeError, match="PreparedRun"):
        execution_work_at(cast("Any", object()), 0)


@pytest.mark.parametrize(
    ("start_ordinal", "maximum_items"),
    [(-1, 1), (cast("int", True), 1), (0, 0), (0, -1), (0, cast("int", True))],
)
def test_execution_window_rejects_invalid_bounds(
    start_ordinal: int,
    maximum_items: int,
) -> None:
    prepared = _many_slice_run(subject_count=1)
    with pytest.raises(ValueError, match="execution work window"):
        tuple(
            iter_execution_work_window(
                prepared,
                start_ordinal=start_ordinal,
                maximum_items=maximum_items,
            )
        )
    with pytest.raises(TypeError, match="PreparedRun"):
        tuple(
            iter_execution_work_window(
                cast("Any", object()),
                start_ordinal=0,
                maximum_items=1,
            )
        )
