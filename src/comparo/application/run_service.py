"""Frontend-neutral registry for current and recently finished local runs."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import final

import msgspec

from comparo.application.compare import CompareExecutionControls
from comparo.application.compare import ComparePreparationHandle
from comparo.application.compare import CompareService
from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSession
from comparo.application.execution_session import ExecutionSessionPhase
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.results import AssertionResultPage
from comparo.application.results import ComparisonResultPage
from comparo.application.results import DiffResultPage
from comparo.application.results import ExecutionResultPage
from comparo.application.results import ResultPage
from comparo.application.results import ResultPageQuery
from comparo.application.results import project_result_page


class RunServiceErrorCode(StrEnum):
    """Closed value-free failures produced by run registry commands."""

    CLOSED = "closed"
    LIMIT_EXCEEDED = "limit-exceeded"
    RUN_DUPLICATE = "run-duplicate"
    RUN_UNKNOWN = "run-unknown"
    RUN_ACTIVE = "run-active"


class RunServiceError(RuntimeError):
    """One safe run-registry failure that retains no user-controlled value."""

    __slots__ = ("code",)

    def __init__(self, code: RunServiceErrorCode) -> None:
        """Retain only one closed error category."""
        if type(code) is not RunServiceErrorCode:
            raise TypeError("run service error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class RunHandle(msgspec.Struct, frozen=True, kw_only=True):
    """Stable safe identity for a run owned by one local RunService."""

    run_id: str

    def __post_init__(self) -> None:
        """Require a bounded non-empty run identity."""
        if type(self.run_id) is not str or not self.run_id or len(self.run_id) > 128:
            raise ValueError("run handle ID must be bounded non-empty text")


@final
class RunQuery(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded query over current and retained terminal runs."""

    start: int
    maximum_runs: int
    phases: tuple[ExecutionSessionPhase, ...] = ()

    def __post_init__(self) -> None:
        """Require finite paging and unique typed phase filters."""
        if type(self.start) is not int or self.start < 0:
            raise ValueError("run query start must be a non-negative integer")
        if type(self.maximum_runs) is not int or self.maximum_runs < 1:
            raise ValueError("run query page size must be a positive integer")
        if type(self.phases) is not tuple or any(
            type(item) is not ExecutionSessionPhase for item in self.phases
        ):
            raise TypeError("run query phases must be immutable and typed")
        if len(self.phases) != len(set(self.phases)):
            raise ValueError("run query phases must be unique")


@final
class RunInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Latest safe state of one current or recently terminal run."""

    handle: RunHandle
    snapshot: ExecutionProgressSnapshot
    terminal: bool

    def __post_init__(self) -> None:
        """Bind the handle, progress identity, and terminal marker."""
        if (
            type(self.handle) is not RunHandle
            or type(self.snapshot) is not ExecutionProgressSnapshot
        ):
            raise TypeError("run inspection requires typed handle and snapshot")
        if self.handle.run_id != self.snapshot.run_id:
            raise ValueError("run inspection handle contradicts its snapshot")
        if type(self.terminal) is not bool:
            raise TypeError("run inspection terminal marker must be bool")
        terminal_phase = self.snapshot.phase in {
            ExecutionSessionPhase.FINISHED,
            ExecutionSessionPhase.CANCELLED,
        }
        if self.terminal != terminal_phase:
            raise ValueError("run inspection terminal marker contradicts its phase")


@final
class RunWindow(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded stable-order page of registered runs."""

    start: int
    total_matches: int
    runs: tuple[RunInspection, ...]
    next_start: int | None = None

    def __post_init__(self) -> None:
        """Require coherent paging metadata and unique run identities."""
        if type(self.start) is not int or self.start < 0:
            raise ValueError("run window start must be non-negative")
        if type(self.total_matches) is not int or self.total_matches < 0:
            raise ValueError("run window total must be non-negative")
        if type(self.runs) is not tuple or any(
            type(item) is not RunInspection for item in self.runs
        ):
            raise TypeError("run window items must be immutable and typed")
        identities = tuple(item.handle.run_id for item in self.runs)
        if len(identities) != len(set(identities)):
            raise ValueError("run window identities must be unique")
        consumed = self.start + len(self.runs)
        expected_next = consumed if consumed < self.total_matches else None
        if self.next_start != expected_next:
            raise ValueError("run window continuation contradicts its page")


@final
class RunResultAccess(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one terminal production result or value-free diagnostic rejection."""

    result: ProductionExecutionResult | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful and rejected result access structurally disjoint."""
        if self.result is not None and type(self.result) is not ProductionExecutionResult:
            raise TypeError("run result access result must be typed")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("run result access diagnostics must be immutable and typed")
        if (self.result is None) == bool(self.diagnostics):
            return
        raise ValueError("run result access requires exactly result or diagnostics")


@final
class RunResultPageAccess(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one completed-run result page or value-free diagnostic rejection."""

    page: ResultPage | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful and rejected page access structurally disjoint."""
        if self.page is not None and type(self.page) not in {
            ExecutionResultPage,
            AssertionResultPage,
            ComparisonResultPage,
            DiffResultPage,
        }:
            raise TypeError("run result page access page must be typed")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("run result page diagnostics must be immutable and typed")
        if (self.page is None) == bool(self.diagnostics):
            return
        raise ValueError("run result page access requires exactly page or diagnostics")


@final
class RunService:
    """Own bounded discovery and control of local execution sessions by run ID."""

    __slots__ = ("_closed", "_maximum_runs", "_order", "_sessions")

    def __init__(self, *, maximum_runs: int) -> None:
        """Create one open registry with an explicit total retention ceiling."""
        if type(maximum_runs) is not int or maximum_runs < 1:
            raise ValueError("run service limit must be a positive integer")
        self._maximum_runs = maximum_runs
        self._sessions: dict[str, ExecutionSession] = {}
        self._order: list[str] = []
        self._closed = False

    @property
    def closed(self) -> bool:
        """Return whether the registry permanently rejects new runs."""
        return self._closed

    @property
    def count(self) -> int:
        """Return current and retained terminal run count."""
        return len(self._sessions)

    def start(
        self,
        compare: CompareService,
        preparation: ComparePreparationHandle,
        controls: CompareExecutionControls,
    ) -> RunHandle:
        """Consume one reviewed Compare preparation and register its execution atomically."""
        if type(compare) is not CompareService:
            raise TypeError("run service start requires CompareService")
        if type(preparation) is not ComparePreparationHandle:
            raise TypeError("run service start requires ComparePreparationHandle")
        if type(controls) is not CompareExecutionControls:
            raise TypeError("run service start requires CompareExecutionControls")
        self._admit()
        session = compare.start(preparation, controls)
        try:
            return self.register(session)
        except BaseException:
            session.cancel()
            raise

    def register(self, session: ExecutionSession) -> RunHandle:
        """Adopt an already-started session when capacity was checked by the caller."""
        if type(session) is not ExecutionSession:
            raise TypeError("run service registration requires ExecutionSession")
        self._admit()
        run_id = session.run_id
        if run_id in self._sessions:
            raise RunServiceError(RunServiceErrorCode.RUN_DUPLICATE)
        self._sessions[run_id] = session
        self._order.append(run_id)
        return RunHandle(run_id=run_id)

    def list(self, query: RunQuery) -> RunWindow:
        """Return a stable registration-order page of current safe snapshots."""
        if type(query) is not RunQuery:
            raise TypeError("run service listing requires RunQuery")
        self._require_open()
        allowed = frozenset(query.phases)
        matches = tuple(
            self._inspect(self._sessions[run_id])
            for run_id in self._order
            if not allowed or self._sessions[run_id].snapshot.phase in allowed
        )
        page = matches[query.start : query.start + query.maximum_runs]
        consumed = query.start + len(page)
        return RunWindow(
            start=query.start,
            total_matches=len(matches),
            runs=page,
            next_start=(consumed if consumed < len(matches) else None),
        )

    def inspect(self, handle: RunHandle) -> RunInspection:
        """Return the latest immutable snapshot for one registered run."""
        return self._inspect(self._session(handle))

    async def after(
        self,
        handle: RunHandle,
        revision: int,
    ) -> ExecutionProgressSnapshot | None:
        """Wait for one newer coalesced snapshot or terminal stream closure."""
        return await self._session(handle).after(revision)

    def cancel(self, handle: RunHandle) -> bool:
        """Request first-wins cancellation for one registered active run."""
        return self._session(handle).cancel()

    async def result(self, handle: RunHandle) -> RunResultAccess:
        """Wait for a terminal safe result without exposing unexpected exception details."""
        session = self._session(handle)
        try:
            return RunResultAccess(result=await session.result())
        except asyncio.CancelledError:
            if not session.task_cancelled:
                raise
            return _result_failure("run.service.task-cancelled")
        except Exception:  # noqa: BLE001 - exception details cannot cross this boundary
            return _result_failure("run.service.internal")

    async def result_page(
        self,
        handle: RunHandle,
        query: ResultPageQuery,
    ) -> RunResultPageAccess:
        """Wait for one run and project a bounded page from its sanitized artifact."""
        if type(query) is not ResultPageQuery:
            raise TypeError("run service result paging requires ResultPageQuery")
        access = await self.result(handle)
        if access.result is None:
            return RunResultPageAccess(diagnostics=access.diagnostics)
        artifact = access.result.artifact
        if artifact is None:
            return _result_page_failure("run.service.artifact-unavailable")
        try:
            return RunResultPageAccess(page=project_result_page(artifact, query))
        except Exception:  # noqa: BLE001 - projection defects remain value-free
            return _result_page_failure("run.service.result-page-internal")

    def discard(self, handle: RunHandle) -> bool:
        """Release one terminal retained run; active runs cannot be orphaned."""
        session = self._session(handle)
        if not session.done:
            raise RunServiceError(RunServiceErrorCode.RUN_ACTIVE)
        del self._sessions[handle.run_id]
        self._order.remove(handle.run_id)
        return True

    async def close(self) -> None:
        """Close the registry, cancel active runs, and wait for their cleanup."""
        if self._closed:
            return
        self._closed = True
        sessions = tuple(self._sessions.values())
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)

    def _admit(self) -> None:
        self._require_open()
        while len(self._sessions) >= self._maximum_runs:
            terminal = next(
                (run_id for run_id in self._order if self._sessions[run_id].done),
                None,
            )
            if terminal is None:
                raise RunServiceError(RunServiceErrorCode.LIMIT_EXCEEDED)
            del self._sessions[terminal]
            self._order.remove(terminal)

    def _require_open(self) -> None:
        if self._closed:
            raise RunServiceError(RunServiceErrorCode.CLOSED)

    def _session(self, handle: RunHandle) -> ExecutionSession:
        if type(handle) is not RunHandle:
            raise TypeError("run service operation requires RunHandle")
        self._require_open()
        session = self._sessions.get(handle.run_id)
        if session is None:
            raise RunServiceError(RunServiceErrorCode.RUN_UNKNOWN)
        return session

    @staticmethod
    def _inspect(session: ExecutionSession) -> RunInspection:
        snapshot = session.snapshot
        return RunInspection(
            handle=RunHandle(run_id=session.run_id),
            snapshot=snapshot,
            terminal=snapshot.phase
            in {ExecutionSessionPhase.FINISHED, ExecutionSessionPhase.CANCELLED},
        )


def _result_failure(code: str) -> RunResultAccess:
    return RunResultAccess(
        diagnostics=(
            ApplicationDiagnostic(
                code=code,
                severity="error",
                stage="execution",
            ),
        )
    )


def _result_page_failure(code: str) -> RunResultPageAccess:
    return RunResultPageAccess(
        diagnostics=(
            ApplicationDiagnostic(
                code=code,
                severity="error",
                stage="execution",
            ),
        )
    )


__all__ = (
    "RunHandle",
    "RunInspection",
    "RunQuery",
    "RunResultAccess",
    "RunResultPageAccess",
    "RunService",
    "RunServiceError",
    "RunServiceErrorCode",
    "RunWindow",
)
