"""Tests for secret-safe structured diagnostics."""

import pytest

from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticContext
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.diagnostics import IndexPathSegment
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import SourceLocation


def _resource() -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.REQUEST,
        resource_id=ResourceId(value="health"),
    )


def _location() -> SourceLocation:
    return SourceLocation(
        source_id=SourceId(value="project-config"),
        document=PositiveCount(value=1),
        line=PositiveCount(value=10),
        column=PositiveCount(value=4),
    )


def test_diagnostic_codes_are_stable_namespaced_tokens() -> None:
    assert DiagnosticCode(value="policy.capability-denied").value == "policy.capability-denied"
    for value in ("freeform", "Policy.denied", "policy denied", 1):
        with pytest.raises(ValueError, match="namespaced token"):
            DiagnosticCode(value=value)  # type: ignore[arg-type]


def test_diagnostic_path_is_structural_and_typed() -> None:
    field = FieldPathSegment(name="responseSchema")
    index = IndexPathSegment(index=Count(value=2))
    assert (field.name, index.index.value) == ("responseSchema", 2)
    with pytest.raises(ValueError, match="static field"):
        FieldPathSegment(name="response.secret value")
    with pytest.raises(TypeError, match="Count"):
        IndexPathSegment(index=2)  # type: ignore[arg-type]


def test_diagnostic_context_contains_only_closed_safe_metadata() -> None:
    context = DiagnosticContext(
        resource=_resource(),
        location=_location(),
        field_path=(FieldPathSegment(name="response"), IndexPathSegment(index=Count(value=0))),
        occurrences=Count(value=3),
    )
    assert context.occurrences == Count(value=3)
    with pytest.raises(TypeError, match="resource"):
        DiagnosticContext(resource="Request/health")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="location"):
        DiagnosticContext(location="line 10")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable tuple"):
        DiagnosticContext(field_path=[FieldPathSegment(name="response")])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="unsupported segment"):
        DiagnosticContext(field_path=("response",))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="occurrences"):
        DiagnosticContext(occurrences=3)  # type: ignore[arg-type]


def test_diagnostic_has_no_arbitrary_payload_message_slot() -> None:
    diagnostic = Diagnostic(
        code=DiagnosticCode(value="budget.plan-exceeded"),
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.PLANNING,
    )
    assert diagnostic.context == DiagnosticContext()
    assert not hasattr(diagnostic, "message")


def test_diagnostic_validates_all_typed_components() -> None:
    code = DiagnosticCode(value="policy.capability-denied")
    with pytest.raises(TypeError, match="code"):
        Diagnostic(
            code="policy.capability-denied",  # type: ignore[arg-type]
            severity=DiagnosticSeverity.ERROR,
            stage=DiagnosticStage.AUTHORIZATION,
        )
    with pytest.raises(TypeError, match="severity"):
        Diagnostic(
            code=code,
            severity="error",  # type: ignore[arg-type]
            stage=DiagnosticStage.AUTHORIZATION,
        )
    with pytest.raises(TypeError, match="stage"):
        Diagnostic(
            code=code,
            severity=DiagnosticSeverity.ERROR,
            stage="authorization",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="context"):
        Diagnostic(
            code=code,
            severity=DiagnosticSeverity.ERROR,
            stage=DiagnosticStage.AUTHORIZATION,
            context="unsafe message",  # type: ignore[arg-type]
        )


def test_diagnostic_values_are_immutable() -> None:
    context = DiagnosticContext()
    with pytest.raises(AttributeError):
        context.field_path = ()  # type: ignore[misc]
