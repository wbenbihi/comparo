"""Tests for the explicit, non-linear runtime scope lattice."""

from uuid import UUID

import pytest

from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import ScopeRelation
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.scopes import run_scope_of
from comparo.core.domain.scopes import scope_contains
from comparo.core.domain.scopes import scope_relation


def _uuid(value: int) -> UUID:
    return UUID(int=value)


def _environment(name: str = "staging") -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.ENVIRONMENT,
        resource_id=ResourceId(value=name),
    )


def _coordinates(
    *,
    run_number: int = 1,
    environment_name: str = "staging",
    execution_number: int = 4,
) -> tuple[
    RunScope,
    CellScope,
    TrialScope,
    EnvironmentScope,
    ExecutionScope,
    StepScope,
    AttemptScope,
]:
    run = RunScope(run_id=RunId(value=_uuid(run_number)))
    cell = CellScope(run=run, cell_id=CellId(value=_uuid(2)))
    trial = TrialScope(cell=cell, trial_id=TrialId(value=_uuid(3)))
    environment = EnvironmentScope(run=run, environment=_environment(environment_name))
    execution = ExecutionScope(
        trial=trial,
        environment=environment,
        execution_id=EnvironmentExecutionId(value=_uuid(execution_number)),
    )
    step_id = StepExecutionId(value=_uuid(5))
    step = StepScope(execution=execution, step_execution_id=step_id)
    attempt = AttemptScope(
        execution=execution,
        attempt_id=AttemptId(value=_uuid(6)),
        step_execution_id=step_id,
    )
    return run, cell, trial, environment, execution, step, attempt


def test_declaration_scope_is_distinct_from_runtime_coordinates() -> None:
    run, *_ = _coordinates()
    assert ValueScopeKind.CELL.value == "cell"
    assert isinstance(run, RunScope)
    assert isinstance(ValueScopeKind.RUN, ValueScopeKind)


def test_runtime_scope_constructors_reject_untyped_coordinates() -> None:
    run, cell, trial, environment, execution, _, _ = _coordinates()
    with pytest.raises(TypeError, match="RunId"):
        RunScope(run_id=_uuid(1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="RunScope"):
        CellScope(run=run.run_id, cell_id=cell.cell_id)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="CellId"):
        CellScope(run=run, cell_id=_uuid(2))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="CellScope"):
        TrialScope(cell=run, trial_id=trial.trial_id)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="TrialId"):
        TrialScope(cell=cell, trial_id=_uuid(3))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="RunScope"):
        EnvironmentScope(run=run.run_id, environment=environment.environment)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ResourceIdentity"):
        EnvironmentScope(run=run, environment="staging")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="TrialScope"):
        ExecutionScope(
            trial=cell,  # type: ignore[arg-type]
            environment=environment,
            execution_id=execution.execution_id,
        )
    with pytest.raises(TypeError, match="EnvironmentScope"):
        ExecutionScope(
            trial=trial,
            environment=run,  # type: ignore[arg-type]
            execution_id=execution.execution_id,
        )
    with pytest.raises(TypeError, match="EnvironmentExecutionId"):
        ExecutionScope(trial=trial, environment=environment, execution_id=_uuid(4))  # type: ignore[arg-type]


def test_environment_and_execution_coordinates_enforce_semantics() -> None:
    run, _, trial, _, _, _, _ = _coordinates()
    request = ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.REQUEST,
        resource_id=ResourceId(value="health"),
    )
    with pytest.raises(ValueError, match="Environment resource"):
        EnvironmentScope(run=run, environment=request)

    other_run = RunScope(run_id=RunId(value=_uuid(99)))
    other_environment = EnvironmentScope(run=other_run, environment=_environment())
    with pytest.raises(ValueError, match="same run"):
        ExecutionScope(
            trial=trial,
            environment=other_environment,
            execution_id=EnvironmentExecutionId(value=_uuid(4)),
        )


def test_step_and_attempt_coordinates_validate_optional_ownership() -> None:
    _, _, _, _, execution, step, attempt = _coordinates()
    with pytest.raises(TypeError, match="ExecutionScope"):
        StepScope(execution="execution", step_execution_id=step.step_execution_id)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="StepExecutionId"):
        StepScope(execution=execution, step_execution_id=_uuid(5))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionScope"):
        AttemptScope(execution="execution", attempt_id=attempt.attempt_id)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AttemptId"):
        AttemptScope(execution=execution, attempt_id=_uuid(6))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="StepExecutionId"):
        AttemptScope(
            execution=execution,
            attempt_id=attempt.attempt_id,
            step_execution_id=_uuid(5),  # type: ignore[arg-type]
        )
    assert (
        AttemptScope(execution=execution, attempt_id=attempt.attempt_id).step_execution_id is None
    )


def test_run_coordinate_can_be_recovered_from_every_scope() -> None:
    coordinates = _coordinates()
    run = coordinates[0]
    assert all(run_scope_of(scope) == run for scope in coordinates)
    with pytest.raises(TypeError, match="unsupported"):
        run_scope_of("run")  # type: ignore[arg-type]


def test_containment_follows_both_trial_and_environment_axes() -> None:
    run, cell, trial, environment, execution, step, attempt = _coordinates()
    assert scope_contains(run, attempt)
    assert scope_contains(cell, trial)
    assert scope_contains(cell, execution)
    assert scope_contains(trial, step)
    assert scope_contains(environment, attempt)
    assert scope_contains(execution, step)
    assert scope_contains(execution, attempt)
    assert scope_contains(step, attempt)
    assert scope_contains(attempt, attempt)

    no_step_attempt = AttemptScope(
        execution=execution,
        attempt_id=AttemptId(value=_uuid(7)),
    )
    assert not scope_contains(step, no_step_attempt)
    assert not scope_contains(attempt, step)


def test_scope_relation_does_not_impose_unsafe_linear_ordering() -> None:
    run, cell, trial, environment, execution, step, attempt = _coordinates()
    assert scope_relation(run, run) is ScopeRelation.SAME
    assert scope_relation(run, attempt) is ScopeRelation.CONTAINS
    assert scope_relation(attempt, run) is ScopeRelation.CONTAINED_BY
    assert scope_relation(cell, environment) is ScopeRelation.OVERLAPS
    assert scope_relation(trial, environment) is ScopeRelation.OVERLAPS

    other_run, *_ = _coordinates(run_number=99)
    assert scope_relation(run, other_run) is ScopeRelation.DISJOINT
    _, _, _, _, other_execution, _, _ = _coordinates(execution_number=44)
    assert scope_relation(execution, other_execution) is ScopeRelation.DISJOINT
    sibling_cell = CellScope(run=run, cell_id=CellId(value=_uuid(88)))
    assert scope_relation(cell, sibling_cell) is ScopeRelation.DISJOINT
    assert scope_relation(step, environment) is ScopeRelation.CONTAINED_BY


def test_runtime_scopes_are_immutable() -> None:
    run, *_ = _coordinates()
    with pytest.raises(AttributeError):
        run.run_id = RunId(value=_uuid(2))  # type: ignore[misc]
