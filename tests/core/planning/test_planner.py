"""End-to-end planner behavior, precedence, admission, and lazy enumeration."""

from itertools import islice
from typing import cast

import msgspec

from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopology
from comparo.core.domain.planning_inputs import ComparisonTopologyEdit
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.planning import iter_plan_cells
from comparo.core.planning import iter_plan_trials
from comparo.core.planning import plan_run
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def test_planner_binds_profiles_counts_budgets_and_binary_topology(
    comparison_project: CompiledProject,
) -> None:
    result = plan_run(comparison_project, invocation(), authority())
    assert result.diagnostics == ()
    assert result.prepared is not None
    prepared = result.prepared
    assert prepared.plan.cardinality.cells.value == 2
    assert prepared.plan.cardinality.trials.value == 4
    assert prepared.plan.cardinality.environment_executions.value == 8
    assert prepared.plan.cardinality.comparisons.value == 4
    assert prepared.plan.cardinality.maximum_attempts.value == 16
    assert tuple(item.identity.resource_id.value for item in prepared.effective.environments) == (
        "staging",
        "prod",
    )
    subject = prepared.effective.subjects[0]
    assertions = cast("tuple[DefinitionRef, ...]", subject.profiles.assertions)
    diffs = cast("tuple[DefinitionRef, ...]", subject.profiles.diffs)
    safety = cast("tuple[DefinitionRef, ...]", subject.profiles.safety)
    assert tuple(item.identity.resource_id.value for item in assertions) == (
        "base-assertions",
        "common-assertions",
    )
    assert tuple(item.identity.resource_id.value for item in diffs) == ("semantic",)
    assert tuple(item.identity.resource_id.value for item in safety) == (
        "base-safety",
        "project-safety",
    )
    budgets = {item.resource: item for item in prepared.plan.budgets}
    assert cast("Count", budgets[BudgetResource.MATRIX_CELLS].cap).value == 50
    assert cast("Count", budgets[BudgetResource.TRIALS].cap).value == 20
    assert cast("Count", budgets[BudgetResource.QUEUED_WORK].effective).value == 64
    assert cast("Count", budgets[BudgetResource.CONCURRENCY].effective).value == 8
    assert prepared.effective.fail_fast is False
    assert all(cast("Count", item.usage.consumed).value == 0 for item in budgets.values())
    assert len(prepared.effective.value_layers) == 3
    assert "hello" not in repr(prepared.plan)


def test_invocation_replaces_topology_subjects_trials_and_budget_requests(
    comparison_project: CompiledProject,
) -> None:
    run = invocation(
        subjects=CollectionOverridePlan(base=ReplaceBase(items=(ResourceId(value="chat"),))),
        topology=AssertionTopology(environments=(ResourceId(value="prod"),)),
        trials=SetValue(value=PositiveCount(value=3)),
        maximum_attempts=SetValue(value=PositiveCount(value=1)),
        fail_fast=SetValue(value=True),
        budgets=(
            PlanningBudgetValue(
                resource=BudgetResource.TRIALS,
                limit=PositiveCount(value=8),
            ),
            PlanningBudgetValue(
                resource=BudgetResource.CONCURRENCY,
                limit=PositiveCount(value=3),
            ),
        ),
    )
    result = plan_run(comparison_project, run, authority())
    assert result.prepared is not None
    plan = result.prepared.plan
    assert plan.cardinality.trials.value == 6
    assert plan.cardinality.comparisons.value == 0
    assert plan.cardinality.environment_executions.value == 6
    assert plan.cardinality.maximum_attempts.value == 6
    trial_budget = next(item for item in plan.budgets if item.resource is BudgetResource.TRIALS)
    assert cast("Count", trial_budget.requested).value == 8
    assert cast("Count", trial_budget.effective).value == 8
    concurrency_budget = next(
        item for item in plan.budgets if item.resource is BudgetResource.CONCURRENCY
    )
    assert cast("Count", concurrency_budget.requested).value == 3
    assert result.prepared.effective.fail_fast is True


def test_execution_profile_is_a_more_specific_structural_layer(
    comparison_project: CompiledProject,
) -> None:
    profile_document = document(
        "ExecutionProfile",
        "release",
        "{subjects: [{$use: Request/chat}], environments: [{$use: staging}], trials: 4, "
        "maximumAttempts: 1, budgets: {trials: 12}}",
    )
    documents = [
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/chat}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}]}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document("Request", "chat", "{request: {method: GET, endpoint: /chat}}"),
        profile_document,
    ]
    project = compile_project(*documents)
    result = plan_run(
        project,
        invocation(execution_profile=ResourceId(value="release")),
        authority(),
    )
    assert result.prepared is not None
    assert result.prepared.plan.cardinality.trials.value == 4
    assert result.prepared.plan.cardinality.comparisons.value == 0
    assert tuple(
        item.identity.resource_id.value for item in result.prepared.effective.environments
    ) == ("staging",)


def test_matrix_axes_product_zip_union_include_and_exclude_are_deterministic() -> None:
    matrices = (
        document(
            "Matrix",
            "product-matrix",
            "{axes: {plan: [{id: free, value: free}, {id: pro, value: pro}], "
            "locale: [{id: en, value: en}, {id: fr, value: fr}]}, combine: product, "
            "exclude: [free-fr], include: [{id: custom, values: {plan: custom, locale: es}}]}",
        ),
        document(
            "Matrix",
            "zip-matrix",
            "{axes: {left: [{id: a, value: 1}], right: [{id: b, value: 2}]}, combine: zip}",
        ),
        document(
            "Matrix",
            "union-matrix",
            "{axes: {side: [{id: one, value: 1}]}, combine: union}",
        ),
    )
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "matrices: [{$use: product-matrix}, {$use: zip-matrix}, {$use: union-matrix}]}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        *matrices,
        document("Request", "check", "{request: {method: GET, endpoint: /check}}"),
    )
    result = plan_run(project, invocation(), authority())
    assert result.prepared is not None
    subject = result.prepared.effective.subjects[0]
    assert tuple(case.case_id.value for case in subject.matrices[0].cases) == (
        "free-en",
        "pro-en",
        "pro-fr",
        "custom",
    )
    assert tuple(case.case_id.value for case in subject.matrices[1].cases) == ("a-b",)
    assert tuple(case.case_id.value for case in subject.matrices[2].cases) == ("side-one",)
    assert result.prepared.plan.cardinality.cells.value == 4


def test_cell_and_trial_iteration_is_lazy_stable_and_repeatable(
    comparison_project: CompiledProject,
) -> None:
    prepared = plan_run(comparison_project, invocation(), authority()).prepared
    assert prepared is not None
    first_cells = tuple(iter_plan_cells(prepared.plan))
    second_cells = tuple(iter_plan_cells(prepared.plan))
    assert first_cells == second_cells
    assert tuple(cell.matrix_cases[0].case_id.value for cell in first_cells) == ("en", "fr")
    trials = tuple(iter_plan_trials(prepared.plan))
    assert len(trials) == 4
    assert tuple(trial.ordinal.value for trial in trials) == (0, 1, 0, 1)
    assert tuple(islice(iter_plan_trials(prepared.plan), 1)) == trials[:1]


def test_policy_and_budget_rejections_are_structured_and_pre_effect(
    comparison_project: CompiledProject,
) -> None:
    denied = msgspec.structs.replace(
        authority(),
        allowed_subjects=(),
    )
    denied_result = plan_run(comparison_project, invocation(), denied)
    assert denied_result.prepared is None
    assert denied_result.diagnostics[0].code.value == "planning.policy.subject-denied"

    ceilings = tuple(
        msgspec.structs.replace(item, limit=PositiveCount(value=1))
        if item.resource is BudgetResource.MATRIX_CELLS
        else item
        for item in authority().ceilings
    )
    too_large = plan_run(
        comparison_project,
        invocation(),
        PlanningAuthority(policy=authority().policy, ceilings=ceilings),
    )
    assert too_large.prepared is None
    assert too_large.diagnostics[0].code.value == "planning.matrix.expansion-exceeded"
    assert too_large.diagnostics[0].context.occurrences is not None


def test_invocation_can_replace_comparison_topology_explicitly(
    comparison_project: CompiledProject,
) -> None:
    result = plan_run(
        comparison_project,
        invocation(
            topology=ComparisonTopology(
                baseline=ResourceId(value="prod"),
                candidates=(ResourceId(value="staging"),),
            ),
            subjects=CollectionOverridePlan(base=KeepBase()),
        ),
        authority(),
    )
    assert result.prepared is not None
    pair = result.prepared.effective.comparisons[0]
    assert pair.baseline.identity.resource_id.value == "prod"
    assert pair.candidate.identity.resource_id.value == "staging"


def test_invocation_can_edit_inherited_comparison_candidates_by_identity(
    comparison_project: CompiledProject,
) -> None:
    result = plan_run(
        comparison_project,
        invocation(
            topology=ComparisonTopologyEdit(
                baseline=SetValue(value=ResourceId(value="prod")),
                candidates=CollectionOverridePlan(
                    base=KeepBase(),
                    remove=(ResourceId(value="prod"),),
                    add=(ResourceId(value="staging"),),
                ),
            )
        ),
        authority(),
    )
    assert result.prepared is not None
    pair = result.prepared.effective.comparisons[0]
    assert pair.baseline.identity.resource_id.value == "prod"
    assert pair.candidate.identity.resource_id.value == "staging"
