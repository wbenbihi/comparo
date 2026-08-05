"""Pure binding of project safety, redaction, and artifact profile requests."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import final
from urllib.parse import urlsplit

import msgspec

from comparo.adapters.http import ResponseClassification
from comparo.application.evaluation_selectors import InvalidSelectorError
from comparo.application.evaluation_selectors import compile_selector
from comparo.application.profile_capabilities import ARTIFACT_PROFILE_ADMITTED_FIELDS
from comparo.application.profile_capabilities import REDACTION_PROFILE_ADMITTED_FIELDS
from comparo.application.profile_capabilities import SAFETY_PROFILE_ADMITTED_FIELDS
from comparo.core.artifacts.policy import ArtifactCapturePolicy
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import RedactionAction
from comparo.core.artifacts.policy import RedactionPolicy
from comparo.core.artifacts.policy import RedactionRule
from comparo.core.artifacts.policy import UnsafeSourceBehavior
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticContext
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.planning._canonical import object_member
from comparo.core.planning.models import DefinitionHandle
from comparo.core.planning.models import PreparedRun

type ProfileResource = CompiledResource | InlineResource

_PROFILE_MISSING = DiagnosticCode(value="runtime.profile.definition-missing")
_PROFILE_SHAPE = DiagnosticCode(value="runtime.profile.shape-invalid")
_PROFILE_FIELD = DiagnosticCode(value="runtime.profile.field-unsupported")
_PROFILE_SELECTOR = DiagnosticCode(value="runtime.profile.selector-invalid")

_STATIC_PROFILE_PROVENANCE = ValueProvenance(
    entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),)
)


@final
class EnvironmentResponseClassification(msgspec.Struct, frozen=True, kw_only=True):
    """One Environment-specific effective response classification."""

    environment: DefinitionRef
    classification: ResponseClassification

    def __post_init__(self) -> None:
        """Require one selected Environment and a typed classification."""
        if (
            type(self.environment) is not DefinitionRef
            or self.environment.identity.kind is not ResourceKind.ENVIRONMENT
        ):
            raise TypeError("response classification requires an Environment definition")
        if type(self.classification) is not ResponseClassification:
            raise TypeError("response classification must be typed")


@final
class RemoteSchemaRestriction(msgspec.Struct, frozen=True, kw_only=True):
    """Project-requested remote Schema restriction intersected across SafetyProfiles."""

    sources: tuple[str, ...] = ()
    allow_any: bool = True

    def __post_init__(self) -> None:
        """Require canonical exact HTTP(S) sources or one unambiguous wildcard."""
        if type(self.sources) is not tuple or any(
            type(source) is not str or not _safe_schema_uri(source) for source in self.sources
        ):
            raise TypeError("remote Schema restrictions require exact safe HTTP(S) sources")
        if self.sources != tuple(sorted(set(self.sources))):
            raise ValueError("remote Schema restriction sources must be unique and canonical")
        if type(self.allow_any) is not bool:
            raise TypeError("remote Schema allowAny must be bool")
        if self.allow_any and self.sources:
            raise ValueError("remote Schema allowAny cannot retain redundant sources")

    def allows(self, uri: str) -> bool:
        """Return whether this project restriction admits one exact source URI."""
        if type(uri) is not str:
            raise TypeError("remote Schema restriction lookup requires a URI string")
        return self.allow_any or uri in self.sources


@final
class BoundExecutionProfiles(msgspec.Struct, frozen=True, kw_only=True):
    """Effective project restrictions ready for production composition."""

    artifact: ArtifactPolicy
    redaction: RedactionPolicy
    responses: tuple[EnvironmentResponseClassification, ...]
    remote_schemas: RemoteSchemaRestriction = RemoteSchemaRestriction()

    def __post_init__(self) -> None:
        """Require an immutable typed composition product."""
        if type(self.artifact) is not ArtifactPolicy or type(self.redaction) is not RedactionPolicy:
            raise TypeError("bound execution profiles require typed artifact policies")
        if type(self.responses) is not tuple or any(
            type(item) is not EnvironmentResponseClassification for item in self.responses
        ):
            raise TypeError("bound response classifications must be immutable and typed")
        if type(self.remote_schemas) is not RemoteSchemaRestriction:
            raise TypeError("bound remote Schema restrictions must be typed")

    def classification_for(self, environment: DefinitionRef) -> ResponseClassification:
        """Return the exact classification for one selected Environment."""
        if type(environment) is not DefinitionRef:
            raise TypeError("response classification lookup requires a DefinitionRef")
        found = next(
            (item.classification for item in self.responses if item.environment == environment),
            None,
        )
        if found is None:
            raise KeyError("response classification Environment is not selected")
        return found


@final
class ProfileBindingResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly a composed profile product or safe structural diagnostics."""

    profiles: BoundExecutionProfiles | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful binding and diagnostic rejection disjoint."""
        if self.profiles is not None and type(self.profiles) is not BoundExecutionProfiles:
            raise TypeError("profile binding product must be typed")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not Diagnostic for item in self.diagnostics
        ):
            raise TypeError("profile binding diagnostics must be immutable and typed")
        if (self.profiles is None) == bool(self.diagnostics):
            return
        raise ValueError("profile binding requires exactly profiles or diagnostics")


def bind_execution_profiles(
    prepared: PreparedRun,
    *,
    artifact: ArtifactPolicy,
    response: ResponseClassification,
) -> ProfileBindingResult:
    """Compose project profiles as restrictions over caller-owned production controls."""
    if type(prepared) is not PreparedRun:
        raise TypeError("profile binding requires PreparedRun")
    if type(artifact) is not ArtifactPolicy or type(response) is not ResponseClassification:
        raise TypeError("profile binding requires typed caller-owned defaults")
    problems: list[Diagnostic] = []
    resources = _selected_profile_resources(prepared, problems)
    if problems:
        return ProfileBindingResult(diagnostics=tuple(problems))
    redaction = _redaction_policy(resources.redaction, problems)
    artifact_policy = _artifact_policy(resources.artifact, artifact, problems)
    responses = _response_classifications(prepared, resources.safety, response, problems)
    remote_schemas = _remote_schema_restriction(resources.safety, problems)
    if problems:
        return ProfileBindingResult(diagnostics=tuple(problems))
    return ProfileBindingResult(
        profiles=BoundExecutionProfiles(
            artifact=artifact_policy,
            redaction=redaction,
            responses=responses,
            remote_schemas=remote_schemas,
        )
    )


@final
class _SelectedProfiles(msgspec.Struct, frozen=True, kw_only=True):
    safety: tuple[tuple[DefinitionHandle, ProfileResource], ...]
    redaction: tuple[ProfileResource, ...]
    artifact: tuple[ProfileResource, ...]


def _selected_profile_resources(
    prepared: PreparedRun,
    problems: list[Diagnostic],
) -> _SelectedProfiles:
    safety_handles = tuple(
        dict.fromkeys(
            handle for subject in prepared.effective.subjects for handle in subject.profiles.safety
        )
    )
    redaction_handles = tuple(
        dict.fromkeys(
            subject.profiles.redaction
            for subject in prepared.effective.subjects
            if subject.profiles.redaction is not None
        )
    )
    artifact_handles = tuple(
        dict.fromkeys(
            subject.profiles.artifacts
            for subject in prepared.effective.subjects
            if subject.profiles.artifacts is not None
        )
    )
    safety = tuple(
        (handle, resource)
        for handle in safety_handles
        if (resource := _profile(prepared.project, handle, problems)) is not None
    )
    redaction = tuple(
        resource
        for handle in redaction_handles
        if (resource := _profile(prepared.project, handle, problems)) is not None
    )
    artifact = tuple(
        resource
        for handle in artifact_handles
        if (resource := _profile(prepared.project, handle, problems)) is not None
    )
    for _handle, profile in safety:
        _supported_fields(profile, SAFETY_PROFILE_ADMITTED_FIELDS, problems)
    for profile in redaction:
        _supported_fields(profile, REDACTION_PROFILE_ADMITTED_FIELDS, problems)
    for profile in artifact:
        _supported_fields(profile, ARTIFACT_PROFILE_ADMITTED_FIELDS, problems)
    return _SelectedProfiles(safety=safety, redaction=redaction, artifact=artifact)


def _supported_fields(
    profile: ProfileResource,
    allowed: frozenset[str],
    problems: list[Diagnostic],
) -> None:
    problems.extend(
        _problem(_PROFILE_FIELD, profile, member.name)
        for member in _spec(profile).members
        if member.name not in allowed
    )


def _redaction_policy(
    profiles: tuple[ProfileResource, ...],
    problems: list[Diagnostic],
) -> RedactionPolicy:
    rules: list[RedactionRule] = []
    for profile in profiles:
        backstop = object_member(_spec(profile), "backstop")
        if backstop is not None and _literal_bool(backstop) is None:
            problems.append(_problem(_PROFILE_SHAPE, profile, "backstop"))
        expression = object_member(_spec(profile), "rules")
        if expression is None:
            continue
        items = _array_expressions(expression)
        if items is None:
            problems.append(_problem(_PROFILE_SHAPE, profile, "rules"))
            continue
        for index, item in enumerate(items):
            compiled = _redaction_rule(profile, item, index, problems)
            if compiled is not None:
                rules.append(compiled)
    return RedactionPolicy(rules=tuple(rules))


def _redaction_rule(
    profile: ProfileResource,
    expression: ValueExpression,
    index: int,
    problems: list[Diagnostic],
) -> RedactionRule | None:
    if type(expression) is not ObjectExpr:
        problems.append(_problem(_PROFILE_SHAPE, profile, "rules"))
        return None
    fields = _fields(expression)
    if not set(fields) <= {"id", "selector", "action"} or "selector" not in fields:
        problems.append(_problem(_PROFILE_SHAPE, profile, "rules"))
        return None
    identity = _literal_text(fields.get("id")) or f"rule-{index + 1}"
    action_text = _literal_text(fields.get("action")) or RedactionAction.MASK.value
    try:
        action = RedactionAction(action_text)
        selector = compile_selector(
            fields["selector"],
            fingerprint=_selector_fingerprint(profile, index),
            default_body_view=BodyViewKind.JSON,
        )
        prefix = profile.definition.fingerprint.digest.hex_value[:12]
        return RedactionRule(
            rule_id=f"{prefix}/{identity}"[:63],
            selector=selector,
            action=action,
        )
    except InvalidSelectorError:
        problems.append(_problem(_PROFILE_SELECTOR, profile, "rules"))
    except (TypeError, ValueError):
        problems.append(_problem(_PROFILE_SHAPE, profile, "rules"))
    return None


def _artifact_policy(
    profiles: tuple[ProfileResource, ...],
    base: ArtifactPolicy,
    problems: list[Diagnostic],
) -> ArtifactPolicy:
    capture = base.capture
    persist = base.persist
    unsafe = base.unsafe_source
    for profile in profiles:
        capture = _capture_policy(profile, capture, problems)
        persist, unsafe = _storage_policy(profile, persist, unsafe, problems)
        retention = object_member(_spec(profile), "retention")
        if retention is not None and not _empty_object(retention):
            problems.append(_problem(_PROFILE_FIELD, profile, "retention"))
    return msgspec.structs.replace(
        base,
        capture=capture,
        persist=persist,
        unsafe_source=unsafe,
    )


def _capture_policy(
    profile: ProfileResource,
    base: ArtifactCapturePolicy,
    problems: list[Diagnostic],
) -> ArtifactCapturePolicy:
    value = object_member(_spec(profile), "capture")
    if value is None:
        return base
    fields = _object_fields(value)
    if fields is None or not set(fields) <= {"requests", "responses", "bodies", "evidence"}:
        problems.append(_problem(_PROFILE_SHAPE, profile, "capture"))
        return base
    resolved: dict[str, bool] = {}
    for name, expression in fields.items():
        flag = _literal_bool(expression)
        if flag is None:
            problems.append(_problem(_PROFILE_SHAPE, profile, "capture", name))
        else:
            resolved[name] = flag
    return ArtifactCapturePolicy(
        requests=base.requests and resolved.get("requests", True),
        responses=base.responses and resolved.get("responses", True),
        bodies=base.bodies and resolved.get("bodies", True),
        evidence=base.evidence and resolved.get("evidence", True),
    )


def _storage_policy(
    profile: ProfileResource,
    persist: ArtifactPersistMode,
    unsafe: UnsafeSourceBehavior,
    problems: list[Diagnostic],
) -> tuple[ArtifactPersistMode, UnsafeSourceBehavior]:
    value = object_member(_spec(profile), "storage")
    if value is None:
        return persist, unsafe
    fields = _object_fields(value)
    if fields is None or not set(fields) <= {"persist", "unsafeSource"}:
        problems.append(_problem(_PROFILE_SHAPE, profile, "storage"))
        return persist, unsafe
    requested_persist = _literal_text(fields.get("persist"))
    requested_unsafe = _literal_text(fields.get("unsafeSource"))
    try:
        if "persist" in fields:
            if requested_persist is None:
                raise ValueError
            candidate = ArtifactPersistMode(requested_persist)
            rank = {
                ArtifactPersistMode.NEVER: 0,
                ArtifactPersistMode.ON_GATE_FAILURE: 1,
                ArtifactPersistMode.ALWAYS: 2,
            }
            persist = min((persist, candidate), key=rank.__getitem__)
        if "unsafeSource" in fields:
            if requested_unsafe is None:
                raise ValueError
            candidate_unsafe = UnsafeSourceBehavior(requested_unsafe)
            if candidate_unsafe is UnsafeSourceBehavior.REJECT_ARTIFACT:
                unsafe = candidate_unsafe
    except ValueError:
        problems.append(_problem(_PROFILE_SHAPE, profile, "storage"))
    return persist, unsafe


def _response_classifications(
    prepared: PreparedRun,
    safety: tuple[tuple[DefinitionHandle, ProfileResource], ...],
    base: ResponseClassification,
    problems: list[Diagnostic],
) -> tuple[EnvironmentResponseClassification, ...]:
    environment_handles = {
        environment: _attached_safety_handle(prepared.project, environment)
        for environment in prepared.effective.environments
    }
    all_environment_handles = {
        handle for handle in environment_handles.values() if handle is not None
    }
    common = tuple(item for item in safety if item[0] not in all_environment_handles)
    by_handle = dict(safety)
    result: list[EnvironmentResponseClassification] = []
    for environment in prepared.effective.environments:
        selected = list(common)
        handle = environment_handles[environment]
        if handle is not None and handle in by_handle:
            selected.append((handle, by_handle[handle]))
        classification = base
        for _profile_handle, profile in selected:
            classification = _classification(profile, classification, problems)
        result.append(
            EnvironmentResponseClassification(
                environment=environment,
                classification=classification,
            )
        )
    return tuple(result)


def _remote_schema_restriction(
    safety: tuple[tuple[DefinitionHandle, ProfileResource], ...],
    problems: list[Diagnostic],
) -> RemoteSchemaRestriction:
    effective = RemoteSchemaRestriction()
    for _handle, profile in safety:
        value = object_member(_spec(profile), "schemas")
        if value is None:
            continue
        fields = _object_fields(value)
        if fields is None or not set(fields) <= {"allowAny", "sources"}:
            problems.append(_problem(_PROFILE_SHAPE, profile, "schemas"))
            continue
        allow_any = _literal_bool(fields.get("allowAny"))
        sources = _literal_texts(fields["sources"]) if "sources" in fields else ()
        if ("allowAny" in fields and allow_any is None) or sources is None:
            problems.append(_problem(_PROFILE_SHAPE, profile, "schemas"))
            continue
        requested_allow_any = allow_any is True
        try:
            requested = RemoteSchemaRestriction(
                sources=tuple(sorted(set(sources))),
                allow_any=requested_allow_any,
            )
        except (TypeError, ValueError):
            problems.append(_problem(_PROFILE_SHAPE, profile, "schemas"))
            continue
        effective = _intersect_schema_restrictions(effective, requested)
    return effective


def _intersect_schema_restrictions(
    left: RemoteSchemaRestriction,
    right: RemoteSchemaRestriction,
) -> RemoteSchemaRestriction:
    if left.allow_any:
        return right
    if right.allow_any:
        return left
    return RemoteSchemaRestriction(
        sources=tuple(sorted(set(left.sources).intersection(right.sources))),
        allow_any=False,
    )


def _safe_schema_uri(uri: str) -> bool:
    try:
        parsed = urlsplit(uri)
        _ = parsed.port
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _classification(
    profile: ProfileResource,
    base: ResponseClassification,
    problems: list[Diagnostic],
) -> ResponseClassification:
    value = object_member(_spec(profile), "classification")
    if value is None:
        return base
    fields = _object_fields(value)
    if fields is None or not set(fields) <= {"responseBody", "responseHeaders"}:
        problems.append(_problem(_PROFILE_SHAPE, profile, "classification"))
        return base
    body = base.body
    if "responseBody" in fields:
        text = _literal_text(fields["responseBody"])
        try:
            requested = Sensitivity(text) if text is not None else Sensitivity("")
        except ValueError:
            problems.append(_problem(_PROFILE_SHAPE, profile, "classification", "responseBody"))
        else:
            if requested is Sensitivity.SENSITIVE:
                body = requested
    headers = list(base.sensitive_headers)
    if "responseHeaders" in fields:
        values = _literal_texts(fields["responseHeaders"])
        if values is None:
            problems.append(_problem(_PROFILE_SHAPE, profile, "classification", "responseHeaders"))
        else:
            try:
                headers.extend(HeaderName(value=item.lower()) for item in values)
            except (TypeError, ValueError):
                problems.append(
                    _problem(_PROFILE_SHAPE, profile, "classification", "responseHeaders")
                )
    return ResponseClassification(body=body, sensitive_headers=tuple(dict.fromkeys(headers)))


def _attached_safety_handle(
    project: CompiledProject,
    environment: DefinitionRef,
) -> DefinitionHandle | None:
    resource = project.find(environment.identity.kind, environment.identity.resource_id)
    if (
        resource is None
        or resource.definition != environment
        or type(resource.spec) is not CanonicalObject
    ):
        return None
    value = object_member(resource.spec, "safety")
    if type(value) is InlineResource:
        return value.definition
    from comparo.core.configuration.canonical import LinkedResource

    return value.target if type(value) is LinkedResource else None


def _profile(
    project: CompiledProject,
    handle: DefinitionHandle,
    problems: list[Diagnostic],
) -> ProfileResource | None:
    if type(handle) is DefinitionRef:
        found_resource = project.find(handle.identity.kind, handle.identity.resource_id)
        if found_resource is not None and found_resource.definition == handle:
            return found_resource
    elif type(handle) is InlineDefinitionRef:
        found_inline = next(
            (item for item in _inline_resources(project) if item.definition == handle),
            None,
        )
        if found_inline is not None:
            return found_inline
    problems.append(_problem_for_handle(_PROFILE_MISSING, handle))
    return None


def _inline_resources(project: CompiledProject) -> Iterable[InlineResource]:
    for resource in (project.project, *project.resources):
        yield from _walk_inline(resource.spec)


def _walk_inline(value: CanonicalValue) -> Iterable[InlineResource]:
    if type(value) is InlineResource:
        yield value
        yield from _walk_inline(value.spec)
    elif type(value) is CanonicalArray:
        for item in value.items:
            yield from _walk_inline(item)
    elif type(value) is CanonicalObject:
        for member in value.members:
            yield from _walk_inline(member.value)


def _spec(profile: ProfileResource) -> CanonicalObject:
    value = profile.spec
    if type(value) is not CanonicalObject:
        raise TypeError("profile spec must be a CanonicalObject")
    return value


def _fields(value: ObjectExpr) -> dict[str, ValueExpression]:
    return {member.name: member.expression for member in value.members}


def _object_fields(value: CanonicalValue | None) -> dict[str, ValueExpression] | None:
    if type(value) is ObjectExpr:
        return _fields(value)
    if type(value) is LiteralExpr and type(value.value) is JsonObject:
        return {
            member.name: _json_expression(member.value, value) for member in value.value.members
        }
    if type(value) is JsonObject:
        return {member.name: _json_expression(member.value) for member in value.members}
    return None


def _empty_object(value: CanonicalValue) -> bool:
    return (
        (type(value) is ObjectExpr and not value.members)
        or (
            type(value) is LiteralExpr
            and type(value.value) is JsonObject
            and not value.value.members
        )
        or (type(value) is JsonObject and not value.members)
    )


def _array_expressions(value: CanonicalValue) -> tuple[ValueExpression, ...] | None:
    if type(value) is ArrayExpr:
        return value.items
    if type(value) is LiteralExpr and type(value.value) is JsonArray:
        return tuple(_json_expression(item, value) for item in value.value.items)
    if type(value) is JsonArray:
        return tuple(_json_expression(item) for item in value.items)
    return None


def _json_expression(value: JsonValue, owner: LiteralExpr | None = None) -> ValueExpression:
    provenance = _STATIC_PROFILE_PROVENANCE if owner is None else owner.provenance
    if type(value) is JsonObject:
        return ObjectExpr(
            members=tuple(
                ObjectExpressionMember(
                    name=member.name,
                    expression=_json_expression(member.value, owner),
                )
                for member in value.members
            ),
            provenance=provenance,
        )
    if type(value) is JsonArray:
        return ArrayExpr(
            items=tuple(_json_expression(item, owner) for item in value.items),
            provenance=provenance,
        )
    return LiteralExpr(value=value, provenance=provenance)


def _literal_text(value: ValueExpression | None) -> str | None:
    return (
        value.value.value
        if type(value) is LiteralExpr and type(value.value) is JsonString
        else None
    )


def _literal_bool(value: CanonicalValue | None) -> bool | None:
    return (
        value.value.value
        if type(value) is LiteralExpr and type(value.value) is JsonBoolean
        else value.value
        if type(value) is JsonBoolean
        else None
    )


def _literal_texts(value: ValueExpression) -> tuple[str, ...] | None:
    if type(value) is ArrayExpr:
        values = tuple(_literal_text(item) for item in value.items)
    elif type(value) is LiteralExpr and type(value.value) is JsonArray:
        values = tuple(
            item.value if type(item) is JsonString else None for item in value.value.items
        )
    else:
        return None
    return (
        None
        if any(item is None for item in values)
        else tuple(item for item in values if item is not None)
    )


def _selector_fingerprint(profile: ProfileResource, index: int) -> Fingerprint:
    digest = hashlib.sha256(
        f"comparo.redaction-selector.v1\0{profile.definition.fingerprint.digest.hex_value}\0{index}".encode()
    ).hexdigest()
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value=digest))


def _problem(
    code: DiagnosticCode,
    profile: ProfileResource,
    *path: str,
) -> Diagnostic:
    if type(profile) is CompiledResource:
        resource = profile.definition.identity
        location = profile.location
    else:
        resource = profile.origin.owner
        location = None
    return Diagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.COMPILATION,
        context=DiagnosticContext(
            resource=resource,
            location=location,
            field_path=tuple(FieldPathSegment(name=item) for item in path),
        ),
    )


def _problem_for_handle(code: DiagnosticCode, handle: DefinitionHandle) -> Diagnostic:
    resource = handle.identity if type(handle) is DefinitionRef else handle.origin.owner
    return Diagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.COMPILATION,
        context=DiagnosticContext(resource=resource),
    )


__all__ = (
    "BoundExecutionProfiles",
    "EnvironmentResponseClassification",
    "ProfileBindingResult",
    "RemoteSchemaRestriction",
    "bind_execution_profiles",
)
