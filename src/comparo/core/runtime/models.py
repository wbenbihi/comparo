"""Immutable runtime work, progress, outcome, and final-result contracts."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.assertions.engine import AssertionEvaluation
from comparo.core.diffs.engine import DiffEvaluation
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.gates.model import GateEvaluation
from comparo.core.domain.gates.summary_validation import require_lifecycle_completion
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.lifecycle import RunCompletion
from comparo.core.domain.planning import EnvironmentPair
from comparo.core.domain.primitives import Count
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.planning.models import PlannedTrial
from comparo.core.selection.snapshot import SelectionSnapshot


@final
class ExecutionWork(msgspec.Struct, frozen=True, kw_only=True):
    """One lazily created Environment execution in canonical plan order."""

    ordinal: Count
    subject: DefinitionRef
    trial: PlannedTrial
    environment: DefinitionRef
    scope: ExecutionScope

    def __post_init__(self) -> None:
        """Bind the safe work identity to its exact plan and runtime coordinates."""
        if type(self.ordinal) is not Count:
            raise TypeError("execution work ordinal must be a Count")
        if type(self.subject) is not DefinitionRef or self.subject.identity.kind not in {
            ResourceKind.REQUEST,
            ResourceKind.SCENARIO,
        }:
            raise ValueError("execution work subject must identify a Request or Scenario")
        if type(self.trial) is not PlannedTrial:
            raise TypeError("execution work trial must be a PlannedTrial")
        if self.trial.cell.recipe.subject != self.subject:
            raise ValueError("execution work subject must match its planned cell")
        if (
            type(self.environment) is not DefinitionRef
            or self.environment.identity.kind is not ResourceKind.ENVIRONMENT
        ):
            raise ValueError("execution work environment must identify an Environment")
        if type(self.scope) is not ExecutionScope:
            raise TypeError("execution work scope must be an ExecutionScope")
        if (
            self.scope.trial.trial_id != self.trial.trial_id
            or self.scope.trial.cell.cell_id != self.trial.cell.cell_id
            or self.scope.environment.environment != self.environment.identity
        ):
            raise ValueError("execution work scope contradicts its plan coordinates")


@final
class ObservedExecution(msgspec.Struct, frozen=True, kw_only=True):
    """A complete observation and its already-loaded, I/O-free selection snapshot."""

    observation: EnvironmentExecutionObservation
    snapshot: SelectionSnapshot

    def __post_init__(self) -> None:
        """Require one exact observation shared by both runtime products."""
        if type(self.observation) is not EnvironmentExecutionObservation:
            raise TypeError("observed execution requires an EnvironmentExecutionObservation")
        if type(self.snapshot) is not SelectionSnapshot:
            raise TypeError("observed execution requires a SelectionSnapshot")
        if self.snapshot.observation != self.observation:
            raise ValueError("selection snapshot must contain the observed execution")


@final
class UnavailableExecution(msgspec.Struct, frozen=True, kw_only=True):
    """An execution that could not produce an HTTP observation."""

    stage: DiagnosticStage
    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Retain only structured value-free failure information."""
        if type(self.stage) is not DiagnosticStage or type(self.code) is not DiagnosticCode:
            raise TypeError("unavailable execution requires a typed stage and code")


@final
class BlockedExecution(msgspec.Struct, frozen=True, kw_only=True):
    """An execution denied by policy before an observation could be produced."""

    stage: DiagnosticStage
    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require an authorization or resolution policy boundary."""
        if type(self.stage) is not DiagnosticStage or type(self.code) is not DiagnosticCode:
            raise TypeError("blocked execution requires a typed stage and code")
        if self.stage not in {DiagnosticStage.RESOLUTION, DiagnosticStage.AUTHORIZATION}:
            raise ValueError("blocked execution stage must be resolution or authorization")


@final
class CancelledExecution(msgspec.Struct, frozen=True, kw_only=True):
    """An execution interrupted by the run's first-wins cancellation signal."""

    source: CancellationSource

    def __post_init__(self) -> None:
        """Require the stable cancellation authority."""
        if type(self.source) is not CancellationSource:
            raise TypeError("cancelled execution source must be a CancellationSource")


type ExecutionOutcome = (
    ObservedExecution | UnavailableExecution | BlockedExecution | CancelledExecution
)


def is_execution_outcome(value: object) -> TypeGuard[ExecutionOutcome]:
    """Narrow arbitrary values to the closed execution-outcome union."""
    return type(value) in {
        ObservedExecution,
        UnavailableExecution,
        BlockedExecution,
        CancelledExecution,
    }


@final
class ExecutionRecord(msgspec.Struct, frozen=True, kw_only=True):
    """One terminal execution plus any pure assertion evaluations derived from it."""

    work: ExecutionWork
    outcome: ExecutionOutcome
    assertions: tuple[AssertionEvaluation, ...] = ()

    def __post_init__(self) -> None:
        """Bind outcomes and assertion facts to the execution occurrence."""
        if type(self.work) is not ExecutionWork or not is_execution_outcome(self.outcome):
            raise TypeError("execution record requires typed work and outcome")
        if type(self.assertions) is not tuple or any(
            type(item) is not AssertionEvaluation for item in self.assertions
        ):
            raise TypeError("execution assertions must be an immutable typed tuple")
        if type(self.outcome) is not ObservedExecution and self.assertions:
            raise ValueError("unobserved executions cannot carry assertion evaluations")
        if type(self.outcome) is ObservedExecution:
            observation = self.outcome.observation
            if (
                observation.execution_id != self.work.scope.execution_id
                or observation.environment != self.work.environment
            ):
                raise ValueError("execution observation contradicts its runtime work")
        if any(
            item.fact.application.environment_execution_id != self.work.scope.execution_id
            for item in self.assertions
        ):
            raise ValueError("assertion evaluation belongs to another execution")


@final
class ComparisonWork(msgspec.Struct, frozen=True, kw_only=True):
    """One directed pair for one planned trial in deterministic result order."""

    ordinal: Count
    comparison_id: ComparisonId
    subject: DefinitionRef
    trial: PlannedTrial
    pair: EnvironmentPair
    baseline_execution_id: EnvironmentExecutionId
    candidate_execution_id: EnvironmentExecutionId

    def __post_init__(self) -> None:
        """Require exact distinct directed execution coordinates."""
        if type(self.ordinal) is not Count or type(self.comparison_id) is not ComparisonId:
            raise TypeError("comparison work requires a Count ordinal and ComparisonId")
        if type(self.subject) is not DefinitionRef or type(self.trial) is not PlannedTrial:
            raise TypeError("comparison work requires a definition and PlannedTrial")
        if self.trial.cell.recipe.subject != self.subject:
            raise ValueError("comparison work subject must match its planned cell")
        if type(self.pair) is not EnvironmentPair:
            raise TypeError("comparison work pair must be an EnvironmentPair")
        if (
            type(self.baseline_execution_id) is not EnvironmentExecutionId
            or type(self.candidate_execution_id) is not EnvironmentExecutionId
        ):
            raise TypeError("comparison work requires EnvironmentExecutionId values")
        if self.baseline_execution_id == self.candidate_execution_id:
            raise ValueError("comparison work requires distinct executions")


@final
class IncompleteComparison(msgspec.Struct, frozen=True, kw_only=True):
    """A pair that could not enter binary evaluation because a side was unavailable."""

    unavailable_executions: tuple[EnvironmentExecutionId, ...]

    def __post_init__(self) -> None:
        """Require one or two unique missing side identities."""
        if type(self.unavailable_executions) is not tuple or not self.unavailable_executions:
            raise ValueError("incomplete comparison requires unavailable executions")
        if any(type(item) is not EnvironmentExecutionId for item in self.unavailable_executions):
            raise TypeError("incomplete comparison identities must be EnvironmentExecutionId")
        if len(set(self.unavailable_executions)) != len(self.unavailable_executions):
            raise ValueError("incomplete comparison identities must be unique")


@final
class EvaluatedComparison(msgspec.Struct, frozen=True, kw_only=True):
    """All configured pure diff evaluations for an observed directed pair."""

    diffs: tuple[DiffEvaluation, ...]

    def __post_init__(self) -> None:
        """Require immutable typed evaluations; an empty profile remains valid."""
        if type(self.diffs) is not tuple or any(
            type(item) is not DiffEvaluation for item in self.diffs
        ):
            raise TypeError("comparison diffs must be an immutable typed tuple")


type ComparisonOutcome = IncompleteComparison | EvaluatedComparison


@final
class ComparisonRecord(msgspec.Struct, frozen=True, kw_only=True):
    """One terminal comparison in canonical plan order."""

    work: ComparisonWork
    outcome: ComparisonOutcome

    def __post_init__(self) -> None:
        """Bind every diff fact to the exact directed comparison occurrence."""
        if type(self.work) is not ComparisonWork:
            raise TypeError("comparison record requires ComparisonWork")
        if type(self.outcome) not in {IncompleteComparison, EvaluatedComparison}:
            raise TypeError("comparison record has an unsupported outcome")
        if type(self.outcome) is EvaluatedComparison and any(
            item.fact.application.pair.comparison_id != self.work.comparison_id
            for item in self.outcome.diffs
        ):
            raise ValueError("diff evaluation belongs to another comparison")


class RuntimeEventKind(DomainStrEnum):
    """Stable value-free progress event categories."""

    RUN_STARTED = "run-started"
    EXECUTION_QUEUED = "execution-queued"
    EXECUTION_STARTED = "execution-started"
    EXECUTION_FINISHED = "execution-finished"
    COMPARISON_FINISHED = "comparison-finished"
    RUN_FINISHED = "run-finished"


@final
class RuntimeEvent(msgspec.Struct, frozen=True, kw_only=True):
    """A sequenced progress notification carrying only safe runtime identities."""

    sequence: Count
    run_id: RunId
    kind: RuntimeEventKind
    execution_id: EnvironmentExecutionId | None = None
    comparison_id: ComparisonId | None = None

    def __post_init__(self) -> None:
        """Require the coordinate dictated by the exact event kind."""
        if type(self.sequence) is not Count or type(self.run_id) is not RunId:
            raise TypeError("runtime event requires a Count sequence and RunId")
        if type(self.kind) is not RuntimeEventKind:
            raise TypeError("runtime event kind must be RuntimeEventKind")
        execution_kinds = {
            RuntimeEventKind.EXECUTION_QUEUED,
            RuntimeEventKind.EXECUTION_STARTED,
            RuntimeEventKind.EXECUTION_FINISHED,
        }
        if (self.execution_id is not None) is (self.kind not in execution_kinds):
            raise ValueError("runtime event execution coordinate contradicts its kind")
        if self.execution_id is not None and type(self.execution_id) is not EnvironmentExecutionId:
            raise TypeError("runtime event execution ID must be EnvironmentExecutionId")
        comparison_kind = self.kind is RuntimeEventKind.COMPARISON_FINISHED
        if (self.comparison_id is not None) is (not comparison_kind):
            raise ValueError("runtime event comparison coordinate contradicts its kind")
        if self.comparison_id is not None and type(self.comparison_id) is not ComparisonId:
            raise TypeError("runtime event comparison ID must be ComparisonId")


@final
class RunResult(msgspec.Struct, frozen=True, kw_only=True):
    """Deterministically ordered offline engine result independent of any front-end."""

    run_id: RunId
    completion: RunCompletion
    executions: tuple[ExecutionRecord, ...]
    comparisons: tuple[ComparisonRecord, ...]
    gate: GateEvaluation
    ledgers: tuple[ResourceBudget, ...]

    def __post_init__(self) -> None:
        """Require coherent ordering, run ownership, gate output, and final ledger snapshots."""
        if type(self.run_id) is not RunId:
            raise TypeError("run result requires a RunId")
        require_lifecycle_completion(self.completion)
        if type(self.executions) is not tuple or any(
            type(item) is not ExecutionRecord for item in self.executions
        ):
            raise TypeError("run executions must be an immutable typed tuple")
        if type(self.comparisons) is not tuple or any(
            type(item) is not ComparisonRecord for item in self.comparisons
        ):
            raise TypeError("run comparisons must be an immutable typed tuple")
        if tuple(item.work.ordinal.value for item in self.executions) != tuple(
            sorted(item.work.ordinal.value for item in self.executions)
        ):
            raise ValueError("run executions must use deterministic ordinal order")
        if tuple(item.work.ordinal.value for item in self.comparisons) != tuple(
            sorted(item.work.ordinal.value for item in self.comparisons)
        ):
            raise ValueError("run comparisons must use deterministic ordinal order")
        if any(item.work.scope.trial.cell.run.run_id != self.run_id for item in self.executions):
            raise ValueError("run execution belongs to another run")
        if type(self.gate) is not GateEvaluation:
            raise TypeError("run result gate must be GateEvaluation")
        if type(self.ledgers) is not tuple or any(
            type(item) is not ResourceBudget for item in self.ledgers
        ):
            raise TypeError("run ledgers must be immutable ResourceBudget values")
