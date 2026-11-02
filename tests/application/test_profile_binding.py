"""Project profile binding and caller-authority composition tests."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.application import profile_binding as profile_module
from comparo.application.evaluation_selectors import InvalidSelectorError
from comparo.application.evaluation_selectors import SelectorCompileError
from comparo.application.production_execution import persisted_production_execution_defaults_v1
from comparo.application.profile_binding import BoundExecutionProfiles
from comparo.application.profile_binding import EnvironmentResponseClassification
from comparo.application.profile_binding import ProfileBindingResult
from comparo.application.profile_binding import RemoteSchemaRestriction
from comparo.application.profile_binding import bind_execution_profiles
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import RedactionAction
from comparo.core.artifacts.policy import RedactionPolicy
from comparo.core.artifacts.policy import UnsafeSourceBehavior
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import InlineResource
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.planning import plan_run
from comparo.core.planning.models import DefinitionHandle
from comparo.core.planning.models import PreparedRun
from comparo.core.policy import planning_authority
from comparo.core.policy import trusted_local_policy
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _prepared(*resources: str) -> PreparedRun:
    compiled = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}], redaction: {$use: RedactionProfile/safe}, "
            "artifacts: {$use: ArtifactProfile/ci}}}",
        ),
        document(
            "Environment",
            "staging",
            "{baseUrl: 'https://staging.example', safety: {$use: SafetyProfile/private}}",
        ),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document("Request", "check", "{request: {method: GET, endpoint: /check}}"),
        *resources,
    )
    policy = trusted_local_policy()
    planned = plan_run(
        compiled,
        invocation(),
        planning_authority(policy, project_namespace=compiled.namespace),
    )
    assert planned.prepared is not None
    return planned.prepared


def _bind_specs(
    *,
    redaction: str = "{}",
    artifact: str = "{}",
    safety: str = "{}",
) -> ProfileBindingResult:
    settings = persisted_production_execution_defaults_v1()
    return bind_execution_profiles(
        _prepared(
            document("RedactionProfile", "safe", redaction),
            document("ArtifactProfile", "ci", artifact),
            document("SafetyProfile", "private", safety),
        ),
        artifact=settings.artifact,
        response=settings.transport.classification,
    )


def test_profiles_restrict_capture_and_classify_each_environment() -> None:
    prepared = _prepared(
        document(
            "RedactionProfile",
            "safe",
            "{rules: [{id: account, selector: 'response.headers:x-account', action: remove}]}",
        ),
        document(
            "ArtifactProfile",
            "ci",
            "{capture: {evidence: false}, storage: {persist: on-gate-failure}}",
        ),
        document(
            "SafetyProfile",
            "private",
            "{classification: {responseBody: sensitive, responseHeaders: [x-private]}}",
        ),
    )
    settings = persisted_production_execution_defaults_v1()

    result = bind_execution_profiles(
        prepared,
        artifact=settings.artifact,
        response=settings.transport.classification,
    )

    assert result.diagnostics == ()
    assert result.profiles is not None
    assert result.profiles.artifact.persist is ArtifactPersistMode.ON_GATE_FAILURE
    assert not result.profiles.artifact.capture.evidence
    assert result.profiles.redaction.rules[0].action is RedactionAction.REMOVE
    staging, prod = result.profiles.responses
    assert staging.classification.body is Sensitivity.SENSITIVE
    assert tuple(item.value for item in staging.classification.sensitive_headers) == ("x-private",)
    assert prod.classification.body is Sensitivity.PUBLIC
    assert prod.classification.sensitive_headers == ()


def test_profile_cannot_raise_caller_persistence_or_capture_authority() -> None:
    prepared = _prepared(
        document("RedactionProfile", "safe", "{}"),
        document(
            "ArtifactProfile",
            "ci",
            "{capture: {bodies: true}, storage: {persist: always}}",
        ),
        document("SafetyProfile", "private", "{}"),
    )
    settings = persisted_production_execution_defaults_v1()
    caller = settings.artifact
    caller = caller.__class__(
        persist=ArtifactPersistMode.NEVER,
        unsafe_source=caller.unsafe_source,
        capture=caller.capture.__class__(
            requests=True,
            responses=True,
            bodies=False,
            evidence=True,
        ),
        limits=caller.limits,
    )

    result = bind_execution_profiles(
        prepared,
        artifact=caller,
        response=settings.transport.classification,
    )

    assert result.profiles is not None
    assert result.profiles.artifact.persist is ArtifactPersistMode.NEVER
    assert not result.profiles.artifact.capture.bodies


def test_safety_profile_remote_schema_sources_are_exact_project_restrictions() -> None:
    prepared = _prepared(
        document("RedactionProfile", "safe", "{}"),
        document("ArtifactProfile", "ci", "{}"),
        document(
            "SafetyProfile",
            "private",
            "{schemas: {sources: ['https://schemas.example.test/response.json']}}",
        ),
    )
    settings = persisted_production_execution_defaults_v1()

    result = bind_execution_profiles(
        prepared,
        artifact=settings.artifact,
        response=settings.transport.classification,
    )

    assert result.diagnostics == ()
    assert result.profiles is not None
    assert not result.profiles.remote_schemas.allow_any
    assert result.profiles.remote_schemas.sources == ("https://schemas.example.test/response.json",)
    assert result.profiles.remote_schemas.allows("https://schemas.example.test/response.json")
    assert not result.profiles.remote_schemas.allows("https://schemas.example.test/other.json")


def test_present_empty_schema_restriction_denies_all_while_absence_is_unrestricted() -> None:
    settings = persisted_production_execution_defaults_v1()
    absent = bind_execution_profiles(
        _prepared(
            document("RedactionProfile", "safe", "{}"),
            document("ArtifactProfile", "ci", "{}"),
            document("SafetyProfile", "private", "{}"),
        ),
        artifact=settings.artifact,
        response=settings.transport.classification,
    )
    empty = bind_execution_profiles(
        _prepared(
            document("RedactionProfile", "safe", "{}"),
            document("ArtifactProfile", "ci", "{}"),
            document("SafetyProfile", "private", "{schemas: {}}"),
        ),
        artifact=settings.artifact,
        response=settings.transport.classification,
    )

    assert absent.profiles is not None
    assert absent.profiles.remote_schemas.allow_any
    assert empty.profiles is not None
    assert not empty.profiles.remote_schemas.allow_any
    assert empty.profiles.remote_schemas.sources == ()


def test_malformed_safety_schema_authority_fails_closed() -> None:
    prepared = _prepared(
        document("RedactionProfile", "safe", "{}"),
        document("ArtifactProfile", "ci", "{}"),
        document(
            "SafetyProfile",
            "private",
            "{schemas: {sources: ['https://schemas.example.test/schema.json?token=secret']}}",
        ),
    )
    settings = persisted_production_execution_defaults_v1()

    result = bind_execution_profiles(
        prepared,
        artifact=settings.artifact,
        response=settings.transport.classification,
    )

    assert result.profiles is None
    assert tuple(item.code.value for item in result.diagnostics) == (
        "runtime.profile.shape-invalid",
    )


def test_unsupported_profile_capability_fails_closed_without_payloads() -> None:
    prepared = _prepared(
        document("RedactionProfile", "safe", "{}"),
        document("ArtifactProfile", "ci", "{}"),
        document("SafetyProfile", "private", "{network: {hosts: [private.example]}}"),
    )
    settings = persisted_production_execution_defaults_v1()

    result = bind_execution_profiles(
        prepared,
        artifact=settings.artifact,
        response=settings.transport.classification,
    )

    assert result.profiles is None
    assert tuple(item.code.value for item in result.diagnostics) == (
        "runtime.profile.field-unsupported",
    )
    assert b"private.example" not in __import__("msgspec").json.encode(result)


def test_profile_contracts_reject_untyped_and_contradictory_state() -> None:
    prepared = _prepared(
        document("RedactionProfile", "safe", "{}"),
        document("ArtifactProfile", "ci", "{}"),
        document("SafetyProfile", "private", "{}"),
    )
    settings = persisted_production_execution_defaults_v1()
    environment = prepared.effective.environments[0]
    classification = settings.transport.classification
    response = EnvironmentResponseClassification(
        environment=environment,
        classification=classification,
    )

    with pytest.raises(TypeError, match="Environment definition"):
        EnvironmentResponseClassification(
            environment=prepared.effective.subjects[0].definition,
            classification=classification,
        )
    with pytest.raises(TypeError, match="classification must be typed"):
        msgspec.structs.replace(response, classification=object())

    for sources, allow_any, error in (
        (cast("tuple[str, ...]", ["https://schemas.example.test/a"]), False, TypeError),
        (("ftp://schemas.example.test/a",), False, TypeError),
        (("https://schemas.example.test:bad/a",), False, TypeError),
        (("https://user@schemas.example.test/a",), False, TypeError),
        (("https://schemas.example.test/a?x=1",), False, TypeError),
        (("https://schemas.example.test/a#x",), False, TypeError),
        (
            (
                "https://schemas.example.test/b",
                "https://schemas.example.test/a",
            ),
            False,
            ValueError,
        ),
        (("https://schemas.example.test/a",) * 2, False, ValueError),
        ((), cast("bool", 1), TypeError),
        (("https://schemas.example.test/a",), True, ValueError),
    ):
        with pytest.raises(error):
            RemoteSchemaRestriction(sources=sources, allow_any=allow_any)
    with pytest.raises(TypeError, match="lookup"):
        RemoteSchemaRestriction().allows(cast("str", object()))

    profiles = BoundExecutionProfiles(
        artifact=settings.artifact,
        redaction=RedactionPolicy(rules=()),
        responses=(response,),
    )
    for changes in (
        {"artifact": cast("ArtifactPolicy", object())},
        {"redaction": cast("RedactionPolicy", object())},
        {"responses": cast("tuple[EnvironmentResponseClassification, ...]", [response])},
        {"responses": (cast("EnvironmentResponseClassification", object()),)},
        {"remote_schemas": cast("RemoteSchemaRestriction", object())},
    ):
        with pytest.raises(TypeError):
            msgspec.structs.replace(profiles, **changes)
    with pytest.raises(TypeError, match="DefinitionRef"):
        profiles.classification_for(cast("DefinitionRef", object()))
    absent_environment = msgspec.structs.replace(
        environment,
        identity=msgspec.structs.replace(
            environment.identity,
            resource_id=environment.identity.resource_id.__class__(value="absent"),
        ),
    )
    with pytest.raises(KeyError, match="not selected"):
        profiles.classification_for(absent_environment)

    diagnostic = _bind_specs(safety="{classification: plain}").diagnostics[0]
    with pytest.raises(TypeError, match="product"):
        ProfileBindingResult(profiles=cast("BoundExecutionProfiles", object()))
    with pytest.raises(TypeError, match="diagnostics"):
        ProfileBindingResult(diagnostics=cast("tuple[Diagnostic, ...]", [diagnostic]))
    with pytest.raises(ValueError, match="exactly"):
        ProfileBindingResult()
    with pytest.raises(ValueError, match="exactly"):
        ProfileBindingResult(profiles=profiles, diagnostics=(diagnostic,))
    with pytest.raises(TypeError, match="PreparedRun"):
        bind_execution_profiles(
            cast("PreparedRun", object()),
            artifact=settings.artifact,
            response=classification,
        )
    with pytest.raises(TypeError, match="caller-owned defaults"):
        bind_execution_profiles(
            prepared,
            artifact=cast("ArtifactPolicy", object()),
            response=classification,
        )


@pytest.mark.parametrize(
    ("redaction", "artifact", "safety", "expected"),
    [
        ("{backstop: plain}", "{}", "{}", "runtime.profile.shape-invalid"),
        ("{rules: plain}", "{}", "{}", "runtime.profile.shape-invalid"),
        ("{rules: [plain]}", "{}", "{}", "runtime.profile.shape-invalid"),
        ("{rules: [{id: x}]}", "{}", "{}", "runtime.profile.shape-invalid"),
        (
            "{rules: [{selector: response.status, action: invalid}]}",
            "{}",
            "{}",
            "runtime.profile.shape-invalid",
        ),
        (
            "{rules: [{selector: {source: response.body, path: '['}}]}",
            "{}",
            "{}",
            "runtime.profile.shape-invalid",
        ),
        ("{}", "{retention: {days: 1}}", "{}", "runtime.profile.field-unsupported"),
        ("{}", "{capture: plain}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{capture: {unknown: true}}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{capture: {bodies: plain}}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{storage: plain}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{storage: {unknown: true}}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{storage: {persist: invalid}}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{storage: {persist: true}}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{storage: {unsafeSource: invalid}}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{storage: {unsafeSource: true}}", "{}", "runtime.profile.shape-invalid"),
        ("{}", "{}", "{schemas: plain}", "runtime.profile.shape-invalid"),
        ("{}", "{}", "{schemas: {unknown: true}}", "runtime.profile.shape-invalid"),
        ("{}", "{}", "{schemas: {allowAny: plain}}", "runtime.profile.shape-invalid"),
        ("{}", "{}", "{schemas: {sources: plain}}", "runtime.profile.shape-invalid"),
        ("{}", "{}", "{schemas: {sources: [1]}}", "runtime.profile.shape-invalid"),
        (
            "{}",
            "{}",
            "{schemas: {allowAny: true, sources: ['https://schemas.example.test/a']}}",
            "runtime.profile.shape-invalid",
        ),
        ("{}", "{}", "{classification: plain}", "runtime.profile.shape-invalid"),
        ("{}", "{}", "{classification: {unknown: true}}", "runtime.profile.shape-invalid"),
        (
            "{}",
            "{}",
            "{classification: {responseBody: invalid}}",
            "runtime.profile.shape-invalid",
        ),
        (
            "{}",
            "{}",
            "{classification: {responseHeaders: plain}}",
            "runtime.profile.shape-invalid",
        ),
        (
            "{}",
            "{}",
            "{classification: {responseHeaders: ['bad header']}}",
            "runtime.profile.shape-invalid",
        ),
    ],
)
def test_every_invalid_supported_profile_shape_fails_closed(
    redaction: str,
    artifact: str,
    safety: str,
    expected: str,
) -> None:
    result = _bind_specs(redaction=redaction, artifact=artifact, safety=safety)

    assert result.profiles is None
    assert expected in {item.code.value for item in result.diagnostics}


def test_selector_compilation_failure_is_reported_without_authored_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_selector(*_args: object, **_kwargs: object) -> None:
        raise InvalidSelectorError(SelectorCompileError(alias="bounded-alias"))

    monkeypatch.setattr(profile_module, "compile_selector", reject_selector)

    result = _bind_specs(redaction="{rules: [{selector: response.status}]}")

    assert result.profiles is None
    diagnostic = result.diagnostics[0]
    assert diagnostic.code.value == "runtime.profile.selector-invalid"
    assert b"bounded-alias" not in msgspec.json.encode(diagnostic)


def test_valid_profile_defaults_and_strictest_intersections_are_composed() -> None:
    result = _bind_specs(
        redaction="{rules: [{selector: response.status}]} ",
        artifact=(
            "{capture: {requests: false, responses: false, bodies: false, evidence: false}, "
            "storage: {persist: on-gate-failure, unsafeSource: reject-artifact}, retention: {}}"
        ),
        safety=(
            "{classification: {responseBody: public, responseHeaders: [X-Private, x-private]}, "
            "schemas: {allowAny: true}}"
        ),
    )

    assert result.diagnostics == ()
    assert result.profiles is not None
    assert result.profiles.redaction.rules[0].action is RedactionAction.MASK
    assert result.profiles.redaction.rules[0].rule_id.endswith("/rule-1")
    assert result.profiles.artifact.persist is ArtifactPersistMode.ON_GATE_FAILURE
    assert result.profiles.artifact.unsafe_source is UnsafeSourceBehavior.REJECT_ARTIFACT
    assert not result.profiles.artifact.capture.requests
    assert not result.profiles.artifact.capture.responses
    assert not result.profiles.artifact.capture.bodies
    assert not result.profiles.artifact.capture.evidence
    assert result.profiles.remote_schemas.allow_any
    assert (
        result.profiles.classification_for(result.profiles.responses[0].environment)
        == result.profiles.responses[0].classification
    )
    assert tuple(
        header.value for header in result.profiles.responses[0].classification.sensitive_headers
    ) == ("x-private",)


def test_schema_restriction_intersection_and_private_helpers_cover_closed_carriers() -> None:
    first = RemoteSchemaRestriction(
        sources=(
            "https://schemas.example.test/a",
            "https://schemas.example.test/b",
        ),
        allow_any=False,
    )
    second = RemoteSchemaRestriction(
        sources=(
            "https://schemas.example.test/b",
            "https://schemas.example.test/c",
        ),
        allow_any=False,
    )
    assert profile_module._intersect_schema_restrictions(first, RemoteSchemaRestriction()) is first
    assert profile_module._intersect_schema_restrictions(first, second) == RemoteSchemaRestriction(
        sources=("https://schemas.example.test/b",),
        allow_any=False,
    )
    assert not profile_module._safe_schema_uri("https://schemas.example.test:bad/a")
    assert profile_module._literal_texts(cast("ValueExpression", object())) is None


def test_profile_graph_helpers_cover_inline_and_exact_value_carriers() -> None:
    prepared = _prepared(
        document("RedactionProfile", "safe", "{}"),
        document("ArtifactProfile", "ci", "{}"),
        document("SafetyProfile", "private", "{}"),
    )
    project = prepared.project
    safety = project.find(ResourceKind.SAFETY_PROFILE, ResourceId(value="private"))
    assert safety is not None
    environment = prepared.effective.environments[0]
    environment_resource = project.find(
        environment.identity.kind,
        environment.identity.resource_id,
    )
    assert environment_resource is not None

    inline = InlineResource(
        definition=InlineDefinitionRef(
            kind=ResourceKind.SAFETY_PROFILE,
            origin=InlineDefinitionOrigin(
                owner=environment.identity,
                fragment=InlineFragment(value="#safety"),
            ),
            fingerprint=safety.definition.fingerprint,
        ),
        spec=CanonicalObject(members=()),
    )
    nested = CanonicalObject(
        members=(
            CanonicalMember(
                name="items",
                value=CanonicalArray(items=(inline,)),
            ),
        ),
    )
    project_with_inline = msgspec.structs.replace(
        project,
        project=msgspec.structs.replace(project.project, spec=nested),
    )
    assert tuple(profile_module._walk_inline(nested)) == (inline,)
    assert tuple(profile_module._inline_resources(project_with_inline)) == (inline,)
    problems: list[Diagnostic] = []
    assert profile_module._profile(project_with_inline, inline.definition, problems) is inline

    absent_inline = msgspec.structs.replace(
        inline.definition,
        origin=msgspec.structs.replace(
            inline.definition.origin,
            fragment=InlineFragment(value="#absent"),
        ),
    )
    assert profile_module._profile(project_with_inline, absent_inline, problems) is None
    absent_named = msgspec.structs.replace(
        safety.definition,
        identity=msgspec.structs.replace(
            safety.definition.identity,
            resource_id=ResourceId(value="absent"),
        ),
    )
    assert profile_module._profile(project_with_inline, absent_named, problems) is None
    assert len(problems) == 2
    assert (
        profile_module._profile(
            project_with_inline,
            cast("DefinitionHandle", inline),
            problems,
        )
        is None
    )

    environment_with_inline = msgspec.structs.replace(
        environment_resource,
        spec=CanonicalObject(members=(CanonicalMember(name="safety", value=inline),)),
    )
    resources = tuple(
        environment_with_inline if item.definition == environment_resource.definition else item
        for item in project.resources
    )
    attached_project = msgspec.structs.replace(project, resources=resources)
    assert (
        profile_module._attached_safety_handle(attached_project, environment) == inline.definition
    )
    malformed_environment = msgspec.structs.replace(
        environment_resource,
        spec=JsonString(value="malformed"),
    )
    malformed_resources = tuple(
        malformed_environment if item.definition == environment_resource.definition else item
        for item in project.resources
    )
    assert (
        profile_module._attached_safety_handle(
            msgspec.structs.replace(project, resources=malformed_resources),
            environment,
        )
        is None
    )

    provenance = profile_module._STATIC_PROFILE_PROVENANCE
    exact_object = JsonObject(
        members=(JsonObjectMember(name="enabled", value=JsonBoolean(value=True)),)
    )
    literal_object = LiteralExpr(value=exact_object, provenance=provenance)
    expression_object = ObjectExpr(members=(), provenance=provenance)
    assert profile_module._object_fields(expression_object) == {}
    assert set(profile_module._object_fields(literal_object) or ()) == {"enabled"}
    assert set(profile_module._object_fields(exact_object) or ()) == {"enabled"}
    assert profile_module._empty_object(ObjectExpr(members=(), provenance=provenance))
    assert profile_module._empty_object(
        LiteralExpr(value=JsonObject(members=()), provenance=provenance)
    )
    assert profile_module._empty_object(JsonObject(members=()))

    exact_array = JsonArray(items=(JsonString(value="one"),))
    literal_array = LiteralExpr(value=exact_array, provenance=provenance)
    expression_array = ArrayExpr(
        items=(LiteralExpr(value=JsonString(value="one"), provenance=provenance),),
        provenance=provenance,
    )
    assert len(profile_module._array_expressions(expression_array) or ()) == 1
    assert len(profile_module._array_expressions(literal_array) or ()) == 1
    assert len(profile_module._array_expressions(exact_array) or ()) == 1
    assert profile_module._literal_texts(literal_array) == ("one",)

    bad_spec = msgspec.structs.replace(safety, spec=JsonString(value="bad"))
    with pytest.raises(TypeError, match="CanonicalObject"):
        profile_module._spec(bad_spec)
    inline_problem = profile_module._problem(profile_module._PROFILE_SHAPE, inline, "schemas")
    assert inline_problem.context.resource == environment.identity

    omitted = _bind_specs(artifact="{storage: {unsafeSource: omit}}")
    assert omitted.profiles is not None
    assert omitted.profiles.artifact.unsafe_source is UnsafeSourceBehavior.OMIT
