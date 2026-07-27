"""Offline sanitized request-rendering application-service tests."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import Never
from typing import cast

import msgspec
import pytest

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import LocalFileLimits
from comparo.adapters.values import LocalFilesystemError
from comparo.adapters.values import LocalFilesystemErrorCode
from comparo.adapters.values import YamlValueLimits
from comparo.application import render_preview as rendering
from comparo.application.environment_binding import CapturedAmbientEnvironment
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.preflight import prepare_project_plan
from comparo.application.render_preview import RenderedBodyOmission
from comparo.application.render_preview import RenderedCookie
from comparo.application.render_preview import RenderedHeader
from comparo.application.render_preview import RenderedRequestPreview
from comparo.application.render_preview import RenderPreviewRequest
from comparo.application.render_preview import RenderPreviewResult
from comparo.application.render_preview import RenderPreviewSettings
from comparo.application.render_preview import RenderPreviewWindowRequest
from comparo.application.render_preview import RequestBodyOmissionReason
from comparo.application.render_preview import render_prepared_request_window
from comparo.application.render_preview import render_prepared_requests
from comparo.core.artifacts.common import ARTIFACT_REDACTION_MARKER
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime.models import BlockedExecution
from comparo.core.runtime.models import CancelledExecution
from comparo.core.runtime.models import UnavailableExecution
from comparo.core.runtime.ports import RequestPreparationFailure
from tests.application.test_preflight import _request


def _settings(*, maximum_patterns: int = 256) -> RenderPreviewSettings:
    return RenderPreviewSettings(
        files=LocalFileLimits(),
        environment=EnvironmentSourceLimits(),
        yaml=YamlValueLimits(),
        sanitization=SanitizationLimits(
            maximum_patterns=PositiveCount(value=maximum_patterns),
            maximum_pattern_bytes=ByteCount(value=64 * 1024),
            maximum_value_bytes=ByteCount(value=16 * 1024),
            maximum_value_nodes=PositiveCount(value=1024),
        ),
    )


def _project(
    root: Path,
    *,
    request_spec: str = "{request: {method: GET, endpoint: /ping}}",
    project_fields: str = "",
    staging_spec: str = "{baseUrl: 'https://staging.example.test'}",
    trials: int = 1,
) -> PreparedProjectPlan:
    data = root / ".comparo"
    data.mkdir()
    extra = f"  {project_fields}\n" if project_fields else ""
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec:\n"
        "  data: .comparo\n"
        f"{extra}"
        "  defaults:\n"
        "    subjects: [{$use: Request/ping}]\n"
        "    baseline: {$use: staging}\n"
        "    candidates: [{$use: prod}]\n"
        f"    trials: {trials}\n",
        encoding="utf-8",
    )
    (data / "resources.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: staging, name: Staging}\n"
        f"spec: {staging_spec}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: prod, name: Prod}\n"
        "spec: {baseUrl: 'https://prod.example.test'}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Request\n"
        "metadata: {id: ping, name: Ping}\n"
        f"spec: {request_spec}\n",
        encoding="utf-8",
    )
    outcome = prepare_project_plan(_request(config))
    assert outcome.project is not None, outcome.diagnostics
    return outcome.project


def _render(
    project: PreparedProjectPlan,
    *,
    settings: RenderPreviewSettings | None = None,
    ambient: dict[str, str] | None = None,
) -> RenderPreviewResult:
    return asyncio.run(
        render_prepared_requests(
            RenderPreviewRequest(
                project=project,
                settings=_settings() if settings is None else settings,
                ambient_environment=(
                    None
                    if ambient is None
                    else CapturedAmbientEnvironment(
                        tuple(sorted(ambient.items())),
                        limits=EnvironmentSourceLimits(),
                    )
                ),
            )
        )
    )


def _coordinate(name: str) -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="demo",
        kind="Request",
        resource_id=name,
        fingerprint="0" * 64,
    )


def _body() -> RenderedBodyOmission:
    return RenderedBodyOmission(
        reason=RequestBodyOmissionReason.NO_BODY,
        byte_count=None,
    )


def _preview(*, ordinal: int = 0, execution_id: str = "execution") -> RenderedRequestPreview:
    return RenderedRequestPreview(
        ordinal=ordinal,
        execution_id=execution_id,
        trial_id="trial",
        cell_id="cell",
        subject=_coordinate("request"),
        environment=msgspec.structs.replace(
            _coordinate("environment"),
            kind="Environment",
        ),
        method="GET",
        url="https://example.test/",
        headers=(),
        cookies=(),
        body=_body(),
    )


def _boundary(*values: SensitiveValue[object]) -> SanitizationBoundary:
    boundary = SanitizationBoundary(_settings().sanitization)
    for value in values:
        boundary.register(value)
    boundary.seal()
    return boundary


def _provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def test_resolves_every_environment_offline_in_canonical_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_dns(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("render preview must not resolve DNS")

    monkeypatch.setattr(socket, "getaddrinfo", deny_dns)
    project = _project(
        tmp_path,
        request_spec=(
            "{request: {method: get, endpoint: '/users/a b/', "
            "query: {q: 'hello world', empty: ''}, headers: {X-Trace: visible}}}"
        ),
    )

    first = _render(project)
    second = _render(project)

    assert first == second
    assert first.diagnostics == ()
    assert tuple(item.ordinal for item in first.previews) == (0, 1)
    assert tuple(item.environment.resource_id for item in first.previews) == (
        "staging",
        "prod",
    )
    assert tuple(item.url for item in first.previews) == (
        "https://staging.example.test/users/a%20b/?q=hello%20world&empty=",
        "https://prod.example.test/users/a%20b/?q=hello%20world&empty=",
    )
    assert all(item.method == "GET" for item in first.previews)
    expected_headers = (RenderedHeader(name="x-trace", value="visible", redacted=False),)
    assert all(item.headers == expected_headers for item in first.previews)
    assert all(item.cookies == () and item.body == _body() for item in first.previews)
    assert len({item.execution_id for item in first.previews}) == 2


def test_windowed_preview_resolves_only_the_requested_canonical_page(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = RenderPreviewRequest(project=project, settings=_settings())

    first = asyncio.run(
        render_prepared_request_window(
            RenderPreviewWindowRequest(
                preview=request,
                start_ordinal=0,
                maximum_previews=1,
            )
        )
    )
    second = asyncio.run(
        render_prepared_request_window(
            RenderPreviewWindowRequest(
                preview=request,
                start_ordinal=cast("int", first.next_ordinal),
                maximum_previews=1,
            )
        )
    )

    assert first.diagnostics == second.diagnostics == ()
    assert first.total_executions == second.total_executions == 2
    assert tuple(item.ordinal for item in first.previews) == (0,)
    assert tuple(item.ordinal for item in second.previews) == (1,)
    assert first.next_ordinal == 1
    assert second.next_ordinal is None


def test_windowed_preview_beyond_the_plan_is_an_empty_terminal_page(tmp_path: Path) -> None:
    project = _project(tmp_path)

    result = asyncio.run(
        render_prepared_request_window(
            RenderPreviewWindowRequest(
                preview=RenderPreviewRequest(project=project, settings=_settings()),
                start_ordinal=2,
                maximum_previews=1,
            )
        )
    )

    assert result.total_executions == 2
    assert result.previews == ()
    assert result.diagnostics == ()
    assert result.next_ordinal is None


def test_secret_values_are_absent_from_every_returned_product(tmp_path: Path) -> None:
    secret = "very-secret-render-canary"
    project = _project(
        tmp_path,
        project_fields=f"secrets: {{token: {secret}}}",
        request_spec=(
            "{request: {method: GET, endpoint: /safe, "
            "query: {q: '${secrets.token}'}, headers: {X-Trace: '${secrets.token}'}}}"
        ),
    )

    result = _render(project)
    encoded = msgspec.json.encode(result).decode()

    assert result.diagnostics == ()
    assert secret not in encoded
    assert encoded.count(ARTIFACT_REDACTION_MARKER) >= 2
    assert encoded.count("%3Credacted%3E") >= 2
    assert all(item.headers[0].redacted for item in result.previews)


@pytest.mark.parametrize(
    "request_spec",
    [
        "{request: {method: GET, url: 'https://${secrets.token}/'}}",
        "{request: {method: GET, endpoint: '/${secrets.token}'}}",
    ],
)
def test_secret_derived_routing_fails_the_whole_preview(
    tmp_path: Path,
    request_spec: str,
) -> None:
    secret = "route-secret-canary"
    result = _render(
        _project(
            tmp_path,
            project_fields=f"secrets: {{token: {secret}}}",
            request_spec=request_spec,
        )
    )

    assert result.previews == ()
    assert {item.code for item in result.diagnostics} == {"runtime.request.sensitive-target"}
    assert secret not in msgspec.json.encode(result).decode()


def test_body_preview_fails_closed_without_exposing_live_payload(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        request_spec="{request: {method: POST, endpoint: /, body: {secret: canary}}}",
    )

    result = _render(project)

    assert result.previews == ()
    assert {item.code for item in result.diagnostics} == {"runtime.request.shape-invalid"}
    assert len(result.diagnostics) == 2
    assert "canary" not in msgspec.json.encode(result).decode()


def test_environment_snapshot_failures_discard_all_requests(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        staging_spec=(
            "{baseUrl: 'https://staging.example.test', env: {files: [{path: missing.env}]}}"
        ),
    )

    result = _render(project)

    assert result.previews == ()
    assert {item.code for item in result.diagnostics} == {"environment.snapshot.not-found"}


def test_resolution_failure_discards_successful_sibling_requests(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        staging_spec=(
            "{baseUrl: 'https://staging.example.test', env: {values: {ONLY_STAGING: yes}}}"
        ),
        request_spec=(
            "{request: {method: GET, endpoint: /, headers: {X-Env: {$env: ONLY_STAGING}}}}"
        ),
    )

    result = _render(project)

    assert result.previews == ()
    assert tuple(item.path for item in result.diagnostics) == (("1",),)
    assert result.diagnostics[0].code == "runtime.request.unavailable"


def test_window_retains_successes_and_failures_from_the_same_bounded_page(
    tmp_path: Path,
) -> None:
    project = _project(
        tmp_path,
        staging_spec=(
            "{baseUrl: 'https://staging.example.test', env: {values: {ONLY_STAGING: yes}}}"
        ),
        request_spec=(
            "{request: {method: GET, endpoint: /, headers: {X-Env: {$env: ONLY_STAGING}}}}"
        ),
    )

    result = asyncio.run(
        render_prepared_request_window(
            RenderPreviewWindowRequest(
                preview=RenderPreviewRequest(project=project, settings=_settings()),
                start_ordinal=0,
                maximum_previews=2,
            )
        )
    )

    assert result.total_executions == 2
    assert tuple(item.ordinal for item in result.previews) == (0,)
    assert tuple(item.path for item in result.diagnostics) == (("1",),)
    assert result.next_ordinal is None


def test_window_bounds_fifty_consecutive_failures_to_one_occurrence(
    tmp_path: Path,
) -> None:
    project = _project(
        tmp_path,
        request_spec=("{request: {method: GET, endpoint: /, headers: {X-Env: {$env: MISSING}}}}"),
        trials=25,
    )
    preview = RenderPreviewRequest(project=project, settings=_settings())

    first = asyncio.run(
        render_prepared_request_window(
            RenderPreviewWindowRequest(
                preview=preview,
                start_ordinal=0,
                maximum_previews=1,
            )
        )
    )
    last = asyncio.run(
        render_prepared_request_window(
            RenderPreviewWindowRequest(
                preview=preview,
                start_ordinal=49,
                maximum_previews=1,
            )
        )
    )

    assert first.total_executions == last.total_executions == 50
    assert first.previews == last.previews == ()
    assert tuple(item.path for item in first.diagnostics) == (("0",),)
    assert tuple(item.path for item in last.diagnostics) == (("49",),)
    assert first.next_ordinal == 1
    assert last.next_ordinal is None


def test_caller_sanitization_budget_fails_closed_without_values(tmp_path: Path) -> None:
    secret = "budget-secret-canary"
    project = _project(
        tmp_path,
        project_fields=f"secrets: {{token: {secret}}}",
        request_spec=(
            "{request: {method: GET, endpoint: /, headers: {X-Token: '${secrets.token}'}}}"
        ),
    )

    result = _render(project, settings=_settings(maximum_patterns=1))

    assert result.previews == ()
    assert result.diagnostics[0].code == "render.preview.sanitization-budget-exceeded"
    assert secret not in msgspec.json.encode(result).decode()


def test_public_contracts_reject_untyped_or_ambiguous_state(tmp_path: Path) -> None:
    settings = _settings()
    project = _project(tmp_path)
    diagnostic = ApplicationDiagnostic(code="test", severity="error", stage="render")

    for field in ("files", "environment", "yaml", "sanitization"):
        with pytest.raises(TypeError, match="untyped section"):
            msgspec.structs.replace(settings, **{field: object()})
    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        RenderPreviewRequest(project=cast("PreparedProjectPlan", object()), settings=settings)
    with pytest.raises(TypeError, match="RenderPreviewSettings"):
        RenderPreviewRequest(project=project, settings=cast("RenderPreviewSettings", object()))
    with pytest.raises(TypeError, match="captured and sealed"):
        RenderPreviewRequest(
            project=project,
            settings=settings,
            ambient_environment=cast("Any", []),
        )
    with pytest.raises(TypeError, match="captured and sealed"):
        RenderPreviewRequest(
            project=project,
            settings=settings,
            ambient_environment=cast("Any", (("GOOD", object()),)),
        )
    with pytest.raises(TypeError, match="RenderPreviewRequest"):
        asyncio.run(render_prepared_requests(cast("RenderPreviewRequest", object())))

    with pytest.raises(TypeError, match="name and value"):
        RenderedHeader(name=cast("str", object()), value="x", redacted=False)
    with pytest.raises(TypeError, match="redaction"):
        RenderedHeader(name="x", value="x", redacted=cast("bool", 1))
    with pytest.raises(ValueError, match="credential"):
        RenderedHeader(name="authorization", value="live", redacted=False)
    with pytest.raises(TypeError, match="name"):
        RenderedCookie(name=cast("str", object()), value=ARTIFACT_REDACTION_MARKER, redacted=True)
    with pytest.raises(ValueError, match="wholly"):
        RenderedCookie(name="sid", value="live", redacted=True)
    with pytest.raises(ValueError, match="wholly"):
        RenderedCookie(
            name="sid",
            value=ARTIFACT_REDACTION_MARKER,
            redacted=False,
        )
    assert RenderedCookie(
        name="sid",
        value=ARTIFACT_REDACTION_MARKER,
        redacted=True,
    ).redacted

    with pytest.raises(TypeError, match="typed"):
        RenderedBodyOmission(reason=cast("RequestBodyOmissionReason", "no-body"), byte_count=None)
    with pytest.raises(ValueError, match="non-negative"):
        RenderedBodyOmission(reason=RequestBodyOmissionReason.NO_BODY, byte_count=-1)
    with pytest.raises(ValueError, match="body-free"):
        RenderedBodyOmission(reason=RequestBodyOmissionReason.NO_BODY, byte_count=0)

    base = _preview()
    invalid_previews: tuple[tuple[dict[str, object], str], ...] = (
        ({"ordinal": -1}, "ordinal"),
        ({"execution_id": ""}, "scalar"),
        ({"subject": object()}, "resources"),
        ({"headers": []}, "headers"),
        ({"headers": (object(),)}, "headers"),
        ({"cookies": []}, "cookies"),
        ({"cookies": (object(),)}, "cookies"),
        ({"body": object()}, "body"),
    )
    for changes, message in invalid_previews:
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(base, **changes)

    with pytest.raises(TypeError, match="products"):
        RenderPreviewResult(previews=cast("Any", []))
    with pytest.raises(TypeError, match="products"):
        RenderPreviewResult(previews=cast("Any", (object(),)))
    with pytest.raises(TypeError, match="diagnostics"):
        RenderPreviewResult(diagnostics=cast("Any", []))
    with pytest.raises(TypeError, match="diagnostics"):
        RenderPreviewResult(diagnostics=cast("Any", (object(),)))
    with pytest.raises(ValueError, match="exactly"):
        RenderPreviewResult()
    with pytest.raises(ValueError, match="exactly"):
        RenderPreviewResult(previews=(base,), diagnostics=(diagnostic,))
    with pytest.raises(ValueError, match="contiguous"):
        RenderPreviewResult(previews=(_preview(ordinal=1),))
    with pytest.raises(ValueError, match="unique"):
        RenderPreviewResult(previews=(base, _preview(ordinal=1, execution_id=base.execution_id)))


def test_structural_url_header_and_body_render_helpers() -> None:
    origin = TaintOriginId(value="test/preview-secret")
    secret_query = SensitiveValue(QueryText(value="secret"), origins=(origin,))
    secret_path = SensitiveValue(DecodedPathSegment(value="private"), origins=(origin,))
    boundary = _boundary(
        cast("SensitiveValue[object]", secret_query),
        cast("SensitiveValue[object]", secret_path),
    )
    query = QueryParameters(
        parameters=(
            QueryParameter(
                name=QueryName(value="bare"),
                value=BareQueryValue(),
                source=QuerySource.AUTHORED,
                provenance=_provenance(),
            ),
            QueryParameter(
                name=QueryName(value="q"),
                value=secret_query,
                source=QuerySource.AUTHORED,
                provenance=_provenance(),
            ),
        )
    )
    url = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(
            host=Ipv6Host(value="2001:db8::1"),
            port=HttpPort(value=8443),
        ),
        path=UrlPath(
            segments=(
                DecodedPathSegment(value="a b"),
                EncodedPathSegment(value="already%20encoded"),
                secret_path,
            ),
            trailing_slash=True,
        ),
        query=query,
    )
    try:
        assert rendering._render_url(url, boundary) == (
            "https://[2001:db8::1]:8443/a%20b/already%20encoded/%3Credacted%3E/"
            "?bare&q=%3Credacted%3E"
        )
        assert (
            rendering._render_url(
                AbsoluteHttpUrl(
                    scheme=HttpScheme.HTTP,
                    authority=HttpAuthority(host=DnsHost(value="example.test")),
                ),
                boundary,
            )
            == "http://example.test/"
        )
        assert rendering._render_header(
            "x-test",
            HeaderText(value="public"),
            boundary,
        ) == RenderedHeader(name="x-test", value="public", redacted=False)
        assert rendering._render_header(
            "authorization",
            HeaderText(value="public"),
            boundary,
        ) == RenderedHeader(
            name="authorization",
            value=ARTIFACT_REDACTION_MARKER,
            redacted=True,
        )
    finally:
        boundary.close()

    assert (
        rendering._body_omission(
            cast(
                "FinalizedWireRequest",
                SimpleNamespace(body=NoMaterializedBody()),
            )
        )
        == _body()
    )
    with pytest.raises(TypeError, match="materialized"):
        rendering._body_omission(cast("FinalizedWireRequest", SimpleNamespace(body=object())))


def test_preparation_failure_normalization_covers_all_terminal_kinds(tmp_path: Path) -> None:
    project = _project(tmp_path)
    work = next(iter_execution_work(project.prepared))
    unavailable = RequestPreparationFailure(
        outcome=UnavailableExecution(
            stage=DiagnosticStage.RESOLUTION,
            code=DiagnosticCode(value="test.unavailable"),
        )
    )
    blocked = RequestPreparationFailure(
        outcome=BlockedExecution(
            stage=DiagnosticStage.AUTHORIZATION,
            code=DiagnosticCode(value="test.blocked"),
        )
    )
    cancelled = RequestPreparationFailure(
        outcome=CancelledExecution(source=CancellationSource.USER)
    )

    assert rendering._preparation_diagnostic(work, unavailable).code == "test.unavailable"
    assert rendering._preparation_diagnostic(work, blocked).stage == "authorization"
    normalized = rendering._preparation_diagnostic(work, cancelled)
    assert normalized.code == "render.preview.cancelled"
    assert normalized.path == ("0",)


def test_internal_and_cleanup_failures_are_value_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = RenderPreviewRequest(project=_project(tmp_path), settings=_settings())

    async def explode(*_args: object) -> RenderPreviewResult:
        raise RuntimeError("secret-internal-canary")

    async def close_ok(_self: object) -> bool:
        return False

    monkeypatch.setattr(rendering, "_render_with_resources", explode)
    monkeypatch.setattr(rendering._RenderResources, "close", close_ok)
    internal = asyncio.run(render_prepared_requests(request))
    assert internal.diagnostics[0].code == "render.preview.internal"
    assert "canary" not in msgspec.json.encode(internal).decode()

    async def succeed(*_args: object) -> RenderPreviewResult:
        return RenderPreviewResult(previews=(_preview(),))

    async def close_failed(_self: object) -> bool:
        return True

    monkeypatch.setattr(rendering, "_render_with_resources", succeed)
    monkeypatch.setattr(rendering._RenderResources, "close", close_failed)
    cleanup = asyncio.run(render_prepared_requests(request))
    assert cleanup.diagnostics[0].code == "render.preview.cleanup-failed"


def test_cancellation_is_re_raised_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = RenderPreviewRequest(project=_project(tmp_path), settings=_settings())
    closed = False

    async def cancelled(*_args: object) -> RenderPreviewResult:
        raise asyncio.CancelledError

    async def close(_self: object) -> bool:
        nonlocal closed
        closed = True
        return False

    monkeypatch.setattr(rendering, "_render_with_resources", cancelled)
    monkeypatch.setattr(rendering._RenderResources, "close", close)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(render_prepared_requests(request))
    assert closed


def test_local_filesystem_open_failure_is_value_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)

    def unavailable(*_args: object, **_kwargs: object) -> Never:
        raise LocalFilesystemError(LocalFilesystemErrorCode.ROOT_UNSAFE)

    monkeypatch.setattr(rendering, "PinnedProjectFiles", unavailable)
    result = _render(project)
    assert result.diagnostics[0].code == "render.preview.project-root-unavailable"


class _SyncClose:
    def __init__(self, *, fails: bool = False) -> None:
        self.closed = False
        self._fails = fails

    def close(self) -> None:
        self.closed = True
        if self._fails:
            raise RuntimeError("close")


class _AsyncClose:
    def __init__(self, *, fails: bool = False) -> None:
        self.closed = False
        self._fails = fails

    async def close(self) -> None:
        self.closed = True
        if self._fails:
            raise RuntimeError("close")


@pytest.mark.parametrize("failing", ["boundary", "provider", "context", "files", None])
def test_resource_cleanup_is_best_effort(failing: str | None) -> None:
    resources = rendering._RenderResources()
    boundary = _SyncClose(fails=failing == "boundary")
    provider = _AsyncClose(fails=failing == "provider")
    context = _AsyncClose(fails=failing == "context")
    files = _SyncClose(fails=failing == "files")
    resources.boundary = cast("Any", boundary)
    resources.provider = cast("Any", provider)
    resources.context = cast("Any", context)
    resources.files = cast("Any", files)

    assert asyncio.run(resources.close()) is (failing is not None)
    assert boundary.closed
    assert files.closed
    assert context.closed
    assert provider.closed


def test_render_cleanup_finishes_all_resources_under_repeated_cancellation() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowProvider:
            async def close(self) -> None:
                entered.set()
                await release.wait()

        resources = rendering._RenderResources()
        files = _SyncClose()
        context = _AsyncClose()
        resources.provider = cast("Any", SlowProvider())
        resources.context = cast("Any", context)
        resources.files = cast("Any", files)
        task = asyncio.create_task(resources.close())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert context.closed
        assert files.closed

    asyncio.run(scenario())
