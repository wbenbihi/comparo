"""Defensive tests for canonical graphs constructed outside the trusted compiler path."""

from typing import cast

import msgspec
import pytest

from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import KeyedSet
from comparo.core.domain.primitives import Count
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.planning import plan_run
from comparo.core.planning._canonical import literal_string
from comparo.core.planning._canonical import resource_spec
from comparo.core.planning._errors import PlanningError
from comparo.core.planning.binding import _append_value_layer
from comparo.core.planning.binding import _bind_bool
from comparo.core.planning.binding import _bind_count
from comparo.core.planning.binding import _expand_profiles
from comparo.core.planning.binding import _Layer
from comparo.core.planning.binding import _linked_ids
from comparo.core.planning.binding import _linked_profile
from comparo.core.planning.binding import _roots
from comparo.core.planning.binding import _subject_references
from comparo.core.planning.binding import _topology_from_layer
from comparo.core.planning.budgeting import _safety_budget_map
from comparo.core.planning.budgeting import compose_planning_budgets
from comparo.core.planning.budgeting import parse_budget_map
from comparo.core.planning.matrices import compile_matrix
from comparo.core.planning.models import EffectiveProfiles
from comparo.core.planning.models import PlanningSource
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _resource(
    project: CompiledProject,
    kind: ResourceKind,
    resource_id: str,
) -> CompiledResource:
    result = project.find(kind, ResourceId(value=resource_id))
    assert result is not None
    return result


def test_canonical_accessors_reject_wrong_spec_and_static_string_shape(
    comparison_project: CompiledProject,
) -> None:
    request = _resource(comparison_project, ResourceKind.REQUEST, "chat")
    malformed = msgspec.structs.replace(request, spec=JsonString(value="wrong"))
    with pytest.raises(PlanningError, match=r"planning.resource.spec-invalid"):
        resource_spec(malformed)
    with pytest.raises(PlanningError, match=r"planning.matrix.static-id-required"):
        literal_string(JsonString(value="wrong"), owner=request, field="id")


def test_boolean_binding_defends_static_shape_and_all_override_variants(
    comparison_project: CompiledProject,
) -> None:
    owner = comparison_project.project
    enabled = _Layer(
        owner,
        CanonicalObject(members=(CanonicalMember(name="failFast", value=JsonBoolean(value=True)),)),
        ProvenanceKind.PROJECT,
    )
    value, sources = _bind_bool(
        (enabled,),
        Inherit(),
        field="failFast",
        default=False,
        invocation=invocation(),
    )
    assert value is True
    assert tuple(item.kind for item in sources) == (
        ProvenanceKind.VERSIONED_DEFAULT,
        ProvenanceKind.PROJECT,
    )

    cleared, clear_sources = _bind_bool(
        (enabled,),
        Clear(),
        field="failFast",
        default=False,
        invocation=invocation(),
    )
    assert cleared is False
    assert clear_sources[-1].kind is ProvenanceKind.INVOCATION

    malformed = _Layer(
        owner,
        CanonicalObject(members=(CanonicalMember(name="failFast", value=JsonString(value="yes")),)),
        ProvenanceKind.PROJECT,
    )
    with pytest.raises(PlanningError, match=r"planning.field.shape-invalid"):
        _bind_bool(
            (malformed,),
            Inherit(),
            field="failFast",
            default=False,
            invocation=invocation(),
        )
    with pytest.raises(TypeError, match="boolean override"):
        _bind_bool(
            (),
            cast("Inherit", object()),
            field="failFast",
            default=False,
            invocation=invocation(),
        )


def test_subject_and_topology_helpers_defend_link_shape_and_fingerprint(
    comparison_project: CompiledProject,
) -> None:
    owner = comparison_project.project
    request = _resource(comparison_project, ResourceKind.REQUEST, "chat")
    environment = _resource(comparison_project, ResourceKind.ENVIRONMENT, "prod")
    with pytest.raises(PlanningError, match=r"planning.field.shape-invalid"):
        _subject_references(
            comparison_project,
            JsonString(value="wrong"),
            owner=owner,
        )
    with pytest.raises(PlanningError, match=r"planning.reference.kind-invalid"):
        _subject_references(
            comparison_project,
            CanonicalArray(items=(JsonString(value="wrong"),)),
            owner=owner,
        )
    changed = msgspec.structs.replace(
        request.definition,
        fingerprint=environment.definition.fingerprint,
    )
    with pytest.raises(PlanningError, match=r"planning.reference.inconsistent"):
        _subject_references(
            comparison_project,
            CanonicalArray(items=(LinkedResource(target=changed),)),
            owner=owner,
        )
    layer = _Layer(owner, cast("CanonicalObject", owner.spec), ProvenanceKind.PROJECT)
    link = LinkedResource(target=environment.definition)
    with pytest.raises(PlanningError, match=r"planning.topology.contradictory"):
        _topology_from_layer(layer, link, None, CanonicalArray(items=(link,)))
    with pytest.raises(PlanningError, match=r"planning.field.shape-invalid"):
        _linked_ids(JsonString(value="wrong"), ResourceKind.ENVIRONMENT, owner=owner, field="x")
    with pytest.raises(PlanningError, match=r"planning.reference.kind-invalid"):
        _linked_ids(
            CanonicalArray(items=(LinkedResource(target=request.definition),)),
            ResourceKind.ENVIRONMENT,
            owner=owner,
            field="x",
        )
    with pytest.raises(PlanningError, match=r"planning.field.empty"):
        _linked_ids(CanonicalArray(items=()), ResourceKind.ENVIRONMENT, owner=owner, field="x")


def test_profile_helpers_reject_malformed_inconsistent_and_cyclic_graphs(
    comparison_project: CompiledProject,
) -> None:
    owner = comparison_project.project
    layer = _Layer(owner, cast("CanonicalObject", owner.spec), ProvenanceKind.PROJECT)
    with pytest.raises(PlanningError, match=r"planning.field.shape-invalid"):
        _roots(
            comparison_project,
            _Layer(
                layer.resource,
                CanonicalObject(
                    members=(CanonicalMember(name="assertions", value=JsonString(value="bad")),)
                ),
                layer.source_kind,
            ),
            "assertions",
            multiple=True,
        )
    with pytest.raises(PlanningError, match=r"planning.profile.invalid"):
        _roots(
            comparison_project,
            _Layer(
                layer.resource,
                CanonicalObject(
                    members=(
                        CanonicalMember(
                            name="assertions",
                            value=CanonicalArray(items=(JsonString(value="bad"),)),
                        ),
                    )
                ),
                layer.source_kind,
            ),
            "assertions",
            multiple=True,
        )
    profile = _resource(comparison_project, ResourceKind.ASSERTION_PROFILE, "common-assertions")
    changed = msgspec.structs.replace(
        profile.definition,
        fingerprint=owner.definition.fingerprint,
    )
    with pytest.raises(PlanningError, match=r"planning.profile.inconsistent"):
        _linked_profile(comparison_project, owner, LinkedResource(target=changed))

    malformed = msgspec.structs.replace(
        profile,
        spec=CanonicalObject(
            members=(CanonicalMember(name="include", value=JsonString(value="bad")),)
        ),
    )
    with pytest.raises(PlanningError, match=r"planning.profile.include-invalid"):
        _expand_profiles(comparison_project, (malformed,), "include")
    inconsistent = msgspec.structs.replace(
        profile,
        spec=CanonicalObject(
            members=(
                CanonicalMember(
                    name="include",
                    value=CanonicalArray(items=(LinkedResource(target=changed),)),
                ),
            )
        ),
    )
    with pytest.raises(PlanningError, match=r"planning.profile.include-inconsistent"):
        _expand_profiles(comparison_project, (inconsistent,), "include")

    self_linked = msgspec.structs.replace(
        profile,
        spec=CanonicalObject(
            members=(
                CanonicalMember(
                    name="include",
                    value=CanonicalArray(items=(LinkedResource(target=profile.definition),)),
                ),
            )
        ),
    )
    resources = tuple(
        self_linked if item.definition == profile.definition else item
        for item in comparison_project.resources
    )
    cyclic_project = msgspec.structs.replace(comparison_project, resources=resources)
    with pytest.raises(PlanningError, match=r"planning.profile.cycle"):
        _expand_profiles(cyclic_project, (self_linked,), "include")
    base = _resource(comparison_project, ResourceKind.ASSERTION_PROFILE, "base-assertions")
    assert _expand_profiles(comparison_project, (base, profile), "include") == (
        base,
        profile,
    )


def test_budget_helpers_reject_noncanonical_maps_and_malformed_safety(
    comparison_project: CompiledProject,
) -> None:
    owner = comparison_project.project
    with pytest.raises(PlanningError, match=r"planning.budgets.shape-invalid"):
        parse_budget_map(JsonString(value="bad"), owner=owner)
    with pytest.raises(PlanningError, match=r"planning.budgets.resource-unsupported"):
        parse_budget_map(
            JsonObject(members=(JsonObjectMember(name="unknown", value=JsonInteger(value=1)),)),
            owner=owner,
        )
    with pytest.raises(PlanningError, match=r"planning.budgets.limit-invalid"):
        parse_budget_map(
            JsonObject(members=(JsonObjectMember(name="trials", value=JsonInteger(value=0)),)),
            owner=owner,
        )
    safety = _resource(comparison_project, ResourceKind.SAFETY_PROFILE, "project-safety")
    assert (
        _safety_budget_map(msgspec.structs.replace(safety, spec=CanonicalObject(members=())))[1]
        == {}
    )
    with pytest.raises(PlanningError, match=r"planning.resource.spec-invalid"):
        _safety_budget_map(msgspec.structs.replace(safety, spec=JsonString(value="bad")))

    inline_project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "safety: {budgets: {trials: 2}}}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /}}"),
    )
    project_spec = cast("CanonicalObject", inline_project.project.spec)
    defaults = cast(
        "CanonicalObject",
        next(member.value for member in project_spec.members if member.name == "defaults"),
    )
    inline = cast(
        "InlineResource",
        next(member.value for member in defaults.members if member.name == "safety"),
    )
    with pytest.raises(PlanningError, match=r"planning.budgets.shape-invalid"):
        _safety_budget_map(
            msgspec.structs.replace(
                inline,
                spec=CanonicalObject(
                    members=(CanonicalMember(name="budgets", value=JsonString(value="bad")),)
                ),
            )
        )
    with pytest.raises(PlanningError, match=r"planning.budgets.limit-invalid"):
        _safety_budget_map(
            msgspec.structs.replace(
                inline,
                spec=CanonicalObject(
                    members=(
                        CanonicalMember(
                            name="budgets",
                            value=JsonObject(
                                members=(
                                    JsonObjectMember(name="trials", value=JsonInteger(value=0)),
                                )
                            ),
                        ),
                    )
                ),
            )
        )


def test_remaining_defensive_helpers_and_valid_profile_branch(
    comparison_project: CompiledProject,
) -> None:
    request = _resource(comparison_project, ResourceKind.REQUEST, "chat")
    matrix = _resource(comparison_project, ResourceKind.MATRIX, "locales")
    empty_matrix = msgspec.structs.replace(matrix, spec=CanonicalObject(members=()))
    with pytest.raises(PlanningError, match=r"planning.matrix.input-missing"):
        compile_matrix(
            empty_matrix,
            MatrixBindingId(value="empty"),
            maximum_cases=10,
        )
    source = PlanningSource(kind=ProvenanceKind.PROJECT, definition=request.definition)
    with pytest.raises(PlanningError, match=r"planning.values.expression-invalid"):
        _append_value_layer(
            [],
            source=source,
            spec=CanonicalObject(
                members=(CanonicalMember(name="variables", value=JsonString(value="bad")),)
            ),
        )
    layer = _Layer(
        comparison_project.project,
        cast("CanonicalObject", comparison_project.project.spec),
        ProvenanceKind.PROJECT,
    )
    with pytest.raises(TypeError, match="count override"):
        _bind_count(
            (layer,),
            object(),
            field="trials",
            default=1,
            invocation=invocation(),
        )

    gate_project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "gate: {$use: strict}}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("GateProfile", "strict", "{}"),
        document("Request", "check", "{request: {method: GET, endpoint: /}}"),
    )
    result = plan_run(gate_project, invocation(), authority())
    assert result.prepared is not None
    gate = result.prepared.effective.subjects[0].profiles.gate
    assert isinstance(gate, DefinitionRef)
    assert EffectiveProfiles(gate=gate).gate == gate

    invalid_count = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "trials: 0}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /}}"),
    )
    invalid_result = plan_run(invalid_count, invocation(), authority())
    assert invalid_result.prepared is None
    assert invalid_result.diagnostics[0].code.value == "planning.field.positive-integer-required"


def test_ordinary_project_budget_and_empty_execution_profile_budget_paths() -> None:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "budgets: {trials: 3}}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /}}"),
        document("ExecutionProfile", "empty", "{environments: [{$use: dev}]}"),
    )
    result = plan_run(
        project,
        invocation(execution_profile=ResourceId(value="empty")),
        authority(),
    )
    assert result.prepared is not None
    trials = next(
        budget for budget in result.prepared.plan.budgets if budget.resource.value == "trials"
    )
    assert cast("Count", trials.requested).value == 3
    profile = _resource(project, ResourceKind.EXECUTION_PROFILE, "empty")
    project_spec = cast("CanonicalObject", project.project.spec)
    defaults = cast(
        "CanonicalObject",
        next(member.value for member in project_spec.members if member.name == "defaults"),
    )
    with pytest.raises(PlanningError, match=r"planning.resource.spec-invalid"):
        compose_planning_budgets(
            project_resource=project.project,
            project_defaults=defaults,
            execution_profile=msgspec.structs.replace(
                profile,
                spec=JsonString(value="bad"),
            ),
            safety_profiles=(),
            invocation=invocation(),
            authority=authority(),
        )


def test_request_matrix_collision_and_subject_keyed_set_are_explicit() -> None:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}], "
            "matrices: [{$use: cases}]}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Matrix", "cases", "{cases: [{id: one, values: {value: 1}}]}"),
        document(
            "Request",
            "check",
            "{request: {method: GET, endpoint: /}, matrices: [{$use: cases}]}",
        ),
    )
    collision = plan_run(project, invocation(), authority())
    assert collision.prepared is None
    assert collision.diagnostics[0].code.value == "planning.matrix.binding-duplicate"

    ordinary = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], environments: [{$use: dev}]}}",
        ),
        document("Environment", "dev", "{baseUrl: 'https://dev.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /}}"),
    )
    result = plan_run(
        ordinary,
        invocation(
            subjects=CollectionOverridePlan(
                base=KeepBase(),
                set_items=(
                    KeyedSet(
                        identity=ResourceId(value="check"),
                        item=ResourceId(value="check"),
                    ),
                ),
            )
        ),
        authority(),
    )
    assert result.prepared is not None
