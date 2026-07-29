"""Frontend-neutral current and terminal run registry tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.application import execution_session as sessions
from comparo.application.compare import CompareExecutionControls
from comparo.application.compare import ComparePreparationHandle
from comparo.application.compare import CompareService
from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSessionPhase
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.production_execution import ProductionExecutionFailure
from comparo.application.production_execution import ProductionExecutionFailureKind
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.run_service import RunHandle
from comparo.application.run_service import RunInspection
from comparo.application.run_service import RunQuery
from comparo.application.run_service import RunResultAccess
from comparo.application.run_service import RunService
from comparo.application.run_service import RunServiceError
from comparo.application.run_service import RunServiceErrorCode
from comparo.application.run_service import RunWindow
from tests.application.test_compare import _assertion_request
from tests.application.test_compare import _controls
from tests.application.test_compare import _write_project
from tests.application.test_production_execution import _request


def _failure() -> ProductionExecutionResult:
    return ProductionExecutionResult(
        failure=ProductionExecutionFailure(
            kind=ProductionExecutionFailureKind.INTERNAL,
            diagnostics=(
                ApplicationDiagnostic(
                    code="test.finished",
                    severity="error",
                    stage="execution",
                ),
            ),
        )
    )


def test_run_service_discovers_observes_cancels_and_retains_terminal_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        release = asyncio.Event()

        async def execute(_owned: ProductionExecutionRequest) -> ProductionExecutionResult:
            await release.wait()
            return _failure()

        monkeypatch.setattr(sessions, "execute_production", execute)
        session = sessions.start_execution_session(_request(tmp_path))
        service = RunService(maximum_runs=2)
        handle = service.register(session)

        assert service.count == 1
        assert not service.closed
        listing = service.list(RunQuery(start=0, maximum_runs=10))
        assert listing.total_matches == 1
        assert listing.runs[0].handle == handle
        assert listing.runs[0].snapshot.phase is ExecutionSessionPhase.PREPARING
        assert not listing.runs[0].terminal
        assert service.inspect(handle) == listing.runs[0]
        assert await service.after(handle, -1) == listing.runs[0].snapshot
        with pytest.raises(RunServiceError) as active:
            service.discard(handle)
        assert active.value.code is RunServiceErrorCode.RUN_ACTIVE

        assert service.cancel(handle)
        assert service.inspect(handle).snapshot.phase is ExecutionSessionPhase.CANCELLING
        assert not service.cancel(handle)
        release.set()
        accessed = await service.result(handle)
        assert accessed.result == _failure()
        terminal = service.inspect(handle)
        assert terminal.terminal
        assert terminal.snapshot.phase is ExecutionSessionPhase.CANCELLED
        filtered = service.list(
            RunQuery(
                start=0,
                maximum_runs=1,
                phases=(ExecutionSessionPhase.CANCELLED,),
            )
        )
        assert filtered.runs == (terminal,)
        assert service.discard(handle)
        assert service.count == 0
        await service.close()
        await service.close()
        with pytest.raises(RunServiceError) as closed:
            service.list(RunQuery(start=0, maximum_runs=1))
        assert closed.value.code is RunServiceErrorCode.CLOSED

    asyncio.run(scenario())


def test_run_service_bounds_active_runs_and_evicts_oldest_terminal_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        release = asyncio.Event()

        async def execute(_owned: ProductionExecutionRequest) -> ProductionExecutionResult:
            await release.wait()
            return _failure()

        monkeypatch.setattr(sessions, "execute_production", execute)
        service = RunService(maximum_runs=1)
        first = service.register(sessions.start_execution_session(_request(tmp_path)))
        second_session = sessions.start_execution_session(_request(tmp_path))
        with pytest.raises(RunServiceError) as limited:
            service.register(second_session)
        assert limited.value.code is RunServiceErrorCode.LIMIT_EXCEEDED
        second_session.cancel()
        release.set()
        await second_session.result()
        await service.result(first)

        replacement = service.register(sessions.start_execution_session(_request(tmp_path)))
        assert replacement.run_id == first.run_id
        assert service.count == 1
        await service.close()

    asyncio.run(scenario())


def test_run_service_collapses_unexpected_execution_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def explode(_owned: ProductionExecutionRequest) -> ProductionExecutionResult:
            raise RuntimeError("secret-bearing exception")

        monkeypatch.setattr(sessions, "execute_production", explode)
        service = RunService(maximum_runs=1)
        handle = service.register(sessions.start_execution_session(_request(tmp_path)))
        accessed = await service.result(handle)
        assert accessed.result is None
        assert accessed.diagnostics[0].code == "run.service.internal"
        await service.close()

    asyncio.run(scenario())


def test_run_service_starts_compare_sessions_and_cleans_duplicate_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        release = asyncio.Event()

        async def execute(_owned: ProductionExecutionRequest) -> ProductionExecutionResult:
            await release.wait()
            return _failure()

        monkeypatch.setattr(sessions, "execute_production", execute)
        compare = CompareService(maximum_outstanding=2)
        config = _write_project(tmp_path)
        first = compare.prepare(_assertion_request(config))
        second = compare.prepare(_assertion_request(config))
        assert first.handle is not None
        assert second.handle is not None
        registry = RunService(maximum_runs=2)
        first_session = compare.start(first.handle, _controls())
        first_handle = registry.register(first_session)

        with pytest.raises(RunServiceError) as duplicate:
            registry.start(compare, second.handle, _controls())
        assert duplicate.value.code is RunServiceErrorCode.RUN_DUPLICATE
        assert registry.count == 1
        assert registry.inspect(first_handle).handle == first_handle
        release.set()
        await registry.result(first_handle)
        await registry.close()

    asyncio.run(scenario())


def test_run_service_result_waiter_cancellation_does_not_cancel_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        release = asyncio.Event()

        async def execute(_owned: ProductionExecutionRequest) -> ProductionExecutionResult:
            await release.wait()
            return _failure()

        monkeypatch.setattr(sessions, "execute_production", execute)
        registry = RunService(maximum_runs=1)
        handle = registry.register(sessions.start_execution_session(_request(tmp_path)))
        waiter = asyncio.create_task(registry.result(handle))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not registry.inspect(handle).terminal
        release.set()
        assert (await registry.result(handle)).result == _failure()
        await registry.close()

    asyncio.run(scenario())


def test_run_service_reports_owned_task_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def execute(_owned: ProductionExecutionRequest) -> ProductionExecutionResult:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(sessions, "execute_production", execute)
        registry = RunService(maximum_runs=1)
        session = sessions.start_execution_session(_request(tmp_path))
        handle = registry.register(session)
        session._task.cancel()
        await asyncio.sleep(0)
        accessed = await registry.result(handle)
        assert accessed.diagnostics[0].code == "run.service.task-cancelled"
        await registry.close()

    asyncio.run(scenario())


def test_run_service_contracts_reject_invalid_state() -> None:
    with pytest.raises(TypeError, match="error code"):
        RunServiceError(cast("RunServiceErrorCode", object()))
    with pytest.raises(ValueError, match="bounded"):
        RunHandle(run_id="")
    with pytest.raises(ValueError, match="bounded"):
        RunHandle(run_id="x" * 129)
    query_cases: tuple[tuple[dict[str, object], type[Exception]], ...] = (
        ({"start": -1}, ValueError),
        ({"start": True}, ValueError),
        ({"maximum_runs": 0}, ValueError),
        ({"maximum_runs": True}, ValueError),
        ({"phases": []}, TypeError),
        ({"phases": (object(),)}, TypeError),
        (
            {
                "phases": (
                    ExecutionSessionPhase.RUNNING,
                    ExecutionSessionPhase.RUNNING,
                )
            },
            ValueError,
        ),
    )
    for query_changes, query_error in query_cases:
        with pytest.raises(query_error):
            msgspec.structs.replace(RunQuery(start=0, maximum_runs=1), **query_changes)

    snapshot = ExecutionProgressSnapshot(
        revision=0,
        run_id="run",
        phase=ExecutionSessionPhase.PREPARING,
        total_executions=1,
        total_comparisons=0,
    )
    inspection = RunInspection(
        handle=RunHandle(run_id="run"),
        snapshot=snapshot,
        terminal=False,
    )
    inspection_cases: tuple[tuple[dict[str, object], type[Exception]], ...] = (
        ({"handle": object()}, TypeError),
        ({"snapshot": object()}, TypeError),
        ({"handle": RunHandle(run_id="other")}, ValueError),
        ({"terminal": 1}, TypeError),
        ({"terminal": True}, ValueError),
    )
    for inspection_changes, inspection_error in inspection_cases:
        with pytest.raises(inspection_error):
            msgspec.structs.replace(inspection, **inspection_changes)

    valid_window = RunWindow(start=0, total_matches=1, runs=(inspection,))
    assert valid_window.next_start is None
    window_cases: tuple[tuple[dict[str, object], type[Exception]], ...] = (
        ({"start": -1}, ValueError),
        ({"total_matches": -1}, ValueError),
        ({"runs": []}, TypeError),
        ({"runs": (object(),)}, TypeError),
        ({"runs": (inspection, inspection), "total_matches": 2}, ValueError),
        ({"total_matches": 2}, ValueError),
    )
    for window_changes, window_error in window_cases:
        with pytest.raises(window_error):
            msgspec.structs.replace(valid_window, **window_changes)

    diagnostic = ApplicationDiagnostic(code="run.test", severity="error", stage="execution")
    result_access_cases: tuple[Callable[[], object], ...] = (
        lambda: RunResultAccess(),
        lambda: RunResultAccess(result=cast("ProductionExecutionResult", object())),
        lambda: RunResultAccess(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", [])),
        lambda: RunResultAccess(diagnostics=(cast("ApplicationDiagnostic", object()),)),
        lambda: RunResultAccess(result=_failure(), diagnostics=(diagnostic,)),
    )
    for factory in result_access_cases:
        with pytest.raises((TypeError, ValueError)):
            factory()

    invalid_limits: tuple[object, ...] = (0, True)
    for invalid in invalid_limits:
        with pytest.raises(ValueError, match="limit"):
            RunService(maximum_runs=cast("int", invalid))

    service = RunService(maximum_runs=1)
    with pytest.raises(TypeError, match="registration"):
        service.register(cast("sessions.ExecutionSession", object()))
    with pytest.raises(TypeError, match="listing"):
        service.list(cast("RunQuery", object()))
    with pytest.raises(TypeError, match="RunHandle"):
        service.inspect(cast("RunHandle", object()))
    with pytest.raises(RunServiceError) as unknown:
        service.inspect(RunHandle(run_id="unknown"))
    assert unknown.value.code is RunServiceErrorCode.RUN_UNKNOWN

    compare = CompareService(maximum_outstanding=1)
    with pytest.raises(TypeError, match="CompareService"):
        service.start(
            cast("CompareService", object()),
            cast("ComparePreparationHandle", object()),
            cast("CompareExecutionControls", object()),
        )
    with pytest.raises(TypeError, match="ComparePreparationHandle"):
        service.start(
            compare,
            cast("ComparePreparationHandle", object()),
            cast("CompareExecutionControls", object()),
        )
    with pytest.raises(TypeError, match="CompareExecutionControls"):
        service.start(
            compare,
            ComparePreparationHandle(value="prepared"),
            cast("CompareExecutionControls", object()),
        )
