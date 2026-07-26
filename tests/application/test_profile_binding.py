"""Project profile binding and caller-authority composition tests."""

from __future__ import annotations

from comparo.application.production_execution import persisted_production_execution_defaults_v1
from comparo.application.profile_binding import bind_execution_profiles
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.policy import RedactionAction
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.planning import plan_run
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
