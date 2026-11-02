"""Tests for secret-free application inspection products."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.application.inspection import inspect_configuration_diagnostic
from comparo.application.inspection import inspect_diagnostic
from comparo.application.inspection import inspect_plan
from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import SourceSpan
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticContext
from comparo.core.domain.diagnostics import DiagnosticPathSegment
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.diagnostics import IndexPathSegment
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.primitives import Count
from comparo.core.domain.provenance import SourceLocation
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.planner import plan_run
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _prepared(*, method: str = "get") -> PreparedRun:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/ping}], "
            "baseline: {$use: staging}, candidates: [{$use: prod}], "
            "assertions: [{$use: usable}], safety: {$use: protected}}}",
        ),
        document(
            "Environment",
            "staging",
            "{baseUrl: 'https://staging.example', secrets: {API_TOKEN: {$env: CHAT_TOKEN}, "
            "UNUSED: UNUSED-CANARY}}",
        ),
        document(
            "Environment",
            "prod",
            "{baseUrl: 'https://prod.example', secrets: {API_TOKEN: {$env: CHAT_TOKEN}, "
            "UNUSED: UNUSED-CANARY}}",
        ),
        document("AssertionProfile", "usable", "{rules: [{operator: equals}]}"),
        document("DiffProfile", "stable", "{rules: [{comparator: exact}]}"),
        document("SafetyProfile", "protected", "{budgets: {trials: 10}}"),
        document(
            "Request",
            "ping",
            f"{{request: {{method: {method}, endpoint: '/private?api_key=PLAN-CANARY', "
            "headers: {Authorization: '${secrets.API_TOKEN}'}}, "
            "diffs: [{$use: stable}]}",
        ),
    )
    result = plan_run(project, invocation(), authority())
    assert result.prepared is not None
    return result.prepared


def test_plan_inspection_retains_compact_safe_structure() -> None:
    inspection = inspect_plan(_prepared())

    assert inspection.api_version == "comparo.plan/v1alpha1"
    assert len(inspection.slices) == 1
    assert inspection.cardinality.cells == 1
    assert inspection.cardinality.environment_executions == 2
    assert inspection.cardinality.comparisons == 1
    assert inspection.slices[0].subject.resource_id == "ping"
    assert tuple(item.resource_id for item in inspection.slices[0].environments) == (
        "staging",
        "prod",
    )
    assert inspection.slices[0].comparisons[0].baseline.resource_id == "staging"
    assert inspection.slices[0].comparisons[0].candidate.resource_id == "prod"
    assert inspection.slices[0].method == "GET"
    assert inspection.slices[0].unsafe_method is False
    assert inspection.slices[0].profiles.assertions[0].resource_id == "usable"
    assert inspection.slices[0].profiles.assertions[0].kind == "AssertionProfile"
    assert inspection.slices[0].profiles.diffs[0].resource_id == "stable"
    assert inspection.slices[0].profiles.safety[0].resource_id == "protected"
    assert inspection.slices[0].profiles.safety[0].fingerprint
    assert inspection.sensitive_sources[0].name == "API_TOKEN"
    assert inspection.sensitive_sources[0].mechanisms == ("$env:CHAT_TOKEN",)
    assert all(item.name != "UNUSED" for item in inspection.sensitive_sources)
    assert b"PLAN-CANARY" not in msgspec.json.encode(inspection)
    assert b"UNUSED-CANARY" not in msgspec.json.encode(inspection)
    assert inspection.policy.kind == "ExecutionPolicy"
    assert inspection.budgets
    assert msgspec.json.decode(msgspec.json.encode(inspection))["api_version"] == (
        "comparo.plan/v1alpha1"
    )


def test_plan_inspection_canonicalizes_and_classifies_custom_unsafe_methods() -> None:
    inspection = inspect_plan(_prepared(method="purge"))

    assert inspection.slices[0].method == "PURGE"
    assert inspection.slices[0].unsafe_method is True


def test_configuration_diagnostic_inspection_uses_only_safe_coordinates() -> None:
    parsed = parse_yaml_source(
        source_id=_prepared().project.project.location.source_id,
        text="broken: [\n",
    )
    diagnostic = parsed.diagnostics[0]
    inspected = inspect_configuration_diagnostic(diagnostic)

    assert inspected.code == diagnostic.code.value
    assert inspected.stage == "configuration"
    assert inspected.source is not None
    assert inspected.source.source == diagnostic.primary.start.source_id.value

    changed = msgspec.structs.replace(
        diagnostic,
        path=(FieldPathSegment(name="spec"), IndexPathSegment(index=Count(value=2))),
    )
    assert inspect_configuration_diagnostic(changed).path == ("spec", "2")


def test_domain_diagnostic_inspection_normalizes_closed_context() -> None:
    prepared = _prepared()
    identity: ResourceIdentity = prepared.project.project.definition.identity
    location: SourceLocation = prepared.project.project.location
    diagnostic = Diagnostic(
        code=DiagnosticCode(value="planning.test.failure"),
        severity=DiagnosticSeverity.WARNING,
        stage=DiagnosticStage.PLANNING,
        context=DiagnosticContext(
            resource=identity,
            location=location,
            field_path=(FieldPathSegment(name="subjects"),),
            occurrences=Count(value=3),
        ),
    )

    inspected = inspect_diagnostic(diagnostic)

    assert inspected.resource == ("local", "Project", "demo")
    assert inspected.source is not None
    assert inspected.path == ("subjects",)
    assert inspected.occurrences == 3


def test_inspection_rejects_untyped_inputs_and_path_segments() -> None:
    prepared = _prepared()
    with pytest.raises(TypeError, match="PreparedRun"):
        inspect_plan(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ConfigurationDiagnostic"):
        inspect_configuration_diagnostic(cast("ConfigurationDiagnostic", object()))
    with pytest.raises(TypeError, match="Diagnostic"):
        inspect_diagnostic(cast("Diagnostic", object()))

    diagnostic = ConfigurationDiagnostic(
        code=DiagnosticCode(value="configuration.test.failure"),
        severity=DiagnosticSeverity.ERROR,
        primary=SourceSpan(
            start=prepared.project.project.location,
            end=prepared.project.project.location,
        ),
        path=cast("tuple[DiagnosticPathSegment, ...]", (object(),)),
    )
    with pytest.raises(TypeError, match="path segment"):
        inspect_configuration_diagnostic(diagnostic)
