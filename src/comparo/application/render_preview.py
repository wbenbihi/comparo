"""Offline, run-scoped, sanitized previews of every planned outbound request."""

from __future__ import annotations

import asyncio
import hashlib
from typing import cast
from typing import final
from urllib.parse import quote
from uuid import uuid5

import msgspec

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import LocalFileLimits
from comparo.adapters.values import LocalFilesystemError
from comparo.adapters.values import PinnedProjectFiles
from comparo.adapters.values import ProjectFileValueProvider
from comparo.adapters.values import SafeYamlValueDecoder
from comparo.adapters.values import SecureGenerator
from comparo.adapters.values import SystemRunClock
from comparo.adapters.values import YamlValueLimits
from comparo.application._async_cleanup import finish_cleanup
from comparo.application.environment_binding import CapturedAmbientEnvironment
from comparo.application.environment_binding import _validate_captured_ambient
from comparo.application.environment_binding import build_environment_snapshots
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import inspect_diagnostic
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.runtime_capabilities import preflight_runtime_capabilities
from comparo.core.artifacts.common import ARTIFACT_REDACTION_MARKER
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.artifacts.sanitization import SanitizationBudgetExceededError
from comparo.core.credentials import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.credentials import MANDATORY_SENSITIVE_QUERY_NAMES
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import EnvironmentSnapshot
from comparo.core.resolution import RunContext
from comparo.core.runtime import ConfigurationRequestProvider
from comparo.core.runtime import iter_execution_work_window
from comparo.core.runtime.models import BlockedExecution
from comparo.core.runtime.models import CancelledExecution
from comparo.core.runtime.models import ExecutionWork
from comparo.core.runtime.models import UnavailableExecution
from comparo.core.runtime.ports import RequestPreparationFailure

_PREVIEW_SEED_ORIGIN = TaintOriginId(value="runtime/render-preview-seed")


@final
class RenderPreviewSettings(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned local-source and sanitization limits for one offline preview."""

    files: LocalFileLimits
    environment: EnvironmentSourceLimits
    yaml: YamlValueLimits
    sanitization: SanitizationLimits

    def __post_init__(self) -> None:
        """Reject implicit or untyped resource ceilings."""
        expected = (
            (self.files, LocalFileLimits),
            (self.environment, EnvironmentSourceLimits),
            (self.yaml, YamlValueLimits),
            (self.sanitization, SanitizationLimits),
        )
        if any(type(value) is not kind for value, kind in expected):
            raise TypeError("render preview settings contain an untyped section")


@final
class RenderPreviewRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One admitted plan plus explicit local value inputs for an offline render."""

    project: PreparedProjectPlan
    settings: RenderPreviewSettings
    ambient_environment: CapturedAmbientEnvironment | None = None

    def __post_init__(self) -> None:
        """Validate the complete application boundary without copying live values."""
        if type(self.project) is not PreparedProjectPlan:
            raise TypeError("render preview requires PreparedProjectPlan")
        if type(self.settings) is not RenderPreviewSettings:
            raise TypeError("render preview requires RenderPreviewSettings")
        if self.ambient_environment is not None and (
            type(self.ambient_environment) is not CapturedAmbientEnvironment
        ):
            raise TypeError("render preview ambient environment must be captured and sealed")
        if self.ambient_environment is not None:
            _validate_captured_ambient(self.ambient_environment, self.settings.environment)


@final
class RenderPreviewWindowRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One finite canonical ordinal window over an admitted request plan."""

    preview: RenderPreviewRequest
    start_ordinal: int
    maximum_previews: int

    def __post_init__(self) -> None:
        """Require one typed preview composition and explicit finite window bounds."""
        if type(self.preview) is not RenderPreviewRequest:
            raise TypeError("render preview window requires RenderPreviewRequest")
        if type(self.start_ordinal) is not int or self.start_ordinal < 0:
            raise ValueError("render preview start ordinal must be non-negative")
        if type(self.maximum_previews) is not int or self.maximum_previews < 1:
            raise ValueError("render preview window size must be a positive integer")


@final
class RenderedHeader(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered sanitized request-header occurrence."""

    name: str
    value: str
    redacted: bool

    def __post_init__(self) -> None:
        """Keep preview headers JSON-safe and explicit about redaction."""
        if type(self.name) is not str or type(self.value) is not str:
            raise TypeError("rendered header name and value must be text")
        if type(self.redacted) is not bool:
            raise TypeError("rendered header redaction must be bool")
        if self.name in MANDATORY_SENSITIVE_HEADER_NAMES and (
            not self.redacted or self.value != ARTIFACT_REDACTION_MARKER
        ):
            raise ValueError("rendered credential headers must remain wholly redacted")


@final
class RenderedCookie(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered request cookie whose live value is always omitted."""

    name: str
    value: str
    redacted: bool

    def __post_init__(self) -> None:
        """Require the mandatory whole-value cookie redaction contract."""
        if type(self.name) is not str:
            raise TypeError("rendered cookie name must be text")
        if self.value != ARTIFACT_REDACTION_MARKER or self.redacted is not True:
            raise ValueError("rendered cookie values must remain wholly redacted")


class RequestBodyOmissionReason(DomainStrEnum):
    """Why a safe preview contains request-body metadata but never body content."""

    NO_BODY = "no-body"


@final
class RenderedBodyOmission(msgspec.Struct, frozen=True, kw_only=True):
    """Safe request-body presence and size metadata with content deliberately absent."""

    reason: RequestBodyOmissionReason
    byte_count: int | None

    def __post_init__(self) -> None:
        """Bind absent bodies and omitted content to unambiguous byte metadata."""
        if type(self.reason) is not RequestBodyOmissionReason:
            raise TypeError("rendered body omission reason must be typed")
        if self.byte_count is not None and (
            type(self.byte_count) is not int or self.byte_count < 0
        ):
            raise ValueError("rendered body byte count must be non-negative or absent")
        if self.byte_count is not None:
            raise ValueError("body-free render previews cannot retain body byte metadata")


@final
class RenderedRequestPreview(msgspec.Struct, frozen=True, kw_only=True):
    """One safe structural request projection in canonical execution order."""

    ordinal: int
    execution_id: str
    trial_id: str
    cell_id: str
    subject: ResourceCoordinate
    environment: ResourceCoordinate
    method: str
    url: str
    headers: tuple[RenderedHeader, ...]
    cookies: tuple[RenderedCookie, ...]
    body: RenderedBodyOmission

    def __post_init__(self) -> None:
        """Require only bounded-shape sanitized products and safe coordinates."""
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("rendered request ordinal must be non-negative")
        if any(
            type(value) is not str or not value
            for value in (self.execution_id, self.trial_id, self.cell_id, self.method, self.url)
        ):
            raise ValueError("rendered request scalar fields must be non-empty text")
        if (
            type(self.subject) is not ResourceCoordinate
            or type(self.environment) is not ResourceCoordinate
        ):
            raise TypeError("rendered request resources must be safe coordinates")
        if type(self.headers) is not tuple or any(
            type(item) is not RenderedHeader for item in self.headers
        ):
            raise TypeError("rendered request headers must be an immutable typed tuple")
        if type(self.cookies) is not tuple or any(
            type(item) is not RenderedCookie for item in self.cookies
        ):
            raise TypeError("rendered request cookies must be an immutable typed tuple")
        if type(self.body) is not RenderedBodyOmission:
            raise TypeError("rendered request body must be omission metadata")


@final
class RenderPreviewResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly every sanitized planned request or value-free whole-preview diagnostics."""

    previews: tuple[RenderedRequestPreview, ...] = ()
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Reject partial, mixed, mutable, or non-canonical result collections."""
        if type(self.previews) is not tuple or any(
            type(item) is not RenderedRequestPreview for item in self.previews
        ):
            raise TypeError("render preview products must be an immutable typed tuple")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("render preview diagnostics must be an immutable typed tuple")
        if bool(self.previews) == bool(self.diagnostics):
            raise ValueError("render preview requires exactly previews or diagnostics")
        if self.previews:
            ordinals = tuple(item.ordinal for item in self.previews)
            if ordinals != tuple(range(len(self.previews))):
                raise ValueError("render preview products must use contiguous canonical order")
            execution_ids = tuple(item.execution_id for item in self.previews)
            if len(set(execution_ids)) != len(execution_ids):
                raise ValueError("render preview execution identities must be unique")


@final
class RenderPreviewWindowResult(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded ordinal page with successful previews and per-occurrence diagnostics."""

    previews: tuple[RenderedRequestPreview, ...] = ()
    total_executions: int = 0
    next_ordinal: int | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require a canonical bounded page and an unambiguous continuation."""
        if type(self.previews) is not tuple or any(
            type(item) is not RenderedRequestPreview for item in self.previews
        ):
            raise TypeError("render preview window products must be immutable and typed")
        if type(self.total_executions) is not int or self.total_executions < 0:
            raise ValueError("render preview total must be non-negative")
        if self.next_ordinal is not None and (
            type(self.next_ordinal) is not int
            or self.next_ordinal < 0
            or self.next_ordinal >= self.total_executions
        ):
            raise ValueError("render preview continuation must name a remaining ordinal")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("render preview window diagnostics must be immutable and typed")
        if self.previews:
            ordinals = tuple(item.ordinal for item in self.previews)
            if ordinals != tuple(sorted(set(ordinals))):
                raise ValueError("render preview window ordinals must be unique and increasing")
            if ordinals[-1] >= self.total_executions:
                raise ValueError("render preview window ordinal exceeds the total")
            execution_ids = tuple(item.execution_id for item in self.previews)
            if len(set(execution_ids)) != len(execution_ids):
                raise ValueError("render preview window execution identities must be unique")


async def render_prepared_requests(request: RenderPreviewRequest) -> RenderPreviewResult:
    """Resolve and sanitize every planned request without DNS or network-capable adapters."""
    if type(request) is not RenderPreviewRequest:
        raise TypeError("request rendering requires RenderPreviewRequest")
    capabilities = preflight_runtime_capabilities(request.project)
    if capabilities.diagnostics:
        return RenderPreviewResult(
            diagnostics=tuple(inspect_diagnostic(item) for item in capabilities.diagnostics)
        )
    resources = _RenderResources()
    try:
        result = await _render_with_resources(request, resources)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - live failures become one value-free boundary result
        result = _failure("render.preview.internal", stage="render")
    finally:
        cleanup_failed = await resources.close()
    if cleanup_failed:
        return _failure("render.preview.cleanup-failed", stage="render")
    return result


async def render_prepared_request_window(
    request: RenderPreviewWindowRequest,
) -> RenderPreviewWindowResult:
    """Resolve and sanitize only one caller-bounded canonical request window."""
    if type(request) is not RenderPreviewWindowRequest:
        raise TypeError("windowed request rendering requires RenderPreviewWindowRequest")
    capabilities = preflight_runtime_capabilities(request.preview.project)
    if capabilities.diagnostics:
        return RenderPreviewWindowResult(
            total_executions=request.preview.project.prepared.plan.cardinality.environment_executions.value,
            diagnostics=tuple(inspect_diagnostic(item) for item in capabilities.diagnostics),
        )
    resources = _RenderResources()
    try:
        result = await _render_window_with_resources(request, resources)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - live failures become one value-free boundary result
        result = _window_failure(request, "render.preview.internal", stage="render")
    finally:
        cleanup_failed = await resources.close()
    if cleanup_failed:
        return _window_failure(request, "render.preview.cleanup-failed", stage="render")
    return result


class _RenderResources:
    __slots__ = ("boundary", "context", "files", "provider")

    def __init__(self) -> None:
        self.files: PinnedProjectFiles | None = None
        self.context: RunContext | None = None
        self.provider: ConfigurationRequestProvider | None = None
        self.boundary: SanitizationBoundary | None = None

    async def close(self) -> bool:
        """Close every acquired resource best-effort and report only value-free failure state."""
        failed = False
        cancellation_received = False
        if self.boundary is not None:
            try:
                self.boundary.close()
            except Exception:  # noqa: BLE001 - cleanup details never cross the safe boundary
                failed = True
        if self.provider is not None:
            outcome = await finish_cleanup(self.provider.close())
            failed = failed or outcome.failed
            cancellation_received = cancellation_received or outcome.cancellation_received
        if self.context is not None:
            outcome = await finish_cleanup(self.context.close())
            failed = failed or outcome.failed
            cancellation_received = cancellation_received or outcome.cancellation_received
        if self.files is not None:
            try:
                self.files.close()
            except Exception:  # noqa: BLE001 - cleanup details never cross the safe boundary
                failed = True
        if cancellation_received:
            raise asyncio.CancelledError
        return failed


async def _render_with_resources(
    request: RenderPreviewRequest,
    resources: _RenderResources,
) -> RenderPreviewResult:
    previews, diagnostics, _processed = await _render_products(request, resources)
    return (
        RenderPreviewResult(diagnostics=diagnostics)
        if diagnostics
        else RenderPreviewResult(previews=previews)
    )


async def _render_window_with_resources(
    request: RenderPreviewWindowRequest,
    resources: _RenderResources,
) -> RenderPreviewWindowResult:
    total = request.preview.project.prepared.plan.cardinality.environment_executions.value
    previews, diagnostics, processed = await _render_products(
        request.preview,
        resources,
        start_ordinal=request.start_ordinal,
        maximum_previews=request.maximum_previews,
    )
    consumed_until = request.start_ordinal + processed
    return RenderPreviewWindowResult(
        previews=previews,
        total_executions=total,
        next_ordinal=(consumed_until if processed and consumed_until < total else None),
        diagnostics=diagnostics,
    )


async def _render_products(
    request: RenderPreviewRequest,
    resources: _RenderResources,
    *,
    start_ordinal: int = 0,
    maximum_previews: int | None = None,
) -> tuple[
    tuple[RenderedRequestPreview, ...],
    tuple[ApplicationDiagnostic, ...],
    int,
]:
    try:
        resources.files = PinnedProjectFiles(
            request.project.project_root,
            limits=request.settings.files,
        )
    except LocalFilesystemError:
        return (), _diagnostics("render.preview.project-root-unavailable", stage="resolution"), 0
    snapshots = await build_environment_snapshots(
        request.project,
        resources.files,
        ambient=request.ambient_environment,
        limits=request.settings.environment,
    )
    if snapshots.diagnostics:
        return (), tuple(inspect_diagnostic(item) for item in snapshots.diagnostics), 0
    resources.context = _run_context(request, snapshots.snapshots, resources.files)
    resources.provider = ConfigurationRequestProvider(
        request.project.prepared,
        resources.context,
    )
    requests, diagnostics, processed = await _resolve_all(
        request.project.prepared,
        resources.provider,
        start_ordinal=start_ordinal,
        maximum_previews=maximum_previews,
    )
    if not requests:
        return (), diagnostics, processed
    snapshot = await resources.context.sanitization_snapshot()
    try:
        resources.boundary = SanitizationBoundary.from_snapshot(
            snapshot,
            request.settings.sanitization,
            marker=ARTIFACT_REDACTION_MARKER,
        )
    except SanitizationBudgetExceededError:
        return (
            (),
            (
                *diagnostics,
                *_diagnostics("render.preview.sanitization-budget-exceeded", stage="render"),
            ),
            processed,
        )
    resources.boundary.seal()
    return (
        tuple(_render_request(work, finalized, resources.boundary) for work, finalized in requests),
        diagnostics,
        processed,
    )


def _run_context(
    request: RenderPreviewRequest,
    snapshots: tuple[EnvironmentSnapshot, ...],
    files: PinnedProjectFiles,
) -> RunContext:
    seed_material = (
        "comparo.render-preview/v1alpha1:"
        f"{request.project.prepared.plan.run_id.value}:"
        f"{request.project.prepared.plan.fingerprint.digest.hex_value}"
    ).encode("ascii")
    seed = SensitiveValue(
        BytesValue(value=hashlib.sha256(seed_material).digest()),
        origins=(_PREVIEW_SEED_ORIGIN,),
    )
    return RunContext(
        prepared=request.project.prepared,
        environment_snapshots=snapshots,
        clock=SystemRunClock(),
        generator=DeterministicGenerator(seed),
        file_provider=ProjectFileValueProvider(files, request.project.policy),
        secure_generator=SecureGenerator(),
        yaml_decoder=SafeYamlValueDecoder(request.settings.yaml),
    )


async def _resolve_all(
    prepared: PreparedRun,
    provider: ConfigurationRequestProvider,
    *,
    start_ordinal: int = 0,
    maximum_previews: int | None = None,
) -> tuple[
    tuple[tuple[ExecutionWork, FinalizedWireRequest], ...],
    tuple[ApplicationDiagnostic, ...],
    int,
]:
    resolved: list[tuple[ExecutionWork, FinalizedWireRequest]] = []
    diagnostics: list[ApplicationDiagnostic] = []
    total = prepared.plan.cardinality.environment_executions.value
    if start_ordinal >= total:
        return (), (), 0
    maximum_items = total - start_ordinal if maximum_previews is None else maximum_previews
    processed = 0
    for work in iter_execution_work_window(
        prepared,
        start_ordinal=start_ordinal,
        maximum_items=maximum_items,
    ):
        processed += 1
        attempt = _attempt_scope(work)
        outcome = await provider.prepare(work, attempt)
        if type(outcome) is RequestPreparationFailure:
            diagnostics.append(_preparation_diagnostic(work, outcome))
        else:
            resolved.append((work, outcome))
    return tuple(resolved), tuple(diagnostics), processed


def _attempt_scope(work: ExecutionWork) -> AttemptScope:
    return AttemptScope(
        execution=work.scope,
        attempt_id=AttemptId(value=uuid5(work.scope.execution_id.value, "attempt:0")),
    )


def _preparation_diagnostic(
    work: ExecutionWork,
    failure: RequestPreparationFailure,
) -> ApplicationDiagnostic:
    outcome = failure.outcome
    if type(outcome) in {UnavailableExecution, BlockedExecution}:
        typed = cast("UnavailableExecution | BlockedExecution", outcome)
        code = typed.code.value
        stage = typed.stage.value
    elif type(outcome) is CancelledExecution:
        code = "render.preview.cancelled"
        stage = "resolution"
    else:  # pragma: no cover - RequestPreparationFailure domain invariant
        raise TypeError("render preview received an unsupported preparation failure")
    return _work_diagnostic(work, code=code, stage=stage)


def _work_diagnostic(
    work: ExecutionWork,
    *,
    code: str,
    stage: str,
) -> ApplicationDiagnostic:
    identity = work.subject.identity
    return ApplicationDiagnostic(
        code=code,
        severity="error",
        stage=stage,
        resource=(
            identity.namespace.value,
            identity.kind.value,
            identity.resource_id.value,
        ),
        path=(str(work.ordinal.value),),
    )


def _render_request(
    work: ExecutionWork,
    request: FinalizedWireRequest,
    boundary: SanitizationBoundary,
) -> RenderedRequestPreview:
    return RenderedRequestPreview(
        ordinal=work.ordinal.value,
        execution_id=str(work.scope.execution_id.value),
        trial_id=str(work.trial.trial_id.value),
        cell_id=str(work.trial.cell.cell_id.value),
        subject=_coordinate(work.subject),
        environment=_coordinate(work.environment),
        method=boundary.sanitize_text(request.method.value).value,
        url=_render_url(request.url, boundary),
        headers=tuple(
            _render_header(item.name.value, item.value, boundary) for item in request.headers.fields
        ),
        cookies=tuple(
            RenderedCookie(
                name=boundary.sanitize_text(item.name.value).value,
                value=boundary.sanitize_text(item.value, mandatory=True).value,
                redacted=True,
            )
            for item in request.cookies.cookies
        ),
        body=_body_omission(request),
    )


def _coordinate(value: DefinitionRef) -> ResourceCoordinate:
    identity = value.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=value.fingerprint.digest.hex_value,
    )


def _render_url(url: AbsoluteHttpUrl, boundary: SanitizationBoundary) -> str:
    host = url.authority.host.value
    if type(url.authority.host) is Ipv6Host:
        host = f"[{host}]"
    authority = host if url.authority.port is None else f"{host}:{url.authority.port.value}"
    path = "/" + "/".join(_render_path_segment(item, boundary) for item in url.path.segments)
    if url.path.trailing_slash:
        path += "/"
    query: list[str] = []
    for parameter in url.query.parameters:
        safe_name = boundary.sanitize_text(parameter.name.value)
        name = quote(safe_name.value, safe="-._~", encoding="utf-8", errors="strict")
        if type(parameter.value) is BareQueryValue:
            query.append(name)
            continue
        value = (
            parameter.value
            if is_sensitive_value(parameter.value)
            else cast(
                QueryText,
                parameter.value,
            ).value
        )
        safe_value = boundary.sanitize_text(
            value,
            mandatory=parameter.name.value.casefold() in MANDATORY_SENSITIVE_QUERY_NAMES,
        )
        encoded = quote(safe_value.value, safe="-._~", encoding="utf-8", errors="strict")
        query.append(f"{name}={encoded}")
    suffix = "" if not query else "?" + "&".join(query)
    return boundary.sanitize_text(f"{url.scheme.value}://{authority}{path}{suffix}").value


def _render_path_segment(value: object, boundary: SanitizationBoundary) -> str:
    if is_sensitive_value(value):
        safe = boundary.sanitize_text(value).value
        return quote(safe, safe="-._~", encoding="utf-8", errors="strict")
    if type(value) is DecodedPathSegment:
        safe = boundary.sanitize_text(value.value).value
        return quote(safe, safe="-._~", encoding="utf-8", errors="strict")
    encoded = cast(EncodedPathSegment, value)
    return boundary.sanitize_text(encoded.value).value


def _render_header(
    name: str,
    value: HeaderText | SensitiveValue[HeaderText],
    boundary: SanitizationBoundary,
) -> RenderedHeader:
    safe_name = boundary.sanitize_text(name)
    field_value: str | SensitiveValue[object] = value.value if type(value) is HeaderText else value
    safe_value = boundary.sanitize_text(
        field_value,
        mandatory=name in MANDATORY_SENSITIVE_HEADER_NAMES,
    )
    return RenderedHeader(
        name=safe_name.value,
        value=safe_value.value,
        redacted=safe_name.redaction_count + safe_value.redaction_count > 0,
    )


def _body_omission(request: FinalizedWireRequest) -> RenderedBodyOmission:
    if type(request.body) is not NoMaterializedBody:
        raise TypeError("body-free render preview received materialized content")
    return RenderedBodyOmission(
        reason=RequestBodyOmissionReason.NO_BODY,
        byte_count=None,
    )


def _failure(code: str, *, stage: str) -> RenderPreviewResult:
    return RenderPreviewResult(diagnostics=_diagnostics(code, stage=stage))


def _window_failure(
    request: RenderPreviewWindowRequest,
    code: str,
    *,
    stage: str,
) -> RenderPreviewWindowResult:
    return RenderPreviewWindowResult(
        total_executions=request.preview.project.prepared.plan.cardinality.environment_executions.value,
        diagnostics=_diagnostics(code, stage=stage),
    )


def _diagnostics(code: str, *, stage: str) -> tuple[ApplicationDiagnostic, ...]:
    return (ApplicationDiagnostic(code=code, severity="error", stage=stage),)


__all__ = (
    "RenderPreviewRequest",
    "RenderPreviewResult",
    "RenderPreviewSettings",
    "RenderPreviewWindowRequest",
    "RenderPreviewWindowResult",
    "RenderedBodyOmission",
    "RenderedCookie",
    "RenderedHeader",
    "RenderedRequestPreview",
    "RequestBodyOmissionReason",
    "render_prepared_request_window",
    "render_prepared_requests",
)
