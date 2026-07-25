"""Tests for frontend-neutral planning invocation construction."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application.inspection import inspect_plan
from comparo.application.planning import BudgetOverride
from comparo.application.planning import InvocationBuildError
from comparo.application.planning import InvocationBuildErrorCode
from comparo.application.planning import MatrixOverride
from comparo.application.planning import PlanningOptions
from comparo.application.planning import ValueResourceOverride
from comparo.application.planning import build_invocation
from comparo.application.planning import plan_project
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopology
from comparo.core.domain.planning_inputs import InheritTopology
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import JsonString
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


def test_value_resources_group_by_scope_and_override_authored_layers() -> None:
    canary = "invocation-secret-value-canary"
    project = compile_project(
        document(
            "Project",
            "demo",
            "{variables: {source: project}, defaults: {subjects: [{$use: Request/ping}], "
            "environments: [{$use: dev}], variables: {source: defaults}}}",
        ),
        document(
            "Environment",
            "dev",
            "{baseUrl: 'https://dev.example', variables: {source: environment}}",
        ),
        document("Request", "ping", "{request: {method: GET, endpoint: /ping}}"),
        document("Value", "params", "{value: {tenant: acme}}"),
        document("Value", "vars", "{value: {source: invocation}}"),
        document("Value", "secret", f"{{value: {{token: {canary}}}}}"),
    )
    result = plan_project(
        project,
        authority(),
        PlanningOptions(
            value_resources=(
                ValueResourceOverride(
                    namespace=ValueNamespace.SECRETS,
                    environment="dev",
                    resource="secret",
                ),
                ValueResourceOverride(
                    namespace=ValueNamespace.VARIABLES,
                    environment=None,
                    resource="Value/vars",
                ),
                ValueResourceOverride(
                    namespace=ValueNamespace.PARAMETERS,
                    environment=None,
                    resource="params",
                ),
            )
        ),
        invocation_id=_INVOCATION_ID,
        run_id=_RUN_ID,
    )

    prepared = result.prepared
    assert prepared is not None
    global_overlay, environment_overlay = prepared.invocation.values
    assert global_overlay.environment is None
    assert type(global_overlay.parameters) is ObjectExpr
    assert type(global_overlay.variables) is ObjectExpr
    assert environment_overlay.environment is not None
    assert environment_overlay.environment.value == "dev"
    assert type(environment_overlay.secrets) is ObjectExpr
    assert type(environment_overlay.secrets.members[0].expression) is SensitiveLiteralExpr
    assert tuple(layer.source.kind for layer in prepared.effective.value_layers[-2:]) == (
        ProvenanceKind.INVOCATION,
        ProvenanceKind.INVOCATION,
    )
    assert prepared.effective.value_layers[-2].variables == global_overlay.variables
    assert prepared.effective.value_layers[-1].secrets == environment_overlay.secrets
    serialized = msgspec.json.encode(inspect_plan(prepared)).decode()
    assert canary not in serialized
    assert canary not in repr(prepared.effective.value_layers)

    reordered = plan_project(
        project,
        authority(),
        PlanningOptions(
            value_resources=(
                ValueResourceOverride(
                    namespace=ValueNamespace.PARAMETERS,
                    environment=None,
                    resource="params",
                ),
                ValueResourceOverride(
                    namespace=ValueNamespace.SECRETS,
                    environment="dev",
                    resource="secret",
                ),
                ValueResourceOverride(
                    namespace=ValueNamespace.VARIABLES,
                    environment=None,
                    resource="vars",
                ),
            )
        ),
        invocation_id=_INVOCATION_ID,
        run_id=_RUN_ID,
    ).prepared
    assert reordered is not None
    assert tuple(item.environment for item in reordered.invocation.values) == tuple(
        item.environment for item in prepared.invocation.values
    )
    assert tuple(
        tuple(member.name for member in cast("ObjectExpr", item.variables).members)
        for item in reordered.invocation.values
        if item.variables is not None
    ) == (("source",),)
    assert reordered.plan.fingerprint == prepared.plan.fingerprint


@pytest.mark.parametrize(
    ("reference", "code"),
    [
        ("Request/not-a-value", InvocationBuildErrorCode.VALUE_RESOURCE_KIND_INVALID),
        ("Value/", InvocationBuildErrorCode.INVALID_VALUE_RESOURCE),
        ("too/many/parts", InvocationBuildErrorCode.INVALID_VALUE_RESOURCE),
        ("NOT-A-SLUG", InvocationBuildErrorCode.INVALID_VALUE_RESOURCE),
    ],
)
def test_value_resource_references_reject_malformed_or_wrong_kind_without_echoing(
    reference: str,
    code: InvocationBuildErrorCode,
) -> None:
    with pytest.raises(InvocationBuildError) as caught:
        ValueResourceOverride(
            namespace=ValueNamespace.VARIABLES,
            environment=None,
            resource=reference,
        )
    assert caught.value.code is code
    assert reference not in str(caught.value)


def test_value_resource_binding_rejects_invalid_targets_and_scopes() -> None:
    with pytest.raises(InvocationBuildError) as duplicate:
        PlanningOptions(
            value_resources=(
                ValueResourceOverride(
                    namespace=ValueNamespace.VARIABLES,
                    environment=None,
                    resource="first",
                ),
                ValueResourceOverride(
                    namespace=ValueNamespace.VARIABLES,
                    environment=None,
                    resource="second",
                ),
            )
        )
    assert duplicate.value.code is InvocationBuildErrorCode.DUPLICATE_VALUE_RESOURCE

    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/ping}], environments: [{$use: dev}]}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Environment", "other", "{baseUrl: 'https://other.example'}"),
        document("Request", "ping", "{request: {method: GET, endpoint: /ping}}"),
        document("Value", "plain-scalar", "{value: nope}"),
        document("Value", "object", "{value: {answer: 42}}"),
    )

    for resource, code in (
        ("does-not-exist", InvocationBuildErrorCode.VALUE_RESOURCE_MISSING),
        ("plain-scalar", InvocationBuildErrorCode.VALUE_RESOURCE_OBJECT_REQUIRED),
    ):
        with pytest.raises(InvocationBuildError) as caught:
            plan_project(
                project,
                authority(),
                PlanningOptions(
                    value_resources=(
                        ValueResourceOverride(
                            namespace=ValueNamespace.VARIABLES,
                            environment=None,
                            resource=resource,
                        ),
                    )
                ),
                invocation_id=_INVOCATION_ID,
                run_id=_RUN_ID,
            )
        assert caught.value.code is code
        assert resource not in str(caught.value)

    object_resource = next(
        resource
        for resource in project.resources
        if resource.definition.identity.resource_id.value == "object"
    )
    malformed_project = msgspec.structs.replace(
        project,
        resources=tuple(
            msgspec.structs.replace(resource, spec=JsonString(value="invalid"))
            if resource is object_resource
            else resource
            for resource in project.resources
        ),
    )
    with pytest.raises(InvocationBuildError) as malformed:
        plan_project(
            malformed_project,
            authority(),
            PlanningOptions(
                value_resources=(
                    ValueResourceOverride(
                        namespace=ValueNamespace.VARIABLES,
                        environment=None,
                        resource="object",
                    ),
                )
            ),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    assert malformed.value.code is InvocationBuildErrorCode.VALUE_RESOURCE_OBJECT_REQUIRED

    unselected = plan_project(
        project,
        authority(),
        PlanningOptions(
            value_resources=(
                ValueResourceOverride(
                    namespace=ValueNamespace.VARIABLES,
                    environment="other",
                    resource="object",
                ),
            )
        ),
        invocation_id=_INVOCATION_ID,
        run_id=_RUN_ID,
    )
    assert unselected.prepared is None
    assert unselected.diagnostics[0].code.value == "planning.values.environment-not-selected"


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
    with pytest.raises(TypeError, match="namespace"):
        ValueResourceOverride(
            namespace=cast("ValueNamespace", object()),
            environment=None,
            resource="shared",
        )
    with pytest.raises(InvocationBuildError) as namespace:
        ValueResourceOverride(
            namespace=ValueNamespace.ENV,
            environment=None,
            resource="shared",
        )
    assert namespace.value.code is InvocationBuildErrorCode.INVALID_VALUE_NAMESPACE
    with pytest.raises(TypeError, match="Environment"):
        ValueResourceOverride(
            namespace=ValueNamespace.VARIABLES,
            environment=cast("str", object()),
            resource="shared",
        )
    with pytest.raises(InvocationBuildError) as environment:
        ValueResourceOverride(
            namespace=ValueNamespace.VARIABLES,
            environment="NOT VALID",
            resource="shared",
        )
    assert environment.value.code is InvocationBuildErrorCode.INVALID_VALUE_RESOURCE
    with pytest.raises(TypeError, match="resource"):
        ValueResourceOverride(
            namespace=ValueNamespace.VARIABLES,
            environment=None,
            resource=cast("str", object()),
        )
    override = ValueResourceOverride(
        namespace=ValueNamespace.VARIABLES,
        environment=None,
        resource="shared",
    )
    with pytest.raises(TypeError, match="Value resource"):
        PlanningOptions(value_resources=cast("tuple[ValueResourceOverride, ...]", []))
    with pytest.raises(InvocationBuildError) as project_required:
        _build(PlanningOptions(value_resources=(override,)))
    assert project_required.value.code is InvocationBuildErrorCode.VALUE_RESOURCE_MISSING
    with pytest.raises(TypeError, match="CompiledProject"):
        build_invocation(
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
            project=cast("CompiledProject", object()),
        )


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
