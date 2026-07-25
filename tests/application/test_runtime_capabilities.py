"""Effect-free production runtime-capability preflight tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.application.preflight import PreparedProjectPlan
from comparo.application.runtime_capabilities import RuntimeCapabilityPreflightResult
from comparo.application.runtime_capabilities import preflight_runtime_capabilities
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.values import JsonString
from comparo.core.planning import plan_run
from comparo.core.planning._canonical import object_member
from comparo.core.planning._canonical import resource_spec
from comparo.core.policy import planning_authority
from comparo.core.policy import trusted_local_policy
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _prepared_project(
    *,
    request: str = "{request: {method: GET, endpoint: /check}}",
    staging: str = "{baseUrl: 'https://staging.example'}",
    defaults: str = "",
    resources: tuple[str, ...] = (),
) -> PreparedProjectPlan:
    compiled = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            f"candidates: [{{$use: prod}}]{defaults}}}}}",
        ),
        document("Environment", "staging", staging),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document("Request", "check", request),
        *resources,
    )
    policy = trusted_local_policy()
    planned = plan_run(
        compiled,
        invocation(),
        planning_authority(policy, project_namespace=compiled.namespace),
    )
    assert planned.prepared is not None
    return PreparedProjectPlan(
        prepared=planned.prepared,
        policy=policy,
        project_root=Path.cwd(),
    )


def _codes(result: RuntimeCapabilityPreflightResult) -> tuple[str, ...]:
    return tuple(item.code.value for item in result.diagnostics)


def _paths(result: RuntimeCapabilityPreflightResult) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(cast("FieldPathSegment", segment).name for segment in item.context.field_path)
        for item in result.diagnostics
    )


def test_supported_body_free_subset_is_admitted_without_effects() -> None:
    project = _prepared_project(
        request=(
            "{request: {method: POST, endpoint: /check, query: {locale: en}, "
            "headers: {X-Test: ok}, body: null}}"
        ),
        staging=(
            "{baseUrl: 'https://staging.example', env: {values: {PORT: '443'}}, "
            "variables: {region: staging}, secrets: {token: hidden}}"
        ),
    )

    result = preflight_runtime_capabilities(project)

    assert result.supported
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    "field",
    ["headers", "cookies", "auth", "timeout", "tls", "proxy"],
)
def test_every_environment_request_overlay_is_rejected(field: str) -> None:
    values = {
        "headers": "{X-Test: value}",
        "cookies": "{session: value}",
        "auth": "{type: bearer, value: token}",
        "timeout": "{total: 1s}",
        "tls": "{verify: true}",
        "proxy": "{url: 'https://proxy.example'}",
    }
    result = preflight_runtime_capabilities(
        _prepared_project(
            staging=f"{{baseUrl: 'https://staging.example', {field}: {values[field]}}}"
        )
    )

    assert not result.supported
    assert _codes(result) == ("runtime.capability.environment-overlay-unsupported",)
    assert _paths(result) == ((field,),)
    assert result.diagnostics[0].context.resource is not None
    assert result.diagnostics[0].context.resource.kind is ResourceKind.ENVIRONMENT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cookies", "{session: value}"),
        ("auth", "{type: bearer, value: token}"),
        ("bodyType", "json"),
        ("stream", "{protocol: auto}"),
        ("timeout", "{total: 1s}"),
        ("redirects", "{maximum: 1}"),
        ("retries", "{maximumAttempts: 2}"),
    ],
)
def test_every_ignored_request_wire_field_is_rejected(field: str, value: str) -> None:
    result = preflight_runtime_capabilities(
        _prepared_project(request=f"{{request: {{method: GET, endpoint: /, {field}: {value}}}}}")
    )

    assert _codes(result) == ("runtime.capability.request-field-unsupported",)
    assert _paths(result) == (("request", field),)


@pytest.mark.parametrize(
    "body",
    ["hello", "{$env: BODY}"],
)
def test_non_null_or_resolvable_request_bodies_are_rejected(body: str) -> None:
    result = preflight_runtime_capabilities(
        _prepared_project(request=f"{{request: {{method: POST, endpoint: /, body: {body}}}}}")
    )

    assert _codes(result) == ("runtime.capability.request-body-unsupported",)
    assert _paths(result) == (("request", "body"),)
    assert body not in msgspec.json.encode(result).decode()


def test_response_schema_is_rejected_without_loading_remote_content() -> None:
    result = preflight_runtime_capabilities(
        _prepared_project(
            request=(
                "{request: {method: GET, endpoint: /}, "
                "responseSchema: {$use: 'https://schemas.example.test/response.json'}}"
            )
        )
    )

    assert _codes(result) == ("runtime.capability.response-schema-unsupported",)
    assert _paths(result) == (("responseSchema",),)


@pytest.mark.parametrize(
    ("field", "kind", "code"),
    [
        ("safety", "SafetyProfile", "runtime.capability.safety-profile-unsupported"),
        (
            "redaction",
            "RedactionProfile",
            "runtime.capability.redaction-profile-unsupported",
        ),
        (
            "artifacts",
            "ArtifactProfile",
            "runtime.capability.artifact-profile-unsupported",
        ),
    ],
)
def test_effective_profiles_are_rejected_before_runtime(
    field: str,
    kind: str,
    code: str,
) -> None:
    result = preflight_runtime_capabilities(
        _prepared_project(
            defaults=f", {field}: {{$use: profile}}",
            resources=(document(kind, "profile", "{}"),),
        )
    )

    assert _codes(result) == (code,)
    assert _paths(result) == ((field,),)
    assert result.diagnostics[0].context.occurrences is not None
    assert result.diagnostics[0].context.occurrences.value == 1


@pytest.mark.parametrize(
    ("field", "kind", "code"),
    [
        ("safety", "SafetyProfile", "runtime.capability.safety-profile-unsupported"),
        (
            "redaction",
            "RedactionProfile",
            "runtime.capability.redaction-profile-unsupported",
        ),
        (
            "artifacts",
            "ArtifactProfile",
            "runtime.capability.artifact-profile-unsupported",
        ),
    ],
)
def test_selected_environment_profiles_cannot_be_silently_ignored(
    field: str,
    kind: str,
    code: str,
) -> None:
    result = preflight_runtime_capabilities(
        _prepared_project(
            staging=f"{{baseUrl: 'https://staging.example', {field}: {{$use: profile}}}}",
            resources=(document(kind, "profile", "{}"),),
        )
    )

    assert code in _codes(result)
    assert any(
        item.context.resource is not None
        and item.context.resource.kind is ResourceKind.ENVIRONMENT
        and tuple(cast("FieldPathSegment", segment).name for segment in item.context.field_path)
        == (field,)
        for item in result.diagnostics
    )


def test_selected_scenario_is_rejected_defensively() -> None:
    project = _prepared_project(resources=(document("Scenario", "flow", "{steps: []}"),))
    scenario = next(
        item.definition
        for item in project.prepared.project.resources
        if item.definition.identity.kind is ResourceKind.SCENARIO
    )
    subject = msgspec.structs.replace(project.prepared.effective.subjects[0], definition=scenario)
    altered = msgspec.structs.replace(
        project,
        prepared=msgspec.structs.replace(
            project.prepared,
            effective=msgspec.structs.replace(project.prepared.effective, subjects=(subject,)),
        ),
    )

    result = preflight_runtime_capabilities(altered)

    assert _codes(result) == ("runtime.capability.scenario-unsupported",)


def test_inconsistent_selected_definitions_fail_closed() -> None:
    project = _prepared_project()
    subject = project.prepared.effective.subjects[0]
    missing_subject = msgspec.structs.replace(
        subject.definition,
        fingerprint=msgspec.structs.replace(
            subject.definition.fingerprint,
            digest=msgspec.structs.replace(
                subject.definition.fingerprint.digest,
                hex_value="0" * 64,
            ),
        ),
    )
    environment = project.prepared.effective.environments[0]
    missing_environment = msgspec.structs.replace(
        environment,
        fingerprint=msgspec.structs.replace(
            environment.fingerprint,
            digest=msgspec.structs.replace(environment.fingerprint.digest, hex_value="1" * 64),
        ),
    )
    altered_subject = msgspec.structs.replace(subject, definition=missing_subject)
    altered_effective = msgspec.structs.replace(
        project.prepared.effective,
        subjects=(altered_subject,),
        environments=(missing_environment, *project.prepared.effective.environments[1:]),
    )
    altered = msgspec.structs.replace(
        project,
        prepared=msgspec.structs.replace(project.prepared, effective=altered_effective),
    )

    result = preflight_runtime_capabilities(altered)

    assert _codes(result) == (
        "runtime.capability.plan-inconsistent",
        "runtime.capability.plan-inconsistent",
    )
    assert all(item.context.location is None for item in result.diagnostics)


def test_canonical_future_fields_and_invalid_request_shape_fail_closed() -> None:
    project = _prepared_project()
    request_definition = project.prepared.effective.subjects[0].definition
    request_resource = project.prepared.project.find(
        request_definition.identity.kind,
        request_definition.identity.resource_id,
    )
    assert request_resource is not None
    request_spec = resource_spec(request_resource)
    request_expression = object_member(request_spec, "request")
    assert type(request_expression) is ObjectExpr
    method = next(item.expression for item in request_expression.members if item.name == "method")
    assert type(method) is LiteralExpr
    invalid_request = msgspec.structs.replace(
        request_resource,
        spec=CanonicalObject(
            members=(
                *(
                    CanonicalMember(
                        name=item.name,
                        value=method if item.name == "request" else item.value,
                    )
                    for item in request_spec.members
                ),
                CanonicalMember(name="futureCapability", value=JsonString(value="enabled")),
            )
        ),
    )
    staging = project.prepared.effective.environments[0]
    staging_resource = project.prepared.project.find(
        staging.identity.kind,
        staging.identity.resource_id,
    )
    assert staging_resource is not None
    staging_spec = resource_spec(staging_resource)
    invalid_environment = msgspec.structs.replace(
        staging_resource,
        spec=CanonicalObject(
            members=(
                *staging_spec.members,
                CanonicalMember(name="futureOverlay", value=JsonString(value="enabled")),
            )
        ),
    )
    resources = tuple(
        invalid_request
        if item.definition == request_resource.definition
        else invalid_environment
        if item.definition == staging_resource.definition
        else item
        for item in project.prepared.project.resources
    )
    compiled = msgspec.structs.replace(project.prepared.project, resources=resources)
    altered = msgspec.structs.replace(
        project,
        prepared=msgspec.structs.replace(project.prepared, project=compiled),
    )

    result = preflight_runtime_capabilities(altered)

    assert _codes(result) == (
        "runtime.capability.request-field-unsupported",
        "runtime.capability.plan-inconsistent",
        "runtime.capability.environment-overlay-unsupported",
    )


def test_public_contract_rejects_untyped_inputs_and_diagnostics() -> None:
    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        preflight_runtime_capabilities(cast("PreparedProjectPlan", object()))
    with pytest.raises(TypeError, match="diagnostics"):
        RuntimeCapabilityPreflightResult(diagnostics=cast("tuple[Diagnostic, ...]", []))
    with pytest.raises(TypeError, match="diagnostics"):
        RuntimeCapabilityPreflightResult(diagnostics=cast("tuple[Diagnostic, ...]", (object(),)))
