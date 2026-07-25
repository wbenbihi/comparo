"""Pure fail-closed admission for the production runtime's implemented configuration subset."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.application.preflight import PreparedProjectPlan
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticContext
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count
from comparo.core.domain.values import JsonNull
from comparo.core.planning._canonical import object_member
from comparo.core.planning._canonical import resource_spec
from comparo.core.planning.models import EffectiveSubject

_PLAN_INCONSISTENT = DiagnosticCode(value="runtime.capability.plan-inconsistent")
_SCENARIO = DiagnosticCode(value="runtime.capability.scenario-unsupported")
_ENVIRONMENT_OVERLAY = DiagnosticCode(value="runtime.capability.environment-overlay-unsupported")
_REQUEST_FIELD = DiagnosticCode(value="runtime.capability.request-field-unsupported")
_REQUEST_BODY = DiagnosticCode(value="runtime.capability.request-body-unsupported")
_RESPONSE_SCHEMA = DiagnosticCode(value="runtime.capability.response-schema-unsupported")
_SAFETY_PROFILE = DiagnosticCode(value="runtime.capability.safety-profile-unsupported")
_REDACTION_PROFILE = DiagnosticCode(value="runtime.capability.redaction-profile-unsupported")
_ARTIFACT_PROFILE = DiagnosticCode(value="runtime.capability.artifact-profile-unsupported")

_REQUEST_RESOURCE_FIELDS = frozenset(
    {
        "parameters",
        "request",
        "matrices",
        "responseSchema",
        "assertions",
        "diffs",
        "safety",
        "redaction",
        "artifacts",
    }
)
_REQUEST_WIRE_FIELDS = frozenset({"method", "endpoint", "url", "headers", "query", "body"})
_ENVIRONMENT_SUPPORTED = frozenset({"baseUrl", "env", "variables", "secrets"})


@final
class RuntimeCapabilityPreflightResult(msgspec.Struct, frozen=True, kw_only=True):
    """All value-free capability diagnostics found in one effect-free plan scan."""

    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require an immutable closed diagnostic collection."""
        if type(self.diagnostics) is not tuple or any(
            type(item) is not Diagnostic for item in self.diagnostics
        ):
            raise TypeError("runtime capability diagnostics must be immutable and typed")

    @property
    def supported(self) -> bool:
        """Return whether the complete selected plan is admitted by this runtime."""
        return not self.diagnostics


def preflight_runtime_capabilities(
    project: PreparedProjectPlan,
) -> RuntimeCapabilityPreflightResult:
    """Reject every selected configuration capability the production runtime cannot honor.

    The scan reads only the admitted canonical graph and effective structural bindings. It never
    resolves an expression, opens a file, consults ambient state, or invokes a network adapter.
    """
    if type(project) is not PreparedProjectPlan:
        raise TypeError("runtime capability preflight requires PreparedProjectPlan")
    diagnostics: list[Diagnostic] = []
    for subject in project.prepared.effective.subjects:
        resource = _selected_resource(project, subject.definition)
        if resource is None:
            diagnostics.append(_inconsistent(subject.definition))
        elif subject.definition.identity.kind is ResourceKind.SCENARIO:
            diagnostics.append(_diagnostic(_SCENARIO, resource))
        else:
            _request_capabilities(resource, diagnostics)
        _profile_capabilities(subject, resource, diagnostics)
    for definition in project.prepared.effective.environments:
        resource = _selected_resource(project, definition)
        if resource is None:
            diagnostics.append(_inconsistent(definition))
        else:
            _environment_capabilities(resource, diagnostics)
    return RuntimeCapabilityPreflightResult(diagnostics=tuple(diagnostics))


def _selected_resource(
    project: PreparedProjectPlan,
    definition: DefinitionRef,
) -> CompiledResource | None:
    found = project.prepared.project.find(
        definition.identity.kind,
        definition.identity.resource_id,
    )
    return found if found is not None and found.definition == definition else None


def _request_capabilities(
    resource: CompiledResource,
    diagnostics: list[Diagnostic],
) -> None:
    spec = resource_spec(resource)
    diagnostics.extend(
        _diagnostic(_REQUEST_FIELD, resource, member.name)
        for member in spec.members
        if member.name not in _REQUEST_RESOURCE_FIELDS
    )
    if object_member(spec, "responseSchema") is not None:
        diagnostics.append(_diagnostic(_RESPONSE_SCHEMA, resource, "responseSchema"))
    request = object_member(spec, "request")
    if type(request) is not ObjectExpr:
        diagnostics.append(_diagnostic(_PLAN_INCONSISTENT, resource, "request"))
        return
    for member in request.members:
        if member.name == "body":
            if not _is_exact_null(member.expression):
                diagnostics.append(_diagnostic(_REQUEST_BODY, resource, "request", "body"))
        elif member.name not in _REQUEST_WIRE_FIELDS:
            diagnostics.append(_diagnostic(_REQUEST_FIELD, resource, "request", member.name))


def _environment_capabilities(
    resource: CompiledResource,
    diagnostics: list[Diagnostic],
) -> None:
    spec = resource_spec(resource)
    for member in spec.members:
        if member.name not in _ENVIRONMENT_SUPPORTED:
            code = {
                "safety": _SAFETY_PROFILE,
                "redaction": _REDACTION_PROFILE,
                "artifacts": _ARTIFACT_PROFILE,
            }.get(member.name, _ENVIRONMENT_OVERLAY)
            diagnostics.append(_diagnostic(code, resource, member.name))


def _profile_capabilities(
    subject: EffectiveSubject,
    resource: CompiledResource | None,
    diagnostics: list[Diagnostic],
) -> None:
    profiles = subject.profiles
    occurrences = (
        (_SAFETY_PROFILE, "safety", len(profiles.safety)),
        (_REDACTION_PROFILE, "redaction", int(profiles.redaction is not None)),
        (_ARTIFACT_PROFILE, "artifacts", int(profiles.artifacts is not None)),
    )
    for code, field, count in occurrences:
        if count:
            diagnostics.append(
                _diagnostic_for_definition(
                    code,
                    subject.definition,
                    resource,
                    field,
                    occurrences=count,
                )
            )


def _is_exact_null(expression: object) -> bool:
    return type(expression) is LiteralExpr and type(expression.value) is JsonNull


def _inconsistent(definition: DefinitionRef) -> Diagnostic:
    return _diagnostic_for_definition(_PLAN_INCONSISTENT, definition, None)


def _diagnostic(
    code: DiagnosticCode,
    resource: CompiledResource,
    *path: str,
) -> Diagnostic:
    return _diagnostic_for_definition(
        code,
        resource.definition,
        resource,
        *path,
    )


def _diagnostic_for_definition(
    code: DiagnosticCode,
    definition: DefinitionRef,
    resource: CompiledResource | None,
    *path: str,
    occurrences: int | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.COMPILATION,
        context=DiagnosticContext(
            resource=definition.identity,
            location=None if resource is None else resource.location,
            field_path=tuple(FieldPathSegment(name=item) for item in path),
            occurrences=None if occurrences is None else Count(value=occurrences),
        ),
    )


__all__ = (
    "RuntimeCapabilityPreflightResult",
    "preflight_runtime_capabilities",
)
