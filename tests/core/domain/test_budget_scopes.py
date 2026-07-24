"""Tests for runtime and non-runtime budget scope contracts."""

from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.budget_origins import BudgetOrigin
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.identity import AnalysisId
from comparo.core.domain.identity import ArtifactId
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import StreamId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.scopes import ArtifactBudgetScope
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import ComparisonBudgetScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import ExternalCapabilityBudgetScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import StreamBudgetScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.scopes import run_scope_of
from tests.core.domain.budget_origin_factories import default_budget_origin
from tests.core.domain.budget_origin_factories import definition_budget_origin
from tests.core.domain.budget_origin_factories import invocation_budget_origin

_REQUESTED = Count(value=10)
_EFFECTIVE = Count(value=8)
_CAP = Count(value=8)


def _contribution(
    amount: Count | ByteCount | Duration | Ratio,
    kind: BudgetContributionKind = BudgetContributionKind.DEFAULT,
    origin: BudgetOrigin | None = None,
) -> BudgetContribution:
    return BudgetContribution(
        kind=kind,
        amount=amount,
        origin=origin or default_budget_origin(),
    )


def _usage(amount: Count | ByteCount | Duration | Ratio) -> BudgetUsage:
    if type(amount) is Count:
        zero: Count | ByteCount | Duration | Ratio = Count(value=0)
    elif type(amount) is ByteCount:
        zero = ByteCount(value=0)
    elif type(amount) is Duration:
        zero = Duration(nanoseconds=0)
    else:
        zero = Ratio.from_decimal(Decimal(0))
    return BudgetUsage(consumed=zero, in_use=zero, high_water=zero)


def _budget(
    *,
    requested: Count = _REQUESTED,
    effective: Count = _EFFECTIVE,
    cap: Count | None = _CAP,
    contributions: tuple[BudgetContribution, ...] | None = None,
    usage: BudgetUsage | None = None,
    scope: RunScope | None = None,
) -> ResourceBudget:
    if contributions is None:
        contributions = (
            _contribution(Count(value=5)),
            _contribution(
                requested,
                BudgetContributionKind.REQUEST,
                invocation_budget_origin(),
            ),
            _contribution(
                Count(value=8),
                BudgetContributionKind.CEILING,
                definition_budget_origin(ResourceKind.EXECUTION_POLICY),
            ),
        )
    return ResourceBudget(
        resource=BudgetResource.REQUESTS,
        scope_kind=BudgetScopeKind.RUN,
        stage=BudgetStage.PLANNING,
        operation=BudgetOperation(value="expand.requests"),
        requested=requested,
        effective=effective,
        cap=cap,
        contributions=contributions,
        usage=usage or _usage(requested),
        scope=scope or RunScope(run_id=RunId(value=UUID(int=1))),
    )


def _runtime_scopes() -> tuple[tuple[BudgetScopeKind, RuntimeScope], ...]:
    run = RunScope(run_id=RunId(value=UUID(int=10)))
    cell = CellScope(run=run, cell_id=CellId(value=UUID(int=11)))
    trial = TrialScope(cell=cell, trial_id=TrialId(value=UUID(int=12)))
    environment = EnvironmentScope(
        run=run,
        environment=ResourceIdentity(
            namespace=NamespaceId(value="local"),
            kind=ResourceKind.ENVIRONMENT,
            resource_id=ResourceId(value="staging"),
        ),
    )
    execution = ExecutionScope(
        trial=trial,
        environment=environment,
        execution_id=EnvironmentExecutionId(value=UUID(int=13)),
    )
    step = StepScope(
        execution=execution,
        step_execution_id=StepExecutionId(value=UUID(int=14)),
    )
    attempt = AttemptScope(
        execution=execution,
        attempt_id=AttemptId(value=UUID(int=15)),
    )
    return (
        (BudgetScopeKind.RUN, run),
        (BudgetScopeKind.CELL, cell),
        (BudgetScopeKind.TRIAL, trial),
        (BudgetScopeKind.ENVIRONMENT, environment),
        (BudgetScopeKind.EXECUTION, execution),
        (BudgetScopeKind.STEP, step),
        (BudgetScopeKind.ATTEMPT, attempt),
    )


def _with_scope(scope_kind: BudgetScopeKind, scope: RuntimeScope | None) -> ResourceBudget:
    if scope_kind in {
        BudgetScopeKind.RUN,
        BudgetScopeKind.CELL,
        BudgetScopeKind.TRIAL,
        BudgetScopeKind.EXECUTION,
    }:
        resource = BudgetResource.CONCURRENCY
        stage = BudgetStage.SCHEDULING
        amount: Count | ByteCount = Count(value=10)
    else:
        resource = BudgetResource.REQUEST_BYTES
        stage = BudgetStage.HTTP
        amount = ByteCount(value=10)
    return ResourceBudget(
        resource=resource,
        scope_kind=scope_kind,
        stage=stage,
        operation=BudgetOperation(value="test.scope"),
        requested=amount,
        effective=amount,
        cap=None,
        contributions=(_contribution(amount),),
        usage=_usage(amount),
        scope=scope,
    )


def test_runtime_budget_scope_kind_requires_its_exact_concrete_scope() -> None:
    scoped = _runtime_scopes()
    for scope_kind, scope in scoped:
        assert _with_scope(scope_kind, scope).scope is scope
        with pytest.raises(ValueError, match="requires a concrete scope"):
            _with_scope(scope_kind, None)

    run = scoped[0][1]
    cell = scoped[1][1]
    with pytest.raises(ValueError, match="must match"):
        _with_scope(BudgetScopeKind.RUN, cell)
    with pytest.raises(ValueError, match="must match"):
        _with_scope(BudgetScopeKind.CELL, run)


def test_specialized_budget_scope_kinds_require_exact_coordinates() -> None:
    runtime = dict(_runtime_scopes())
    run = runtime[BudgetScopeKind.RUN]
    cell = runtime[BudgetScopeKind.CELL]
    attempt = runtime[BudgetScopeKind.ATTEMPT]
    execution = runtime[BudgetScopeKind.EXECUTION]
    assert type(run) is RunScope
    assert type(cell) is CellScope
    assert type(attempt) is AttemptScope
    assert type(execution) is ExecutionScope
    definitions = (
        (
            BudgetResource.REQUEST_BYTES,
            BudgetScopeKind.BODY,
            BudgetStage.HTTP,
            ByteCount(value=1),
            BodyBudgetScope(attempt=attempt, body_id=BodyId(value=UUID(int=20))),
        ),
        (
            BudgetResource.STREAM_EVENTS,
            BudgetScopeKind.STREAM,
            BudgetStage.STREAM,
            Count(value=1),
            StreamBudgetScope(execution=execution, stream_id=StreamId(value=UUID(int=21))),
        ),
        (
            BudgetResource.SELECTION_WORK,
            BudgetScopeKind.COMPARISON,
            BudgetStage.SELECTION,
            Count(value=1),
            ComparisonBudgetScope(run=run, comparison_id=ComparisonId(value=UUID(int=22))),
        ),
        (
            BudgetResource.ARTIFACT_BLOBS,
            BudgetScopeKind.ARTIFACT,
            BudgetStage.ARTIFACT,
            Count(value=1),
            ArtifactBudgetScope(run=run, artifact_id=ArtifactId(value="artifact-23")),
        ),
        (
            BudgetResource.EXTERNAL_WORK,
            BudgetScopeKind.EXTERNAL_CAPABILITY,
            BudgetStage.EXTERNAL_CAPABILITY,
            Count(value=1),
            ExternalCapabilityBudgetScope(run=run, analysis_id=AnalysisId(value=UUID(int=24))),
        ),
    )
    for resource, scope_kind, stage, amount, scope in definitions:
        assert run_scope_of(scope) == run
        values = {
            "resource": resource,
            "scope_kind": scope_kind,
            "stage": stage,
            "operation": BudgetOperation(value="test.non-runtime"),
            "requested": amount,
            "effective": amount,
            "cap": None,
            "contributions": (_contribution(amount),),
            "usage": _usage(amount),
        }
        assert ResourceBudget(**values, scope=scope).scope is scope  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="requires a concrete scope"):
            ResourceBudget(**values)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="must match"):
            ResourceBudget(**values, scope=run)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="body budget scope"):
        BodyBudgetScope(attempt=cast("AttemptScope", run), body_id=BodyId(value=UUID(int=30)))
    with pytest.raises(TypeError, match="body budget scope"):
        BodyBudgetScope(attempt=attempt, body_id=cast("BodyId", StreamId(value=UUID(int=30))))
    with pytest.raises(TypeError, match="stream budget scope"):
        StreamBudgetScope(
            execution=cast("ExecutionScope", run),
            stream_id=StreamId(value=UUID(int=31)),
        )
    with pytest.raises(TypeError, match="stream budget scope"):
        StreamBudgetScope(
            execution=execution,
            stream_id=cast("StreamId", BodyId(value=UUID(int=31))),
        )
    with pytest.raises(TypeError, match="comparison budget scope"):
        ComparisonBudgetScope(
            run=cast("RunScope", cell),
            comparison_id=ComparisonId(value=UUID(int=32)),
        )
    with pytest.raises(TypeError, match="comparison budget scope"):
        ComparisonBudgetScope(
            run=run,
            comparison_id=cast("ComparisonId", BodyId(value=UUID(int=32))),
        )
    with pytest.raises(TypeError, match="artifact budget scope"):
        ArtifactBudgetScope(
            run=cast("RunScope", cell),
            artifact_id=ArtifactId(value="artifact-33"),
        )
    with pytest.raises(TypeError, match="artifact budget scope"):
        ArtifactBudgetScope(
            run=run,
            artifact_id=cast("ArtifactId", BodyId(value=UUID(int=33))),
        )
    with pytest.raises(TypeError, match="external capability budget scope"):
        ExternalCapabilityBudgetScope(
            run=cast("RunScope", cell),
            analysis_id=AnalysisId(value=UUID(int=34)),
        )
    with pytest.raises(TypeError, match="external capability budget scope"):
        ExternalCapabilityBudgetScope(
            run=run,
            analysis_id=cast("AnalysisId", BodyId(value=UUID(int=34))),
        )
    with pytest.raises(TypeError, match="unsupported runtime scope"):
        run_scope_of(cast("RuntimeScope", object()))


def test_plan_budget_scope_remains_intentionally_unaddressed() -> None:
    values = {
        "resource": BudgetResource.REQUESTS,
        "scope_kind": BudgetScopeKind.PLAN,
        "stage": BudgetStage.PLANNING,
        "operation": BudgetOperation(value="test.plan"),
        "requested": Count(value=1),
        "effective": Count(value=1),
        "cap": None,
        "contributions": (_contribution(Count(value=1)),),
        "usage": _usage(Count(value=1)),
    }
    assert ResourceBudget(**values).scope is None  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot carry"):
        ResourceBudget(**values, scope=_runtime_scopes()[0][1])  # type: ignore[arg-type]
