"""Explicit runtime coordinates and their non-linear containment relationships."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec

from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import TrialId


class ValueScopeKind(StrEnum):
    """A declared resolution scope that exists before runtime coordinates are assigned."""

    RUN = "run"
    CELL = "cell"
    TRIAL = "trial"
    ENVIRONMENT = "environment"
    EXECUTION = "execution"
    STEP = "step"
    ATTEMPT = "attempt"


@final
class RunScope(msgspec.Struct, frozen=True, kw_only=True):
    """The coordinate shared by every value and operation in one run."""

    run_id: RunId

    def __post_init__(self) -> None:
        """Require a validated run occurrence identifier."""
        if type(self.run_id) is not RunId:
            raise TypeError("run scope requires a RunId")


@final
class CellScope(msgspec.Struct, frozen=True, kw_only=True):
    """One Matrix cell within a run, shared across its compared Environments."""

    run: RunScope
    cell_id: CellId

    def __post_init__(self) -> None:
        """Require validated parent and occurrence coordinates."""
        if type(self.run) is not RunScope:
            raise TypeError("cell scope requires a RunScope")
        if type(self.cell_id) is not CellId:
            raise TypeError("cell scope requires a CellId")


@final
class TrialScope(msgspec.Struct, frozen=True, kw_only=True):
    """One repeated sample of a logical Matrix cell."""

    cell: CellScope
    trial_id: TrialId

    def __post_init__(self) -> None:
        """Require validated parent and occurrence coordinates."""
        if type(self.cell) is not CellScope:
            raise TypeError("trial scope requires a CellScope")
        if type(self.trial_id) is not TrialId:
            raise TypeError("trial scope requires a TrialId")


@final
class EnvironmentScope(msgspec.Struct, frozen=True, kw_only=True):
    """One Environment axis within a run, independent of cells and trials."""

    run: RunScope
    environment: ResourceIdentity

    def __post_init__(self) -> None:
        """Require an Environment resource in the same explicit run coordinate."""
        if type(self.run) is not RunScope:
            raise TypeError("environment scope requires a RunScope")
        if type(self.environment) is not ResourceIdentity:
            raise TypeError("environment scope requires a ResourceIdentity")
        if self.environment.kind is not ResourceKind.ENVIRONMENT:
            raise ValueError("environment scope requires an Environment resource")


@final
class ExecutionScope(msgspec.Struct, frozen=True, kw_only=True):
    """The join of one trial and one Environment execution axis."""

    trial: TrialScope
    environment: EnvironmentScope
    execution_id: EnvironmentExecutionId

    def __post_init__(self) -> None:
        """Require compatible trial and Environment coordinates within one run."""
        if type(self.trial) is not TrialScope:
            raise TypeError("execution scope requires a TrialScope")
        if type(self.environment) is not EnvironmentScope:
            raise TypeError("execution scope requires an EnvironmentScope")
        if type(self.execution_id) is not EnvironmentExecutionId:
            raise TypeError("execution scope requires an EnvironmentExecutionId")
        if self.trial.cell.run != self.environment.run:
            raise ValueError("execution scope coordinates must belong to the same run")


@final
class StepScope(msgspec.Struct, frozen=True, kw_only=True):
    """One future Scenario step inside an Environment execution."""

    execution: ExecutionScope
    step_execution_id: StepExecutionId

    def __post_init__(self) -> None:
        """Require validated execution and step occurrence coordinates."""
        if type(self.execution) is not ExecutionScope:
            raise TypeError("step scope requires an ExecutionScope")
        if type(self.step_execution_id) is not StepExecutionId:
            raise TypeError("step scope requires a StepExecutionId")


@final
class AttemptScope(msgspec.Struct, frozen=True, kw_only=True):
    """One transport attempt, optionally owned by a concrete Scenario step."""

    execution: ExecutionScope
    attempt_id: AttemptId
    step_execution_id: StepExecutionId | None = None

    def __post_init__(self) -> None:
        """Require a validated execution, attempt, and optional step coordinate."""
        if type(self.execution) is not ExecutionScope:
            raise TypeError("attempt scope requires an ExecutionScope")
        if type(self.attempt_id) is not AttemptId:
            raise TypeError("attempt scope requires an AttemptId")
        if (
            self.step_execution_id is not None
            and type(self.step_execution_id) is not StepExecutionId
        ):
            raise TypeError("attempt step coordinate must be a StepExecutionId")


type RuntimeScope = (
    RunScope | CellScope | TrialScope | EnvironmentScope | ExecutionScope | StepScope | AttemptScope
)


class ScopeRelation(StrEnum):
    """The set relationship between two concrete runtime scope coordinates."""

    SAME = "same"
    CONTAINS = "contains"
    CONTAINED_BY = "contained-by"
    OVERLAPS = "overlaps"
    DISJOINT = "disjoint"


def run_scope_of(scope: RuntimeScope) -> RunScope:
    """Return the run coordinate shared by any supported runtime scope."""
    if type(scope) is RunScope:
        return scope
    if type(scope) is CellScope:
        return scope.run
    if type(scope) is TrialScope:
        return scope.cell.run
    if type(scope) is EnvironmentScope:
        return scope.run
    if type(scope) is ExecutionScope:
        return scope.trial.cell.run
    if type(scope) is StepScope:
        return scope.execution.trial.cell.run
    if type(scope) is AttemptScope:
        return scope.execution.trial.cell.run
    raise TypeError("unsupported runtime scope")


def _execution_of(scope: RuntimeScope) -> ExecutionScope | None:
    """Return the owning execution for execution-level descendants."""
    if type(scope) is ExecutionScope:
        return scope
    if type(scope) is StepScope:
        return scope.execution
    if type(scope) is AttemptScope:
        return scope.execution
    return None


def scope_contains(container: RuntimeScope, candidate: RuntimeScope) -> bool:
    """Return whether a concrete scope fully contains another concrete scope."""
    if container == candidate:
        return True
    if run_scope_of(container) != run_scope_of(candidate):
        return False
    if type(container) is RunScope:
        return True

    candidate_execution = _execution_of(candidate)
    if type(container) is CellScope:
        if type(candidate) is TrialScope:
            return candidate.cell == container
        return candidate_execution is not None and candidate_execution.trial.cell == container
    if type(container) is TrialScope:
        return candidate_execution is not None and candidate_execution.trial == container
    if type(container) is EnvironmentScope:
        return candidate_execution is not None and candidate_execution.environment == container
    if type(container) is ExecutionScope:
        return candidate_execution == container
    if type(container) is StepScope and type(candidate) is AttemptScope:
        return (
            candidate.execution == container.execution
            and candidate.step_execution_id == container.step_execution_id
        )
    return False


def scope_relation(left: RuntimeScope, right: RuntimeScope) -> ScopeRelation:
    """Classify containment without pretending the scope lattice is linearly ordered."""
    if left == right:
        return ScopeRelation.SAME
    if scope_contains(left, right):
        return ScopeRelation.CONTAINS
    if scope_contains(right, left):
        return ScopeRelation.CONTAINED_BY
    if run_scope_of(left) != run_scope_of(right):
        return ScopeRelation.DISJOINT

    left_execution = _execution_of(left)
    right_execution = _execution_of(right)
    if left_execution is not None and right_execution is not None:
        return ScopeRelation.DISJOINT

    cell_and_environment = (
        type(left) in {CellScope, TrialScope} and type(right) is EnvironmentScope
    ) or (type(right) in {CellScope, TrialScope} and type(left) is EnvironmentScope)
    if cell_and_environment:
        return ScopeRelation.OVERLAPS
    return ScopeRelation.DISJOINT
