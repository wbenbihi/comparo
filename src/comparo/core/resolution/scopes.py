"""Concrete scope projection for declared value lifetimes."""

from __future__ import annotations

from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.scopes import run_scope_of


def cell_scope(scope: RuntimeScope) -> CellScope | None:
    """Return the cell axis available at one concrete consumer coordinate."""
    if type(scope) is CellScope:
        return scope
    if type(scope) is TrialScope:
        return scope.cell
    execution = execution_scope(scope)
    return None if execution is None else execution.trial.cell


def trial_scope(scope: RuntimeScope) -> TrialScope | None:
    """Return the trial axis available at one concrete consumer coordinate."""
    if type(scope) is TrialScope:
        return scope
    execution = execution_scope(scope)
    return None if execution is None else execution.trial


def environment_scope(scope: RuntimeScope) -> EnvironmentScope | None:
    """Return the Environment axis available at one concrete consumer coordinate."""
    if type(scope) is EnvironmentScope:
        return scope
    execution = execution_scope(scope)
    return None if execution is None else execution.environment


def execution_scope(scope: RuntimeScope) -> ExecutionScope | None:
    """Return the execution join available at one concrete consumer coordinate."""
    if type(scope) is ExecutionScope:
        return scope
    if type(scope) is StepScope:
        return scope.execution
    if type(scope) is AttemptScope:
        return scope.execution
    return None


def concrete_scope(kind: ValueScopeKind, consumer: RuntimeScope) -> RuntimeScope | None:
    """Project a declared source lifetime from an exact consumer scope."""
    if type(kind) is not ValueScopeKind:
        raise TypeError("declared value scope must be a ValueScopeKind")
    if kind is ValueScopeKind.RUN:
        return run_scope_of(consumer)
    if kind is ValueScopeKind.CELL:
        return cell_scope(consumer)
    if kind is ValueScopeKind.TRIAL:
        return trial_scope(consumer)
    if kind is ValueScopeKind.ENVIRONMENT:
        return environment_scope(consumer)
    if kind is ValueScopeKind.EXECUTION:
        return execution_scope(consumer)
    if kind is ValueScopeKind.STEP:
        if type(consumer) is StepScope:
            return consumer
        if type(consumer) is AttemptScope and consumer.step_execution_id is not None:
            return StepScope(
                execution=consumer.execution,
                step_execution_id=consumer.step_execution_id,
            )
        return None
    return consumer if type(consumer) is AttemptScope else None
