"""Tests for compact plan cardinality and admission control."""

from decimal import Decimal
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.planning import EnvironmentPair
from comparo.core.domain.planning import PlanOrdering
from comparo.core.domain.planning import PlanSlice
from comparo.core.domain.planning import PlanSliceCardinality
from comparo.core.domain.planning import RunPlan
from comparo.core.domain.planning import RunPlanCardinality
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import RunScope
from tests.core.domain.budget_origin_factories import default_budget_origin
from tests.core.domain.http.budget_factories import attempt_scope
from tests.core.domain.planning_factories import _definition
from tests.core.domain.planning_factories import _extra_budget
from tests.core.domain.planning_factories import _plan_budgets
from tests.core.domain.planning_factories import _planning_budget
from tests.core.domain.planning_factories import _run_plan
from tests.core.domain.planning_factories import _slice


def test_plan_cardinality_objects_reject_untyped_counts() -> None:
    counts = {
        "cells": Count(value=1),
        "trials": Count(value=1),
        "environment_executions": Count(value=1),
        "comparisons": Count(value=1),
        "maximum_attempts": Count(value=1),
    }
    for field in counts:
        invalid = counts | {field: object()}
        with pytest.raises(TypeError, match="Count values"):
            PlanSliceCardinality(**invalid)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="Count values"):
            RunPlanCardinality(**invalid)  # type: ignore[arg-type]


def test_plan_slice_recomputes_cardinality_and_validates_topology() -> None:
    assert _slice().cardinality.maximum_attempts.value == 24
    invalid = PlanSliceCardinality(
        cells=Count(value=2),
        trials=Count(value=6),
        environment_executions=Count(value=12),
        comparisons=Count(value=7),
        maximum_attempts=Count(value=24),
    )
    source = _slice()
    with pytest.raises(ValueError, match="does not match"):
        PlanSlice(
            cells=source.cells,
            trial_count=source.trial_count,
            environments=source.environments,
            comparisons=source.comparisons,
            maximum_attempts_per_execution=source.maximum_attempts_per_execution,
            cardinality=invalid,
        )
    outsider = _definition(ResourceKind.ENVIRONMENT, "dev", digest="e")
    with pytest.raises(ValueError, match="must be selected"):
        PlanSlice(
            cells=source.cells,
            trial_count=source.trial_count,
            environments=source.environments,
            comparisons=(EnvironmentPair(baseline=source.environments[0], candidate=outsider),),
            maximum_attempts_per_execution=source.maximum_attempts_per_execution,
            cardinality=source.cardinality,
        )


def test_plan_slice_rejects_invalid_components_and_collection_topology() -> None:
    source = _slice()
    fields = {
        "cells": source.cells,
        "trial_count": source.trial_count,
        "environments": source.environments,
        "comparisons": source.comparisons,
        "maximum_attempts_per_execution": source.maximum_attempts_per_execution,
        "cardinality": source.cardinality,
    }
    for field, message in (
        ("cells", "CellRecipe"),
        ("trial_count", "PositiveCount"),
        ("maximum_attempts_per_execution", "PositiveCount"),
        ("cardinality", "PlanSliceCardinality"),
    ):
        with pytest.raises(TypeError, match=message):
            PlanSlice(**(fields | {field: object()}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable tuple"):
        PlanSlice(**(fields | {"environments": list(source.environments)}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one Environment"):
        PlanSlice(**(fields | {"environments": ()}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DefinitionRef"):
        PlanSlice(**(fields | {"environments": (object(),)}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="identify Environment"):
        PlanSlice(
            **(
                fields | {"environments": (_definition(ResourceKind.REQUEST, "request"),)}  # type: ignore[arg-type]
            )
        )
    environment = source.environments[0]
    with pytest.raises(ValueError, match="must be unique"):
        PlanSlice(
            **(fields | {"environments": (environment, environment)})  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        PlanSlice(**(fields | {"comparisons": list(source.comparisons)}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="EnvironmentPair"):
        PlanSlice(**(fields | {"comparisons": (object(),)}))  # type: ignore[arg-type]
    pair = source.comparisons[0]
    with pytest.raises(ValueError, match="must be unique"):
        PlanSlice(**(fields | {"comparisons": (pair, pair)}))  # type: ignore[arg-type]


def test_run_plan_requires_exact_aggregate_without_eager_tasks() -> None:
    plan_slice = _slice()
    cardinality = RunPlanCardinality(
        cells=Count(value=2),
        trials=Count(value=6),
        environment_executions=Count(value=12),
        comparisons=Count(value=6),
        maximum_attempts=Count(value=24),
    )
    plan = RunPlan(
        run_id=RunId(value=UUID("12345678-1234-5678-1234-567812345678")),
        slices=(plan_slice,),
        budgets=_plan_budgets(cardinality),
        ordering=PlanOrdering.SUBJECT_MATRIX_TRIAL_ENVIRONMENT,
        cardinality=cardinality,
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="f" * 64),
        ),
    )
    assert plan.cardinality == cardinality
    assert not hasattr(plan, "tasks")
    with pytest.raises(ValueError, match="slice totals"):
        RunPlan(
            run_id=plan.run_id,
            slices=plan.slices,
            budgets=plan.budgets,
            ordering=plan.ordering,
            cardinality=RunPlanCardinality(
                cells=Count(value=3),
                trials=cardinality.trials,
                environment_executions=cardinality.environment_executions,
                comparisons=cardinality.comparisons,
                maximum_attempts=cardinality.maximum_attempts,
            ),
            fingerprint=plan.fingerprint,
        )


def test_run_plan_rejects_invalid_identity_collections_and_metadata() -> None:
    plan_slice = _slice()
    cardinality = RunPlanCardinality(
        cells=Count(value=2),
        trials=Count(value=6),
        environment_executions=Count(value=12),
        comparisons=Count(value=6),
        maximum_attempts=Count(value=24),
    )
    fingerprint = Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value="f" * 64),
    )
    fields = {
        "run_id": RunId(value=UUID(int=100)),
        "slices": (plan_slice,),
        "budgets": _plan_budgets(cardinality),
        "ordering": PlanOrdering.SUBJECT_MATRIX_TRIAL_ENVIRONMENT,
        "cardinality": cardinality,
        "fingerprint": fingerprint,
    }
    for field, message in (
        ("run_id", "RunId"),
        ("ordering", "PlanOrdering"),
        ("cardinality", "RunPlanCardinality"),
        ("fingerprint", "Fingerprint"),
    ):
        with pytest.raises(TypeError, match=message):
            RunPlan(**(fields | {field: object()}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable tuple"):
        RunPlan(**(fields | {"slices": [plan_slice]}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one slice"):
        RunPlan(**(fields | {"slices": ()}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PlanSlice"):
        RunPlan(**(fields | {"slices": (object(),)}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="budgets must be an immutable tuple"):
        RunPlan(**(fields | {"budgets": []}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite effective budgets"):
        RunPlan(**(fields | {"budgets": ()}))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ResourceBudget"):
        RunPlan(**(fields | {"budgets": (object(),)}))  # type: ignore[arg-type]


def test_run_plan_requires_unique_unused_sufficient_admission_budgets() -> None:
    plan = _run_plan()
    with pytest.raises(ValueError, match="unique enforcement coordinates"):
        msgspec.structs.replace(plan, budgets=(*plan.budgets, plan.budgets[0]))
    with pytest.raises(ValueError, match="one planning admission budget"):
        msgspec.structs.replace(plan, budgets=plan.budgets[1:])

    insufficient = _planning_budget(BudgetResource.MATRIX_CELLS, 1)
    budgets = tuple(
        insufficient if budget.resource is BudgetResource.MATRIX_CELLS else budget
        for budget in plan.budgets
    )
    with pytest.raises(ValueError, match="exceeds an effective planning budget"):
        msgspec.structs.replace(plan, budgets=budgets)

    used = msgspec.structs.replace(
        plan.budgets[0],
        usage=BudgetUsage(
            consumed=Count(value=1),
            in_use=Count(value=0),
            high_water=Count(value=0),
        ),
    )
    with pytest.raises(ValueError, match="unused admission snapshots"):
        msgspec.structs.replace(plan, budgets=(used, *plan.budgets[1:]))


def test_run_plan_requires_the_exact_admission_operation_for_each_dimension() -> None:
    plan = _run_plan()
    unrelated = msgspec.structs.replace(
        plan.budgets[0],
        operation=BudgetOperation(value="totally.unrelated"),
    )
    with pytest.raises(ValueError, match="one planning admission budget"):
        msgspec.structs.replace(plan, budgets=(unrelated, *plan.budgets[1:]))


def test_run_scoped_budget_must_reference_the_exact_planned_run() -> None:
    plan = _run_plan()
    right_scope = RunScope(run_id=plan.run_id)
    runtime = msgspec.structs.replace(
        plan.budgets[0],
        scope_kind=BudgetScopeKind.RUN,
        scope=right_scope,
        operation=BudgetOperation(value="observe.requests"),
    )
    accepted = msgspec.structs.replace(plan, budgets=(*plan.budgets, runtime))
    assert accepted.budgets[-1].scope == right_scope
    wrong = msgspec.structs.replace(
        runtime,
        scope=RunScope(run_id=RunId(value=UUID(int=999))),
    )
    with pytest.raises(ValueError, match="must belong to the planned run"):
        msgspec.structs.replace(plan, budgets=(*plan.budgets, wrong))

    amount = Count(value=1)
    nested = ResourceBudget(
        resource=BudgetResource.CONCURRENCY,
        scope_kind=BudgetScopeKind.CELL,
        stage=BudgetStage.SCHEDULING,
        operation=BudgetOperation(value="schedule.cell"),
        requested=amount,
        effective=amount,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=amount,
                origin=default_budget_origin(),
            ),
        ),
        usage=BudgetUsage(
            consumed=Count(value=0), in_use=Count(value=0), high_water=Count(value=0)
        ),
        scope=CellScope(
            run=RunScope(run_id=RunId(value=UUID(int=998))),
            cell_id=CellId(value=UUID(int=997)),
        ),
    )
    with pytest.raises(ValueError, match="must belong to the planned run"):
        msgspec.structs.replace(plan, budgets=(*plan.budgets, nested))


def test_plan_budget_snapshots_reject_nonzero_duration_and_ratio_usage() -> None:
    plan = _run_plan()
    zero_duration = Duration(nanoseconds=0)
    duration = _extra_budget(
        resource=BudgetResource.DEADLINE,
        scope_kind=BudgetScopeKind.RUN,
        stage=BudgetStage.HTTP,
        amount=Duration(nanoseconds=10),
        consumed=zero_duration,
        scope=RunScope(run_id=plan.run_id),
    )
    zero_ratio = Ratio.from_decimal(Decimal(0))
    generic_attempt = attempt_scope()
    run = RunScope(run_id=plan.run_id)
    cell = msgspec.structs.replace(generic_attempt.execution.trial.cell, run=run)
    trial = msgspec.structs.replace(generic_attempt.execution.trial, cell=cell)
    environment = msgspec.structs.replace(generic_attempt.execution.environment, run=run)
    execution = msgspec.structs.replace(
        generic_attempt.execution,
        trial=trial,
        environment=environment,
    )
    body_scope = BodyBudgetScope(
        attempt=msgspec.structs.replace(generic_attempt, execution=execution),
        body_id=BodyId(value=UUID(int=996)),
    )
    ratio = _extra_budget(
        resource=BudgetResource.DECOMPRESSION_RATIO,
        scope_kind=BudgetScopeKind.BODY,
        stage=BudgetStage.DECODING,
        amount=Ratio.from_decimal(Decimal(2)),
        consumed=zero_ratio,
        scope=body_scope,
    )
    assert msgspec.structs.replace(plan, budgets=(*plan.budgets, duration, ratio))

    used_duration = msgspec.structs.replace(
        duration,
        usage=BudgetUsage(
            consumed=Duration(nanoseconds=1),
            in_use=zero_duration,
            high_water=zero_duration,
        ),
    )
    with pytest.raises(ValueError, match="unused admission snapshots"):
        msgspec.structs.replace(plan, budgets=(*plan.budgets, used_duration))
    used_ratio = msgspec.structs.replace(
        ratio,
        usage=BudgetUsage(
            consumed=Ratio.from_decimal(Decimal(1)),
            in_use=zero_ratio,
            high_water=zero_ratio,
        ),
    )
    with pytest.raises(ValueError, match="unused admission snapshots"):
        msgspec.structs.replace(plan, budgets=(*plan.budgets, used_ratio))


def test_scenario_recipe_is_representable_but_executable_plan_fails_closed() -> None:
    plan = _run_plan()
    scenario_recipe = msgspec.structs.replace(
        plan.slices[0].cells,
        subject=_definition(ResourceKind.SCENARIO, "checkout"),
    )
    scenario_slice = msgspec.structs.replace(plan.slices[0], cells=scenario_recipe)
    with pytest.raises(ValueError, match="scenario-step cardinality"):
        msgspec.structs.replace(plan, slices=(scenario_slice,))
    step_budget = _planning_budget(BudgetResource.SCENARIO_STEPS, 0)
    with pytest.raises(ValueError, match="scenario-step cardinality"):
        msgspec.structs.replace(
            plan,
            slices=(scenario_slice,),
            budgets=(*plan.budgets, step_budget),
        )


def test_environment_definition_identity_cannot_be_duplicated_by_fingerprint() -> None:
    first = _definition(ResourceKind.ENVIRONMENT, "staging", digest="a")
    second = _definition(ResourceKind.ENVIRONMENT, "staging", digest="b")
    with pytest.raises(ValueError, match="distinct Environments"):
        EnvironmentPair(baseline=first, candidate=second)
    source = _slice()
    with pytest.raises(ValueError, match="identities must be unique"):
        msgspec.structs.replace(source, environments=(first, second), comparisons=())
