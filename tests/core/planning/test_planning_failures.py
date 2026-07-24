"""Fail-closed planner diagnostics and field-owned invocation edit behavior."""

from typing import cast

import msgspec
import pytest

from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import ClearBase
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import KeyedSet
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopologyEdit
from comparo.core.domain.planning_inputs import InvocationValueOverlay
from comparo.core.domain.planning_inputs import MatrixSelection
from comparo.core.planning import iter_plan_cells
from comparo.core.planning import iter_plan_trials
from comparo.core.planning import plan_run
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _code(project: CompiledProject, **invocation_changes: object) -> str:
    result = plan_run(project, invocation(**invocation_changes), authority())
    assert result.prepared is None
    return result.diagnostics[0].code.value


def _minimal_project(defaults: str, *resources: str) -> CompiledProject:
    return compile_project(document("Project", "demo", f"{{defaults: {defaults}}}"), *resources)


def test_planner_public_boundary_rejects_untyped_inputs(
    comparison_project: CompiledProject,
) -> None:
    with pytest.raises(TypeError, match="CompiledProject"):
        plan_run(cast("CompiledProject", object()), invocation(), authority())
    with pytest.raises(TypeError, match="RunInvocation"):
        plan_run(comparison_project, object(), authority())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PlanningAuthority"):
        plan_run(comparison_project, invocation(), object())  # type: ignore[arg-type]
    prepared = plan_run(comparison_project, invocation(), authority()).prepared
    assert prepared is not None
    with pytest.raises(TypeError, match="RunPlan"):
        tuple(iter_plan_cells(object()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="RunPlan"):
        tuple(iter_plan_trials(object()))  # type: ignore[arg-type]


def test_missing_empty_scenario_ambiguous_and_missing_invocation_selections_fail() -> None:
    no_defaults = compile_project(document("Project", "demo", "{}"))
    assert _code(no_defaults) == "planning.project.defaults-missing"
    empty = _minimal_project(
        "{environments: [{$use: dev}]}",
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
    )
    assert _code(empty) == "planning.subjects.empty"
    scenario = _minimal_project(
        "{subjects: [{$use: Scenario/flow}], environments: [{$use: dev}]}",
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Scenario", "flow", "{steps: []}"),
    )
    assert _code(scenario) == "planning.scenario.not-supported"
    assert (
        _code(
            empty,
            subjects=CollectionOverridePlan(base=ReplaceBase(items=(ResourceId(value="missing"),))),
        )
        == "planning.subject.not-found-or-ambiguous"
    )
    ambiguous = _minimal_project(
        "{subjects: [{$use: Request/shared}], environments: [{$use: dev}]}",
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "shared", "{request: {method: GET, endpoint: /}}"),
        document("Scenario", "shared", "{steps: []}"),
    )
    assert (
        _code(
            ambiguous,
            subjects=CollectionOverridePlan(base=ReplaceBase(items=(ResourceId(value="shared"),))),
        )
        == "planning.subject.not-found-or-ambiguous"
    )


def test_missing_or_incomplete_topology_and_missing_profile_fail_before_work() -> None:
    resources = (
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /}}"),
    )
    missing = _minimal_project("{subjects: [{$use: Request/check}]}", *resources)
    assert _code(missing) == "planning.topology.missing"
    incomplete = _minimal_project(
        "{subjects: [{$use: Request/check}], baseline: {$use: dev}}", *resources
    )
    assert _code(incomplete) == "planning.topology.incomplete"
    assert (
        _code(missing, execution_profile=ResourceId(value="missing"))
        == "planning.resource.not-found"
    )


def test_policy_environment_denial_and_exact_cardinality_budgets_fail_closed(
    comparison_project: CompiledProject,
) -> None:
    denied = msgspec.structs.replace(authority(), allowed_environments=())
    result = plan_run(comparison_project, invocation(), denied)
    assert result.prepared is None
    assert result.diagnostics[0].code.value == "planning.policy.environment-denied"
    trial_ceilings = tuple(
        msgspec.structs.replace(item, limit=msgspec.structs.replace(item.limit, value=1))
        if item.resource is BudgetResource.TRIALS
        else item
        for item in authority().ceilings
    )
    result = plan_run(
        comparison_project,
        invocation(),
        msgspec.structs.replace(authority(), ceilings=trial_ceilings),
    )
    assert result.prepared is None
    assert result.diagnostics[0].code.value == "planning.budget.exceeded"

    two_requests = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/one}, {$use: Request/two}], "
            "environments: [{$use: dev}]}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "one", "{request: {method: GET, endpoint: /one}}"),
        document("Request", "two", "{request: {method: GET, endpoint: /two}}"),
    )
    request_ceilings = tuple(
        msgspec.structs.replace(item, limit=msgspec.structs.replace(item.limit, value=1))
        if item.resource is BudgetResource.REQUESTS
        else item
        for item in authority().ceilings
    )
    result = plan_run(
        two_requests,
        invocation(),
        msgspec.structs.replace(authority(), ceilings=request_ceilings),
    )
    assert result.prepared is None
    assert result.diagnostics[0].code.value == "planning.budget.exceeded"


def test_clear_remove_set_and_add_edits_have_fixed_field_owned_order(
    comparison_project: CompiledProject,
) -> None:
    run = invocation(
        subjects=CollectionOverridePlan(
            base=KeepBase(),
            remove=(ResourceId(value="chat"),),
            add=(ResourceId(value="chat"),),
        ),
        matrices=CollectionOverridePlan(
            base=ClearBase(),
            set_items=(
                KeyedSet(
                    identity=MatrixBindingId(value="primary"),
                    item=MatrixSelection(
                        binding=MatrixBindingId(value="primary"),
                        matrix=ResourceId(value="locales"),
                    ),
                ),
            ),
        ),
        trials=Clear(),
        maximum_attempts=Clear(),
    )
    result = plan_run(comparison_project, run, authority())
    assert result.prepared is not None
    assert result.prepared.effective.trials.value == 1
    assert result.prepared.effective.maximum_attempts.value == 1
    matrix = result.prepared.effective.subjects[0].matrices[0]
    assert matrix.binding.value == "primary"


def test_invocation_value_overlays_preserve_global_then_environment_specific_order(
    comparison_project: CompiledProject,
) -> None:
    project_spec = cast("CanonicalObject", comparison_project.project.spec)
    expression = cast(
        "ValueExpression",
        next(member.value for member in project_spec.members if member.name == "variables"),
    )
    result = plan_run(
        comparison_project,
        invocation(
            values=(
                InvocationValueOverlay(
                    environment=ResourceId(value="prod"),
                    variables=expression,
                ),
                InvocationValueOverlay(variables=expression),
            )
        ),
        authority(),
    )
    assert result.prepared is not None
    layers = result.prepared.effective.value_layers
    assert layers[-2].environment is None
    assert layers[-1].environment is not None
    assert layers[-1].environment.identity.resource_id.value == "prod"
    unselected = plan_run(
        comparison_project,
        invocation(
            values=(
                InvocationValueOverlay(
                    environment=ResourceId(value="staging"),
                    variables=expression,
                ),
            ),
            topology=AssertionTopology(environments=(ResourceId(value="prod"),)),
        ),
        authority(),
    )
    assert unselected.prepared is None
    assert unselected.diagnostics[0].code.value == "planning.values.environment-not-selected"


def test_duplicate_subject_and_matrix_bindings_are_diagnostics(
    comparison_project: CompiledProject,
) -> None:
    duplicate_subject = plan_run(
        comparison_project,
        invocation(
            subjects=CollectionOverridePlan(
                base=KeepBase(),
                add=(ResourceId(value="chat"),),
            )
        ),
        authority(),
    )
    assert duplicate_subject.prepared is None
    assert duplicate_subject.diagnostics[0].code.value == "planning.subjects.duplicate"
    duplicate_matrix = plan_run(
        comparison_project,
        invocation(
            matrices=CollectionOverridePlan(
                base=KeepBase(),
                add=(
                    MatrixSelection(
                        binding=MatrixBindingId(value="locales"),
                        matrix=ResourceId(value="locales"),
                    ),
                ),
            )
        ),
        authority(),
    )
    assert duplicate_matrix.prepared is None
    assert duplicate_matrix.diagnostics[0].code.value == "planning.matrix.binding-duplicate"


def test_comparison_topology_edits_fail_closed_when_incoherent(
    comparison_project: CompiledProject,
) -> None:
    cleared = plan_run(
        comparison_project,
        invocation(topology=ComparisonTopologyEdit(baseline=Clear())),
        authority(),
    )
    assert cleared.prepared is None
    assert cleared.diagnostics[0].code.value == "planning.topology.incomplete"

    no_candidates = plan_run(
        comparison_project,
        invocation(
            topology=ComparisonTopologyEdit(candidates=CollectionOverridePlan(base=ClearBase()))
        ),
        authority(),
    )
    assert no_candidates.prepared is None
    assert no_candidates.diagnostics[0].code.value == "planning.topology.incomplete"

    duplicate = plan_run(
        comparison_project,
        invocation(
            topology=ComparisonTopologyEdit(
                candidates=CollectionOverridePlan(
                    base=KeepBase(),
                    add=(ResourceId(value="prod"),),
                )
            )
        ),
        authority(),
    )
    assert duplicate.prepared is None
    assert duplicate.diagnostics[0].code.value == "planning.topology.candidate-duplicate"

    contradictory = plan_run(
        comparison_project,
        invocation(
            topology=ComparisonTopologyEdit(baseline=SetValue(value=ResourceId(value="prod")))
        ),
        authority(),
    )
    assert contradictory.prepared is None
    assert contradictory.diagnostics[0].code.value == "planning.topology.contradictory"

    assertion_project = _minimal_project(
        "{subjects: [{$use: Request/check}], environments: [{$use: dev}]}",
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /}}"),
    )
    wrong_mode = plan_run(
        assertion_project,
        invocation(topology=ComparisonTopologyEdit()),
        authority(),
    )
    assert wrong_mode.prepared is None
    assert wrong_mode.diagnostics[0].code.value == "planning.topology.comparison-edit-without-base"
