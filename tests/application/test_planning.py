"""Tests for frontend-neutral planning invocation construction."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from comparo.application.planning import BudgetOverride
from comparo.application.planning import InvocationBuildError
from comparo.application.planning import InvocationBuildErrorCode
from comparo.application.planning import MatrixOverride
from comparo.application.planning import PlanningOptions
from comparo.application.planning import build_invocation
from comparo.application.planning import plan_project
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopology
from comparo.core.domain.planning_inputs import InheritTopology
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import RunInvocation
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document

_INVOCATION_ID = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("20000000-0000-0000-0000-000000000002")


def _build(options: PlanningOptions) -> RunInvocation:
    return build_invocation(options, invocation_id=_INVOCATION_ID, run_id=_RUN_ID)


def test_empty_options_retain_every_configured_planning_field() -> None:
    invocation = _build(PlanningOptions())

    assert invocation.execution_profile is None
    assert type(invocation.subjects.base) is not ReplaceBase
    assert type(invocation.topology) is InheritTopology
    assert type(invocation.matrices.base) is not ReplaceBase
    assert type(invocation.trials) is Inherit
    assert type(invocation.maximum_attempts) is Inherit
    assert type(invocation.fail_fast) is Inherit


def test_explicit_options_replace_only_their_owned_fields() -> None:
    invocation = _build(
        PlanningOptions(
            execution_profile="release",
            subjects=("ping", "health"),
            baseline="staging",
            candidates=("prod", "canary"),
            matrices=(MatrixOverride(binding="region", matrix="regions"),),
            trials=3,
            maximum_attempts=2,
            fail_fast=False,
            budgets=(BudgetOverride(resource=BudgetResource.CONCURRENCY, limit=4),),
        )
    )

    assert invocation.execution_profile is not None
    assert invocation.execution_profile.value == "release"
    assert type(invocation.subjects.base) is ReplaceBase
    assert tuple(item.value for item in invocation.subjects.base.items) == ("ping", "health")
    assert type(invocation.topology) is ComparisonTopology
    assert invocation.topology.baseline.value == "staging"
    assert tuple(item.value for item in invocation.topology.candidates) == ("prod", "canary")
    assert type(invocation.matrices.base) is ReplaceBase
    assert invocation.matrices.base.items[0].binding.value == "region"
    assert invocation.matrices.base.items[0].matrix.value == "regions"
    assert type(invocation.trials) is SetValue
    assert invocation.trials.value.value == 3
    assert type(invocation.maximum_attempts) is SetValue
    assert invocation.maximum_attempts.value.value == 2
    assert type(invocation.fail_fast) is SetValue
    assert invocation.fail_fast.value is False
    assert invocation.budgets[0].resource is BudgetResource.CONCURRENCY


def test_assertion_topology_is_distinct_from_comparison_topology() -> None:
    invocation = _build(PlanningOptions(environments=("staging", "prod")))

    assert type(invocation.topology) is AssertionTopology
    assert tuple(item.value for item in invocation.topology.environments) == (
        "staging",
        "prod",
    )


@pytest.mark.parametrize(
    ("options", "code"),
    [
        (
            {"subjects": ("ping", "ping")},
            InvocationBuildErrorCode.DUPLICATE_SELECTION,
        ),
        (
            {
                "matrices": (
                    MatrixOverride(binding="region", matrix="a"),
                    MatrixOverride(binding="region", matrix="b"),
                )
            },
            InvocationBuildErrorCode.DUPLICATE_SELECTION,
        ),
        (
            {
                "budgets": (
                    BudgetOverride(resource=BudgetResource.TRIALS, limit=1),
                    BudgetOverride(resource=BudgetResource.TRIALS, limit=2),
                )
            },
            InvocationBuildErrorCode.DUPLICATE_BUDGET,
        ),
        (
            {"environments": ("staging",), "baseline": "staging", "candidates": ("prod",)},
            InvocationBuildErrorCode.CONTRADICTORY_TOPOLOGY,
        ),
        (
            {"baseline": "staging"},
            InvocationBuildErrorCode.CONTRADICTORY_TOPOLOGY,
        ),
        (
            {"candidates": ("prod",)},
            InvocationBuildErrorCode.CONTRADICTORY_TOPOLOGY,
        ),
        ({"trials": 0}, InvocationBuildErrorCode.INVALID_LIMIT),
    ],
)
def test_options_reject_ambiguous_or_unbounded_inputs(
    options: dict[str, object],
    code: InvocationBuildErrorCode,
) -> None:
    with pytest.raises(InvocationBuildError) as caught:
        PlanningOptions(**options)  # type: ignore[arg-type]

    assert caught.value.code is code


def test_budget_override_rejects_non_positive_limit() -> None:
    with pytest.raises(InvocationBuildError) as caught:
        BudgetOverride(resource=BudgetResource.TRIALS, limit=0)
    assert caught.value.code is InvocationBuildErrorCode.INVALID_LIMIT


def test_builder_maps_invalid_slugs_to_value_free_error() -> None:
    with pytest.raises(InvocationBuildError) as caught:
        _build(PlanningOptions(subjects=("NOT A SLUG",)))
    assert caught.value.code is InvocationBuildErrorCode.INVALID_IDENTIFIER
    assert "NOT A SLUG" not in str(caught.value)


def test_builder_rejects_untyped_inputs() -> None:
    with pytest.raises(TypeError, match="error code"):
        InvocationBuildError(cast("InvocationBuildErrorCode", "invalid"))
    with pytest.raises(TypeError, match="PlanningOptions"):
        build_invocation(object(), invocation_id=_INVOCATION_ID, run_id=_RUN_ID)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="UUID"):
        build_invocation(PlanningOptions(), invocation_id="bad", run_id=_RUN_ID)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="selections"):
        PlanningOptions(subjects=cast("tuple[str, ...]", ["bad"]))
    with pytest.raises(TypeError, match="Matrix overrides"):
        PlanningOptions(matrices=cast("tuple[MatrixOverride, ...]", (object(),)))
    with pytest.raises(TypeError, match="budget overrides"):
        PlanningOptions(budgets=cast("tuple[BudgetOverride, ...]", (object(),)))
    with pytest.raises(TypeError, match="fail-fast"):
        PlanningOptions(fail_fast=cast("bool", 1))


def test_plan_project_delegates_to_shared_planner() -> None:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/ping}], environments: [{$use: staging}]}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Request", "ping", "{request: {method: GET, endpoint: /ping}}"),
    )

    result = plan_project(
        project,
        authority(),
        PlanningOptions(),
        invocation_id=_INVOCATION_ID,
        run_id=_RUN_ID,
    )

    assert result.prepared is not None
    assert result.prepared.plan.run_id.value == _RUN_ID
    assert result.prepared.plan.cardinality.environment_executions.value == 1


def test_planning_types_reject_invalid_direct_construction() -> None:
    with pytest.raises(TypeError, match="resource must be typed"):
        BudgetOverride(resource=cast("BudgetResource", "trials"), limit=1)
    with pytest.raises(TypeError, match="PlanningAuthority"):
        plan_project(
            compile_project(
                document("Project", "demo", "{}"),
            ),
            cast("PlanningAuthority", object()),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
