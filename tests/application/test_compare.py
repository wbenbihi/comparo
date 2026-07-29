"""Unified interactive Compare preparation and start facade tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from sys import float_info
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.application import compare
from comparo.application import execution_session
from comparo.application.compare import CompareCloseResult
from comparo.application.compare import CompareExecutionControls
from comparo.application.compare import CompareIntent
from comparo.application.compare import ComparePreparationHandle
from comparo.application.compare import ComparePreparationResult
from comparo.application.compare import ComparePrepareRequest
from comparo.application.compare import CompareService
from comparo.application.compare import CompareServiceError
from comparo.application.compare import CompareServiceErrorCode
from comparo.application.environment_binding import CapturedAmbientEnvironment
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.plan_occurrences import PlanOccurrenceWindow
from comparo.application.plan_review import PlanReviewInspection
from comparo.application.planning import PlanningOptions
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import prepare_project_plan
from comparo.application.production_execution import ProductionExecutionFailure
from comparo.application.production_execution import ProductionExecutionFailureKind
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.production_execution import ProductionRunAnnotations
from comparo.application.production_execution import production_execution_defaults_v1
from comparo.application.render_preview import RenderPreviewWindowRequest
from comparo.application.render_preview import RenderPreviewWindowResult
from comparo.application.resolved_inspection import ResolvedValueInspectionResult
from comparo.application.resolved_inspection import ResolvedValueInspectionTarget
from comparo.application.resolved_inspection import ResolvedValueProjectionLimits
from comparo.application.resolved_inspection import (
    _ResolvedValueInspectionRequest as ResolvedValueInspectionRequest,
)
from comparo.application.rule_inspection import PlanRuleWindow
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount

_INVOCATION_ID = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("20000000-0000-0000-0000-000000000002")
_HANDLE_ID = UUID("30000000-0000-0000-0000-000000000003")
_SECRET_CANARY = "compare-secret-canary"
_AMBIENT_SECRET_CANARY = "compare-ambient-secret-canary"


def _write_project(root: Path) -> Path:
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec:\n"
        "  data: .comparo\n"
        "  defaults:\n"
        "    subjects: [{$use: Request/ping}]\n"
        "    environments: [{$use: staging}]\n",
        encoding="utf-8",
    )
    data = root / ".comparo"
    data.mkdir()
    (data / "resources.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: staging, name: Staging}\n"
        "spec: {baseUrl: https://staging.example.test}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: prod, name: Prod}\n"
        "spec: {baseUrl: https://prod.example.test}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Request\n"
        "metadata: {id: ping, name: Ping}\n"
        "spec: {request: {method: GET, endpoint: /ping}}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: ExecutionProfile\n"
        "metadata: {id: release, name: Release}\n"
        "spec:\n"
        "  subjects: [{$use: Request/ping}]\n"
        "  environments: [{$use: staging}]\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Value\n"
        "metadata: {id: credentials, name: Credentials}\n"
        f"spec: {{value: {{token: {_SECRET_CANARY}}}}}\n",
        encoding="utf-8",
    )
    return config


def _project_request(config: Path, options: PlanningOptions) -> ProjectPlanRequest:
    return ProjectPlanRequest(
        config=config,
        additional_sources=(),
        policy=None,
        options=options,
        invocation_id=_INVOCATION_ID,
        run_id=_RUN_ID,
    )


def _request(
    config: Path,
    intent: CompareIntent,
    options: PlanningOptions,
) -> ComparePrepareRequest:
    return ComparePrepareRequest(
        intent=intent,
        project=_project_request(config, options),
        controls=_controls(),
    )


def _assertion_request(config: Path) -> ComparePrepareRequest:
    return _request(
        config,
        CompareIntent.ASSERTION,
        PlanningOptions(subjects=("ping",), environments=("staging",)),
    )


def _controls() -> CompareExecutionControls:
    return CompareExecutionControls(
        settings=production_execution_defaults_v1(),
        artifact_root=None,
        approve_mutations=False,
        tool_version="test",
    )


def _coordinate(resource: CompiledResource) -> ResourceCoordinate:
    definition = resource.definition
    identity = definition.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=definition.fingerprint.digest.hex_value,
    )


def _resolved_target(
    service: CompareService,
    handle: ComparePreparationHandle,
) -> ResolvedValueInspectionTarget:
    project = service._prepared[handle].project.prepared.project
    request = next(
        item for item in project.resources if item.definition.identity.resource_id.value == "ping"
    )
    environment = next(
        item
        for item in project.resources
        if item.definition.identity.resource_id.value == "staging"
    )
    return ResolvedValueInspectionTarget(
        resource=_coordinate(request),
        field_path=("request", "endpoint"),
        execution_ordinal=0,
        environment=_coordinate(environment),
        limits=ResolvedValueProjectionLimits(
            maximum_page_nodes=PositiveCount(value=8),
            maximum_scalar_bytes=ByteCount(value=1024),
            maximum_path_segments=PositiveCount(value=16),
            maximum_path_segment_bytes=ByteCount(value=128),
            maximum_traversal_nodes=PositiveCount(value=1024),
            maximum_traversal_bytes=ByteCount(value=1024 * 1024),
            maximum_taint_origins=PositiveCount(value=16),
            maximum_taint_origin_bytes=ByteCount(value=256),
        ),
        start_node=0,
        maximum_nodes=8,
    )


def _failure() -> ProductionExecutionResult:
    return ProductionExecutionResult(
        failure=ProductionExecutionFailure(
            kind=ProductionExecutionFailureKind.INTERNAL,
            diagnostics=(
                ApplicationDiagnostic(
                    code="test.finished",
                    severity="error",
                    stage="execution",
                ),
            ),
        )
    )


@pytest.mark.parametrize(
    ("intent", "options", "comparisons"),
    [
        (
            CompareIntent.ASSERTION,
            PlanningOptions(subjects=("ping",), environments=("staging",)),
            0,
        ),
        (
            CompareIntent.COMPARISON,
            PlanningOptions(
                subjects=("ping",),
                baseline="staging",
                candidates=("prod",),
            ),
            1,
        ),
        (
            CompareIntent.EXECUTION_PROFILE,
            PlanningOptions(execution_profile="release"),
            0,
        ),
    ],
)
def test_compare_prepares_every_supported_intent_without_exposing_retained_plan(
    tmp_path: Path,
    intent: CompareIntent,
    options: PlanningOptions,
    comparisons: int,
) -> None:
    service = CompareService(maximum_outstanding=3)
    result = service.prepare(_request(_write_project(tmp_path), intent, options))

    assert result.handle is not None
    assert result.review is not None
    assert result.review.plan.cardinality.comparisons == comparisons
    assert result.review.plan.http_authority is not None
    assert not result.diagnostics
    assert service.outstanding == 1
    assert "project" not in ComparePreparationResult.__struct_fields__
    assert _SECRET_CANARY not in msgspec.json.encode(result).decode()
    assert service.discard(result.handle)
    assert service.outstanding == 0


@pytest.mark.parametrize(
    ("intent", "options", "code"),
    [
        (
            CompareIntent.ASSERTION,
            PlanningOptions(
                subjects=("ping",),
                baseline="staging",
                candidates=("prod",),
            ),
            "compare.intent.assertion-topology-required",
        ),
        (
            CompareIntent.COMPARISON,
            PlanningOptions(subjects=("ping",), environments=("staging",)),
            "compare.intent.comparison-topology-required",
        ),
        (
            CompareIntent.ASSERTION,
            PlanningOptions(execution_profile="release"),
            "compare.intent.ad-hoc-profile-forbidden",
        ),
        (
            CompareIntent.COMPARISON,
            PlanningOptions(execution_profile="release"),
            "compare.intent.ad-hoc-profile-forbidden",
        ),
        (
            CompareIntent.EXECUTION_PROFILE,
            PlanningOptions(subjects=("ping",), environments=("staging",)),
            "compare.intent.execution-profile-required",
        ),
    ],
)
def test_compare_owns_intent_and_effective_topology_validation(
    tmp_path: Path,
    intent: CompareIntent,
    options: PlanningOptions,
    code: str,
) -> None:
    result = CompareService(maximum_outstanding=1).prepare(
        _request(_write_project(tmp_path), intent, options)
    )
    assert result.handle is None
    assert result.review is None
    assert tuple(item.code for item in result.diagnostics) == (code,)


def test_compare_start_consumes_the_exact_prepared_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def execute(_request: object) -> ProductionExecutionResult:
            return _failure()

        monkeypatch.setattr(execution_session, "execute_production", execute)
        service = CompareService(maximum_outstanding=1)
        prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
        assert prepared.handle is not None
        assert prepared.review is not None

        session = service.start(prepared.handle, _controls())
        assert service.outstanding == 0
        assert session.run_id == prepared.review.plan.run_id
        assert await session.result() == _failure()
        assert session.done
        with pytest.raises(CompareServiceError) as captured:
            service.start(prepared.handle, _controls())
        assert captured.value.code is CompareServiceErrorCode.HANDLE_UNKNOWN

    asyncio.run(scenario())


def test_compare_preview_uses_retained_plan_controls_and_admission_environment_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_environment = CapturedAmbientEnvironment(
        (("TOKEN", _AMBIENT_SECRET_CANARY),),
        limits=EnvironmentSourceLimits(),
    )
    capture_calls: list[tuple[object, object]] = []
    preview_requests: list[RenderPreviewWindowRequest] = []
    execution_requests: list[ProductionExecutionRequest] = []

    def capture(project: object, limits: object) -> CapturedAmbientEnvironment:
        capture_calls.append((project, limits))
        return captured_environment

    async def render(request: RenderPreviewWindowRequest) -> RenderPreviewWindowResult:
        preview_requests.append(request)
        return RenderPreviewWindowResult(total_executions=1)

    def start(request: ProductionExecutionRequest) -> object:
        execution_requests.append(request)
        return object()

    monkeypatch.setattr(compare, "capture_process_environment", capture)
    monkeypatch.setattr(compare, "render_prepared_request_window", render)
    monkeypatch.setattr(compare, "start_execution_session", start)
    service = CompareService(maximum_outstanding=1)
    controls = _controls()
    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None
    retained = service._prepared[prepared.handle]

    result = asyncio.run(
        service.preview_requests(
            prepared.handle,
            controls,
            start_ordinal=0,
            maximum_previews=7,
        )
    )

    assert result == RenderPreviewWindowResult(total_executions=1)
    assert service.outstanding == 1
    assert len(capture_calls) == 1
    assert capture_calls[0] == (retained.project, controls.settings.environment)
    assert len(preview_requests) == 1
    preview = preview_requests[0]
    assert preview.start_ordinal == 0
    assert preview.maximum_previews == 7
    assert preview.preview.project is retained.project
    assert preview.preview.ambient_environment == captured_environment
    assert preview.preview.settings.files == controls.settings.files
    assert preview.preview.settings.environment == controls.settings.environment
    assert preview.preview.settings.yaml == controls.settings.yaml
    assert preview.preview.settings.sanitization == controls.settings.artifact.limits.sanitization
    assert _AMBIENT_SECRET_CANARY not in repr(preview)

    session = service.start(prepared.handle, controls)
    assert type(session) is object
    assert service.outstanding == 0
    assert len(capture_calls) == 1
    assert len(execution_requests) == 1
    execution = execution_requests[0]
    assert execution.project is retained.project
    assert execution.settings is retained.controls.settings
    assert execution.ambient_environment == captured_environment
    assert _AMBIENT_SECRET_CANARY not in repr(execution)


def test_compare_preview_is_available_when_execution_checkpoint_is_blocked(
    tmp_path: Path,
) -> None:
    settings = production_execution_defaults_v1()
    controls = msgspec.structs.replace(
        _controls(),
        settings=msgspec.structs.replace(
            settings,
            artifact=msgspec.structs.replace(
                settings.artifact,
                persist=ArtifactPersistMode.ALWAYS,
            ),
        ),
    )
    request = msgspec.structs.replace(
        _assertion_request(_write_project(tmp_path)),
        controls=controls,
    )
    service = CompareService(maximum_outstanding=1)
    prepared = service.prepare(request)
    assert prepared.handle is not None
    assert prepared.review is not None
    assert not prepared.review.execution_allowed

    preview = asyncio.run(
        service.preview_requests(
            prepared.handle,
            controls,
            start_ordinal=0,
            maximum_previews=1,
        )
    )

    assert not preview.diagnostics
    assert len(preview.previews) == 1
    assert preview.previews[0].url == "https://staging.example.test/ping"
    assert preview.total_executions == 1
    assert preview.next_ordinal is None
    assert service.outstanding == 1
    with pytest.raises(CompareServiceError) as blocked:
        service.validate_start(prepared.handle, controls)
    assert blocked.value.code is CompareServiceErrorCode.PREPARATION_BLOCKED


def test_compare_resolved_inspection_uses_retained_plan_controls_and_frozen_ambient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_environment = CapturedAmbientEnvironment(
        (("TOKEN", _AMBIENT_SECRET_CANARY),),
        limits=EnvironmentSourceLimits(),
    )
    requests: list[ResolvedValueInspectionRequest] = []
    expected = ResolvedValueInspectionResult(
        diagnostics=(
            ApplicationDiagnostic(
                code="resolution.inspect.test",
                severity="error",
                stage="resolution",
            ),
        )
    )

    monkeypatch.setattr(
        compare,
        "capture_process_environment",
        lambda _project, _limits: captured_environment,
    )

    async def inspect(request: ResolvedValueInspectionRequest) -> ResolvedValueInspectionResult:
        requests.append(request)
        return expected

    monkeypatch.setattr(compare, "_inspect_prepared_resolved_value", inspect)
    settings = production_execution_defaults_v1()
    controls = msgspec.structs.replace(
        _controls(),
        settings=msgspec.structs.replace(
            settings,
            artifact=msgspec.structs.replace(
                settings.artifact,
                persist=ArtifactPersistMode.ALWAYS,
            ),
        ),
    )
    service = CompareService(maximum_outstanding=1)
    prepared = service.prepare(
        msgspec.structs.replace(
            _assertion_request(_write_project(tmp_path)),
            controls=controls,
        )
    )
    assert prepared.handle is not None
    assert prepared.review is not None
    assert prepared.review.execution_allowed is False
    target = _resolved_target(service, prepared.handle)

    result = asyncio.run(service.inspect_resolved_value(prepared.handle, controls, target))

    assert result is expected
    assert service.outstanding == 1
    assert len(requests) == 1
    request = requests[0]
    retained = service._prepared[prepared.handle]
    assert request.project is retained.project
    assert request.ambient_environment == captured_environment
    assert request.resource == target.resource
    assert request.field_path == target.field_path
    assert request.settings.files == controls.settings.files
    assert request.settings.environment == controls.settings.environment
    assert request.settings.yaml == controls.settings.yaml
    assert _AMBIENT_SECRET_CANARY not in repr(request)


@pytest.mark.parametrize("operation", ["preview", "resolved"])
@pytest.mark.parametrize("revocation", ["discard", "close"])
def test_compare_revocation_cancels_in_flight_inspections_and_blocks_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    revocation: str,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        released = asyncio.Event()

        async def render(_request: RenderPreviewWindowRequest) -> RenderPreviewWindowResult:
            entered.set()
            await released.wait()
            return RenderPreviewWindowResult(total_executions=1)

        async def inspect(
            _request: ResolvedValueInspectionRequest,
        ) -> ResolvedValueInspectionResult:
            entered.set()
            await released.wait()
            return ResolvedValueInspectionResult(
                diagnostics=(
                    ApplicationDiagnostic(
                        code="resolution.inspect.test",
                        severity="error",
                        stage="resolution",
                    ),
                )
            )

        monkeypatch.setattr(compare, "render_prepared_request_window", render)
        monkeypatch.setattr(compare, "_inspect_prepared_resolved_value", inspect)
        service = CompareService(maximum_outstanding=1)
        controls = _controls()
        prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
        assert prepared.handle is not None
        handle = prepared.handle
        task: asyncio.Task[RenderPreviewWindowResult] | asyncio.Task[ResolvedValueInspectionResult]
        if operation == "preview":
            task = asyncio.create_task(
                service.preview_requests(
                    handle,
                    controls,
                    start_ordinal=0,
                    maximum_previews=1,
                )
            )
        else:
            task = asyncio.create_task(
                service.inspect_resolved_value(
                    handle,
                    controls,
                    _resolved_target(service, handle),
                )
            )
        await entered.wait()
        with pytest.raises(CompareServiceError) as busy:
            service.validate_start(handle, controls)
        assert busy.value.code is CompareServiceErrorCode.PREPARATION_BUSY
        if revocation == "discard":
            assert service.discard(handle)
            assert service.discard(handle) is False
        else:
            service.close()
        assert service.outstanding == 1
        assert handle in service._prepared
        assert handle in service._inspections
        assert handle in service._revoked
        with pytest.raises(CompareServiceError) as revoked:
            await service.preview_requests(
                handle,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )
        expected = (
            CompareServiceErrorCode.HANDLE_UNKNOWN
            if revocation == "discard"
            else CompareServiceErrorCode.CLOSED
        )
        assert revoked.value.code is expected
        with pytest.raises(CompareServiceError) as cancelled:
            await task
        assert cancelled.value.code is CompareServiceErrorCode.INSPECTION_CANCELLED
        assert handle not in service._inspections
        assert handle not in service._revoked
        assert handle not in service._prepared
        assert service.outstanding == 0
        current = asyncio.current_task()
        assert current is not None
        service._end_inspection(
            handle,
            compare._InspectionLease(cast("asyncio.Task[object]", current)),
        )

    asyncio.run(scenario())


def test_compare_tracks_concurrent_inspection_leases_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = 0
        both_entered = asyncio.Event()
        released = asyncio.Event()

        async def render(_request: RenderPreviewWindowRequest) -> RenderPreviewWindowResult:
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await released.wait()
            return RenderPreviewWindowResult(total_executions=1)

        monkeypatch.setattr(compare, "render_prepared_request_window", render)
        service = CompareService(
            maximum_outstanding=1,
            maximum_inspections_per_preparation=2,
        )
        controls = _controls()
        prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
        assert prepared.handle is not None
        handle = prepared.handle
        tasks = tuple(
            asyncio.create_task(
                service.preview_requests(
                    handle,
                    controls,
                    start_ordinal=0,
                    maximum_previews=1,
                )
            )
            for _index in range(2)
        )

        await both_entered.wait()
        assert len(service._inspections[handle]) == 2
        with pytest.raises(CompareServiceError) as busy:
            await service.preview_requests(
                handle,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )
        assert busy.value.code is CompareServiceErrorCode.PREPARATION_BUSY
        released.set()
        await asyncio.gather(*tasks)

        assert handle not in service._inspections

    asyncio.run(scenario())


def test_compare_service_revocation_does_not_cancel_direct_await_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        child_task: asyncio.Task[object] | None = None

        async def render(_request: RenderPreviewWindowRequest) -> RenderPreviewWindowResult:
            nonlocal child_task
            child_task = cast("asyncio.Task[object]", asyncio.current_task())
            entered.set()
            await asyncio.Event().wait()
            return RenderPreviewWindowResult(total_executions=1)

        monkeypatch.setattr(compare, "render_prepared_request_window", render)
        service = CompareService(maximum_outstanding=1)
        controls = _controls()
        prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
        assert prepared.handle is not None
        handle = prepared.handle
        caller = asyncio.current_task()

        async def revoke() -> None:
            await entered.wait()
            assert service.discard(handle)

        revoker = asyncio.create_task(revoke())
        with pytest.raises(CompareServiceError) as cancelled:
            await service.preview_requests(
                handle,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )
        await revoker

        assert cancelled.value.code is CompareServiceErrorCode.INSPECTION_CANCELLED
        assert caller is not None
        assert child_task is not None
        assert child_task is not caller
        assert caller.cancelling() == 0
        assert service.outstanding == 0

    asyncio.run(scenario())


def test_compare_aclose_has_caller_owned_timeout_and_eventual_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        cancellation_seen = asyncio.Event()
        release = asyncio.Event()

        async def render(_request: RenderPreviewWindowRequest) -> RenderPreviewWindowResult:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_seen.set()
                await release.wait()
            return RenderPreviewWindowResult(total_executions=1)

        monkeypatch.setattr(compare, "render_prepared_request_window", render)
        service = CompareService(maximum_outstanding=1)
        controls = _controls()
        prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
        assert prepared.handle is not None
        caller = asyncio.create_task(
            service.preview_requests(
                prepared.handle,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )
        )
        await entered.wait()
        service.close()
        await cancellation_seen.wait()

        timed_out = await service.aclose(timeout=Duration(nanoseconds=0))
        assert timed_out.drained is False
        assert tuple(item.code for item in timed_out.diagnostics) == (
            "compare.service.close-timeout",
        )
        release.set()
        drained = await service.aclose(timeout=Duration(nanoseconds=10**10_000))
        assert drained.drained is True
        assert not drained.diagnostics
        with pytest.raises(CompareServiceError) as cancelled:
            await caller
        assert cancelled.value.code is CompareServiceErrorCode.INSPECTION_CANCELLED
        assert service.outstanding == 0

    asyncio.run(scenario())


def test_compare_close_contract_and_empty_service_are_closed_and_typed() -> None:
    diagnostic = ApplicationDiagnostic(code="close.timeout", severity="error", stage="runtime")
    with pytest.raises(TypeError, match="drained state"):
        CompareCloseResult(drained=cast("bool", object()))
    with pytest.raises(TypeError, match="diagnostics"):
        CompareCloseResult(
            drained=False,
            diagnostics=cast("tuple[ApplicationDiagnostic, ...]", (1,)),
        )
    with pytest.raises(ValueError, match="either drained state or diagnostics"):
        CompareCloseResult(drained=True, diagnostics=(diagnostic,))
    with pytest.raises(ValueError, match="either drained state or diagnostics"):
        CompareCloseResult(drained=False)

    service = CompareService(maximum_outstanding=1)
    with pytest.raises(TypeError, match="timeout must be Duration"):
        asyncio.run(service.aclose(timeout=cast("Duration", object())))
    outcome = asyncio.run(service.aclose(timeout=Duration(nanoseconds=0)))
    assert outcome.drained is True
    assert compare._finite_timeout_seconds(Duration(nanoseconds=10**10_000)) == float_info.max


def test_caller_cancellation_returns_promptly_and_retains_child_until_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        cancellation_seen = asyncio.Event()
        child_finished = asyncio.Event()
        release = asyncio.Event()

        async def render(_request: RenderPreviewWindowRequest) -> RenderPreviewWindowResult:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_seen.set()
                await release.wait()
                child_finished.set()
                raise RuntimeError("child failure after cancellation") from None
            raise AssertionError("test child unexpectedly resumed")

        monkeypatch.setattr(compare, "render_prepared_request_window", render)
        service = CompareService(maximum_outstanding=1)
        controls = _controls()
        prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
        assert prepared.handle is not None
        caller = asyncio.create_task(
            service.preview_requests(
                prepared.handle,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )
        )
        await entered.wait()
        caller.cancel()
        await cancellation_seen.wait()

        done, _pending = await asyncio.wait({caller}, timeout=0.1)
        assert done == {caller}
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert prepared.handle in service._inspections
        assert prepared.handle in service._prepared

        release.set()
        await child_finished.wait()
        await asyncio.sleep(0)
        assert prepared.handle not in service._inspections
        assert prepared.handle in service._prepared
        assert service.discard(prepared.handle) is True

    asyncio.run(scenario())


def test_stale_suppressed_cancellation_does_not_mask_service_child_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        suppress_first = asyncio.Event()
        entered = asyncio.Event()

        async def render(_request: RenderPreviewWindowRequest) -> RenderPreviewWindowResult:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("test child unexpectedly resumed")

        monkeypatch.setattr(compare, "render_prepared_request_window", render)
        service = CompareService(maximum_outstanding=1)
        controls = _controls()
        prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
        assert prepared.handle is not None
        handle = prepared.handle

        async def orchestrate() -> RenderPreviewWindowResult:
            try:
                suppress_first.set()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            return await service.preview_requests(
                handle,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )

        caller = asyncio.create_task(orchestrate())
        await suppress_first.wait()
        caller.cancel()
        await entered.wait()
        assert caller.cancelling() == 1

        service.close()

        with pytest.raises(CompareServiceError) as cancelled:
            await caller
        assert cancelled.value.code is CompareServiceErrorCode.INSPECTION_CANCELLED
        assert handle not in service._inspections
        assert handle not in service._prepared

    asyncio.run(scenario())


def test_new_caller_cancellation_wins_a_simultaneous_service_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def render(_request: RenderPreviewWindowRequest) -> RenderPreviewWindowResult:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            return RenderPreviewWindowResult(total_executions=1)

        monkeypatch.setattr(compare, "render_prepared_request_window", render)
        service = CompareService(maximum_outstanding=1)
        controls = _controls()
        prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
        assert prepared.handle is not None
        caller = asyncio.create_task(
            service.preview_requests(
                prepared.handle,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )
        )
        await entered.wait()

        service.close()
        caller.cancel()

        done, _pending = await asyncio.wait({caller}, timeout=0.1)
        assert done == {caller}
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert prepared.handle in service._inspections
        assert prepared.handle in service._prepared

        release.set()
        await service.aclose(timeout=Duration(nanoseconds=1_000_000_000))
        await asyncio.sleep(0)
        assert prepared.handle not in service._inspections
        assert prepared.handle not in service._prepared

    asyncio.run(scenario())


def test_compare_ambient_snapshot_repr_and_nested_debug_state_are_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        compare,
        "capture_process_environment",
        lambda _project, _limits: CapturedAmbientEnvironment(
            (("TOKEN", _AMBIENT_SECRET_CANARY),),
            limits=EnvironmentSourceLimits(),
        ),
    )
    service = CompareService(maximum_outstanding=1)
    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None
    retained = service._prepared[prepared.handle]
    snapshot = retained.ambient_environment
    sealed = snapshot._values

    assert _AMBIENT_SECRET_CANARY not in repr(retained)
    assert repr(snapshot) == "<captured-ambient-environment>"
    assert _AMBIENT_SECRET_CANARY not in repr(sealed)
    assert str(sealed) == "<sensitive>"


def test_inspection_lease_cancellation_is_idempotent() -> None:
    async def scenario() -> None:
        task = asyncio.create_task(asyncio.sleep(60))
        lease = compare._InspectionLease(cast("asyncio.Task[object]", task))
        assert lease.cancel() is True
        assert lease.cancel() is False
        with pytest.raises(asyncio.CancelledError):
            await task
        assert lease.cancel() is False

    asyncio.run(scenario())


def test_compare_preview_validates_retained_authority_without_consuming_it(
    tmp_path: Path,
) -> None:
    service = CompareService(maximum_outstanding=1)
    controls = _controls()
    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None
    unknown = ComparePreparationHandle(value=str(_HANDLE_ID))
    mismatched = msgspec.structs.replace(controls, tool_version="different")

    with pytest.raises(CompareServiceError) as missing:
        asyncio.run(
            service.preview_requests(
                unknown,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )
        )
    assert missing.value.code is CompareServiceErrorCode.HANDLE_UNKNOWN
    with pytest.raises(CompareServiceError) as mismatch:
        asyncio.run(
            service.preview_requests(
                prepared.handle,
                mismatched,
                start_ordinal=0,
                maximum_previews=1,
            )
        )
    assert mismatch.value.code is CompareServiceErrorCode.PREPARATION_MISMATCH
    assert service.outstanding == 1

    service.close()
    with pytest.raises(CompareServiceError) as closed:
        asyncio.run(
            service.preview_requests(
                prepared.handle,
                controls,
                start_ordinal=0,
                maximum_previews=1,
            )
        )
    assert closed.value.code is CompareServiceErrorCode.CLOSED


def test_compare_start_rejects_an_artifact_root_not_shown_by_the_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def execute(_request: object) -> ProductionExecutionResult:
            return _failure()

        monkeypatch.setattr(execution_session, "execute_production", execute)
        service = CompareService(maximum_outstanding=1)
        inspected_root = (tmp_path / "inspected").resolve()
        different_root = (tmp_path / "different").resolve()
        request = _assertion_request(_write_project(tmp_path))
        request = msgspec.structs.replace(
            request,
            controls=msgspec.structs.replace(request.controls, artifact_root=inspected_root),
        )
        prepared = service.prepare(request)
        assert prepared.handle is not None
        controls = msgspec.structs.replace(_controls(), artifact_root=different_root)

        with pytest.raises(CompareServiceError) as captured:
            service.start(prepared.handle, controls)
        assert captured.value.code is CompareServiceErrorCode.PREPARATION_MISMATCH
        assert service.outstanding == 1

        session = service.start(
            prepared.handle,
            msgspec.structs.replace(_controls(), artifact_root=inspected_root),
        )
        assert service.outstanding == 0
        assert await session.result() == _failure()

    asyncio.run(scenario())


def test_compare_bounds_discards_and_closes_outstanding_preparations(tmp_path: Path) -> None:
    config = _write_project(tmp_path)
    service = CompareService(maximum_outstanding=1)
    prepared = service.prepare(_assertion_request(config))
    assert prepared.handle is not None
    limited = service.prepare(_assertion_request(config))
    assert tuple(item.code for item in limited.diagnostics) == (
        "compare.preparation.limit-exceeded",
    )
    unknown = ComparePreparationHandle(value=str(_HANDLE_ID))
    assert not service.discard(unknown)
    assert service.discard(prepared.handle)
    replacement = service.prepare(_assertion_request(config))
    assert replacement.handle is not None
    service.close()
    service.close()
    assert service.closed
    assert service.outstanding == 0
    assert not service.discard(replacement.handle)
    closed = service.prepare(_assertion_request(config))
    assert tuple(item.code for item in closed.diagnostics) == ("compare.service.closed",)
    with pytest.raises(CompareServiceError) as captured:
        service.start(replacement.handle, _controls())
    assert captured.value.code is CompareServiceErrorCode.CLOSED


def test_compare_contains_project_failures_and_handle_allocation_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = CompareService(maximum_outstanding=1).prepare(
        _assertion_request(tmp_path / "missing.yaml")
    )
    assert missing.diagnostics
    assert {item.stage for item in missing.diagnostics} == {"configuration"}

    config = _write_project(tmp_path)
    service = CompareService(maximum_outstanding=2)
    monkeypatch.setattr(compare, "uuid4", lambda: _HANDLE_ID)
    first = service.prepare(_assertion_request(config))
    assert first.handle == ComparePreparationHandle(value=str(_HANDLE_ID))
    exhausted = service.prepare(_assertion_request(config))
    assert tuple(item.code for item in exhausted.diagnostics) == (
        "compare.preparation.handle-unavailable",
    )

    def fail_safely(_request: object) -> object:
        raise RuntimeError(_SECRET_CANARY)

    another = CompareService(maximum_outstanding=1)
    monkeypatch.setattr(compare, "prepare_project_plan", fail_safely)
    failed = another.prepare(_assertion_request(config))
    assert tuple(item.code for item in failed.diagnostics) == ("compare.preparation.internal",)
    assert _SECRET_CANARY not in msgspec.json.encode(failed).decode()


def test_compare_contains_ambient_capture_failure_without_retaining_a_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_safely(*_args: object) -> tuple[tuple[str, str], ...]:
        raise RuntimeError(_SECRET_CANARY)

    monkeypatch.setattr(compare, "capture_process_environment", fail_safely)
    service = CompareService(maximum_outstanding=1)
    failed = service.prepare(_assertion_request(_write_project(tmp_path)))

    assert failed.handle is None
    assert failed.review is None
    assert tuple(item.code for item in failed.diagnostics) == (
        "compare.preparation.environment-capture-failed",
    )
    assert service.outstanding == 0
    assert _SECRET_CANARY not in msgspec.json.encode(failed).decode()


@pytest.mark.parametrize("value", [0, True])
def test_compare_service_rejects_invalid_limits(value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        CompareService(maximum_outstanding=cast("int", value))
    with pytest.raises(ValueError, match="positive integer"):
        CompareService(
            maximum_outstanding=1,
            maximum_inspections_per_preparation=cast("int", value),
        )


def test_compare_service_error_requires_typed_code() -> None:
    error = CompareServiceError(CompareServiceErrorCode.CLOSED)
    assert str(error) == "closed"
    with pytest.raises(TypeError, match="code must be typed"):
        CompareServiceError(cast("CompareServiceErrorCode", "closed"))


@pytest.mark.parametrize("value", [1, "", "x" * 65])
def test_compare_handle_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="bounded non-empty"):
        ComparePreparationHandle(value=cast("str", value))


def test_compare_prepare_request_rejects_untyped_values(tmp_path: Path) -> None:
    project = _project_request(
        _write_project(tmp_path),
        PlanningOptions(subjects=("ping",), environments=("staging",)),
    )
    with pytest.raises(TypeError, match="intent"):
        ComparePrepareRequest(
            intent=cast("CompareIntent", "assertion"),
            project=project,
            controls=_controls(),
        )
    with pytest.raises(TypeError, match="project request"):
        ComparePrepareRequest(
            intent=CompareIntent.ASSERTION,
            project=cast("ProjectPlanRequest", object()),
            controls=_controls(),
        )
    with pytest.raises(TypeError, match="controls"):
        ComparePrepareRequest(
            intent=CompareIntent.ASSERTION,
            project=project,
            controls=cast("CompareExecutionControls", object()),
        )


def test_compare_preparation_result_rejects_invalid_state(tmp_path: Path) -> None:
    prepared = CompareService(maximum_outstanding=1).prepare(
        _assertion_request(_write_project(tmp_path))
    )
    assert prepared.handle is not None
    assert prepared.review is not None
    diagnostic = ApplicationDiagnostic(code="test", severity="error", stage="invocation")
    with pytest.raises(TypeError, match="handle"):
        ComparePreparationResult(handle=cast("ComparePreparationHandle", object()))
    with pytest.raises(TypeError, match="review"):
        ComparePreparationResult(review=cast("PlanReviewInspection", object()))
    with pytest.raises(TypeError, match="diagnostics"):
        ComparePreparationResult(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", []))
    with pytest.raises(TypeError, match="diagnostics"):
        ComparePreparationResult(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", (object(),)))
    invalid_results: tuple[Callable[[], ComparePreparationResult], ...] = (
        lambda: ComparePreparationResult(),
        lambda: ComparePreparationResult(handle=prepared.handle),
        lambda: ComparePreparationResult(review=prepared.review),
        lambda: ComparePreparationResult(
            handle=prepared.handle,
            review=prepared.review,
            diagnostics=(diagnostic,),
        ),
    )
    for invalid in invalid_results:
        with pytest.raises(ValueError, match="exactly"):
            invalid()
    assert ComparePreparationResult(diagnostics=(diagnostic,)).diagnostics == (diagnostic,)


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"settings": object()}, TypeError, "settings"),
        ({"artifact_root": object()}, TypeError, "artifact root"),
        ({"artifact_root": Path("relative")}, ValueError, "resolved and absolute"),
        ({"approve_mutations": 1}, TypeError, "approval"),
        ({"tool_version": 1}, ValueError, "tool version"),
        ({"tool_version": ""}, ValueError, "tool version"),
        ({"tool_version": "x" * 65}, ValueError, "tool version"),
        ({"annotations": object()}, TypeError, "annotations"),
    ],
)
def test_compare_execution_controls_reject_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    values: dict[str, object] = {
        "settings": production_execution_defaults_v1(),
        "artifact_root": None,
        "approve_mutations": False,
        "tool_version": "test",
        "annotations": None,
    }
    values.update(changes)
    with pytest.raises(error, match=message):
        CompareExecutionControls(**values)  # type: ignore[arg-type]


def test_compare_start_forwards_run_annotations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotations = ProductionRunAnnotations(
        title="Release comparison",
        notes="Investigate any drift",
        tags=("release",),
    )
    controls = msgspec.structs.replace(_controls(), annotations=annotations)
    request = msgspec.structs.replace(
        _assertion_request(_write_project(tmp_path)),
        controls=controls,
    )
    service = CompareService(maximum_outstanding=1)
    prepared = service.prepare(request)
    assert prepared.handle is not None
    captured: list[ProductionExecutionRequest] = []

    def start(production: ProductionExecutionRequest) -> execution_session.ExecutionSession:
        captured.append(production)
        return cast("execution_session.ExecutionSession", object())

    monkeypatch.setattr(compare, "start_execution_session", start)
    assert service.start(prepared.handle, controls) is not None
    assert captured[0].annotations == annotations


def test_compare_operations_reject_untyped_inputs(tmp_path: Path) -> None:
    service = CompareService(maximum_outstanding=1)
    with pytest.raises(TypeError, match="ComparePrepareRequest"):
        service.prepare(cast("ComparePrepareRequest", object()))
    with pytest.raises(TypeError, match="start handle"):
        service.start(cast("ComparePreparationHandle", object()), _controls())
    with pytest.raises(TypeError, match="start controls"):
        service.start(
            ComparePreparationHandle(value=str(_HANDLE_ID)),
            cast("CompareExecutionControls", object()),
        )
    with pytest.raises(TypeError, match="discard handle"):
        service.discard(cast("ComparePreparationHandle", object()))
    with pytest.raises(TypeError, match="preview handle"):
        asyncio.run(
            service.preview_requests(
                cast("ComparePreparationHandle", object()),
                _controls(),
                start_ordinal=0,
                maximum_previews=1,
            )
        )
    with pytest.raises(TypeError, match="preview controls"):
        asyncio.run(
            service.preview_requests(
                ComparePreparationHandle(value=str(_HANDLE_ID)),
                cast("CompareExecutionControls", object()),
                start_ordinal=0,
                maximum_previews=1,
            )
        )
    with pytest.raises(TypeError, match="resolved inspection handle"):
        asyncio.run(
            service.inspect_resolved_value(
                cast("ComparePreparationHandle", object()),
                _controls(),
                cast("ResolvedValueInspectionTarget", object()),
            )
        )
    with pytest.raises(TypeError, match="resolved inspection controls"):
        asyncio.run(
            service.inspect_resolved_value(
                ComparePreparationHandle(value=str(_HANDLE_ID)),
                cast("CompareExecutionControls", object()),
                cast("ResolvedValueInspectionTarget", object()),
            )
        )
    with pytest.raises(TypeError, match="resolved inspection target"):
        asyncio.run(
            service.inspect_resolved_value(
                ComparePreparationHandle(value=str(_HANDLE_ID)),
                _controls(),
                cast("ResolvedValueInspectionTarget", object()),
            )
        )
    with pytest.raises(TypeError, match="occurrence handle"):
        service.list_occurrences(
            cast("ComparePreparationHandle", object()),
            _controls(),
            start_ordinal=0,
            maximum_occurrences=1,
        )
    with pytest.raises(TypeError, match="occurrence controls"):
        service.list_occurrences(
            ComparePreparationHandle(value=str(_HANDLE_ID)),
            cast("CompareExecutionControls", object()),
            start_ordinal=0,
            maximum_occurrences=1,
        )
    with pytest.raises(TypeError, match="rule handle"):
        service.list_rules(
            cast("ComparePreparationHandle", object()),
            _controls(),
            start_ordinal=0,
            maximum_rules=1,
        )
    with pytest.raises(TypeError, match="rule controls"):
        service.list_rules(
            ComparePreparationHandle(value=str(_HANDLE_ID)),
            cast("CompareExecutionControls", object()),
            start_ordinal=0,
            maximum_rules=1,
        )
    with pytest.raises(TypeError, match="start handle"):
        service.validate_start(cast("ComparePreparationHandle", object()), _controls())
    with pytest.raises(TypeError, match="start controls"):
        service.validate_start(
            ComparePreparationHandle(value=str(_HANDLE_ID)),
            cast("CompareExecutionControls", object()),
        )

    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None
    service.validate_start(prepared.handle, _controls())
    assert service.outstanding == 1
    with pytest.raises(CompareServiceError) as captured:
        service.start(ComparePreparationHandle(value=str(_HANDLE_ID)), _controls())
    assert captured.value.code is CompareServiceErrorCode.HANDLE_UNKNOWN


def test_compare_lists_canonical_occurrences_without_resolving_requests(tmp_path: Path) -> None:
    service = CompareService(maximum_outstanding=1)
    controls = _controls()
    prepared = service.prepare(
        _request(
            _write_project(tmp_path),
            CompareIntent.COMPARISON,
            PlanningOptions(
                subjects=("ping",),
                baseline="staging",
                candidates=("prod",),
                trials=2,
            ),
        )
    )
    assert prepared.handle is not None
    assert prepared.review is not None

    first = service.list_occurrences(
        prepared.handle,
        controls,
        start_ordinal=0,
        maximum_occurrences=3,
    )
    assert type(first) is PlanOccurrenceWindow
    assert first.plan_fingerprint == prepared.review.plan.fingerprint
    assert first.total_occurrences == 4
    assert tuple(item.ordinal for item in first.occurrences) == (0, 1, 2)
    assert tuple(item.environment.resource_id for item in first.occurrences) == (
        "staging",
        "prod",
        "staging",
    )
    assert all(item.subject.resource_id == "ping" for item in first.occurrences)
    assert tuple(item.trial_ordinal for item in first.occurrences) == (0, 0, 1)
    assert first.next_ordinal == 3

    final = service.list_occurrences(
        prepared.handle,
        controls,
        start_ordinal=first.next_ordinal,
        maximum_occurrences=3,
    )
    assert tuple(item.ordinal for item in final.occurrences) == (3,)
    assert final.next_ordinal is None
    assert service.outstanding == 1


def test_compare_lists_exact_pre_run_rules_without_consuming_handle(tmp_path: Path) -> None:
    service = CompareService(maximum_outstanding=1)
    controls = _controls()
    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None

    rules = service.list_rules(
        prepared.handle,
        controls,
        start_ordinal=0,
        maximum_rules=10,
    )

    assert type(rules) is PlanRuleWindow
    assert rules.total_rules == 0
    assert rules.rules == ()
    assert service.outstanding == 1


@pytest.mark.parametrize(
    ("start_ordinal", "maximum_occurrences", "message"),
    [
        (-1, 1, "start"),
        (True, 1, "start"),
        (0, 0, "page size"),
        (0, True, "page size"),
    ],
)
def test_compare_occurrence_catalog_rejects_invalid_windows(
    tmp_path: Path,
    start_ordinal: object,
    maximum_occurrences: object,
    message: str,
) -> None:
    service = CompareService(maximum_outstanding=1)
    controls = _controls()
    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None

    with pytest.raises(ValueError, match=message):
        service.list_occurrences(
            prepared.handle,
            controls,
            start_ordinal=cast("int", start_ordinal),
            maximum_occurrences=cast("int", maximum_occurrences),
        )
    assert service.outstanding == 1


@pytest.mark.parametrize(
    ("start_ordinal", "maximum_previews", "message"),
    [
        (-1, 1, "start ordinal"),
        (True, 1, "start ordinal"),
        (0, 0, "window size"),
        (0, True, "window size"),
    ],
)
def test_compare_preview_rejects_invalid_page_bounds_without_consuming_handle(
    tmp_path: Path,
    start_ordinal: object,
    maximum_previews: object,
    message: str,
) -> None:
    service = CompareService(maximum_outstanding=1)
    controls = _controls()
    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None

    with pytest.raises(ValueError, match=message):
        asyncio.run(
            service.preview_requests(
                prepared.handle,
                controls,
                start_ordinal=cast("int", start_ordinal),
                maximum_previews=cast("int", maximum_previews),
            )
        )
    assert service.outstanding == 1


def test_compare_blocks_start_when_review_requires_missing_artifact_root(tmp_path: Path) -> None:
    config = _write_project(tmp_path)
    settings = production_execution_defaults_v1()
    controls = msgspec.structs.replace(
        _controls(),
        settings=msgspec.structs.replace(
            settings,
            artifact=msgspec.structs.replace(
                settings.artifact,
                persist=ArtifactPersistMode.ALWAYS,
            ),
        ),
    )
    request = msgspec.structs.replace(_assertion_request(config), controls=controls)
    service = CompareService(maximum_outstanding=1)
    prepared = service.prepare(request)
    assert prepared.handle is not None
    assert prepared.review is not None
    assert not prepared.review.execution_allowed

    with pytest.raises(CompareServiceError) as blocked:
        service.validate_start(prepared.handle, controls)
    assert blocked.value.code is CompareServiceErrorCode.PREPARATION_BLOCKED
    assert service.outstanding == 1


def test_retained_comparison_contract_rejects_untyped_components(tmp_path: Path) -> None:
    service = CompareService(maximum_outstanding=1)
    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None
    retained = service._prepared[prepared.handle]
    for changes, message in (
        ({"project": object()}, "project"),
        ({"controls": object()}, "controls"),
        ({"review": object()}, "review"),
        ({"ambient_environment": []}, "ambient environment"),
        ({"ambient_environment": (("NAME", object()),)}, "ambient environment"),
    ):
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(retained, **changes)


def test_compare_admission_surfaces_each_backend_preflight_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _assertion_request(_write_project(tmp_path))
    outcome = prepare_project_plan(request.project)
    assert outcome.project is not None
    diagnostic = ApplicationDiagnostic(code="test.rejected", severity="error", stage="execution")
    monkeypatch.setattr(compare, "inspect_diagnostic", lambda _item: diagnostic)

    with monkeypatch.context() as context:
        context.setattr(compare, "inspect_diagnostic", lambda _item: diagnostic)
        context.setattr(
            compare,
            "preflight_runtime_capabilities",
            lambda _project: SimpleNamespace(supported=False, diagnostics=(object(),)),
        )
        assert compare._admit_review(request, outcome.project) == (diagnostic,)

    with monkeypatch.context() as context:
        context.setattr(compare, "inspect_diagnostic", lambda _item: diagnostic)
        context.setattr(
            compare,
            "bind_execution_profiles",
            lambda *_args, **_kwargs: SimpleNamespace(profiles=None, diagnostics=(object(),)),
        )
        assert compare._admit_review(request, outcome.project) == (diagnostic,)

    with monkeypatch.context() as context:
        context.setattr(compare, "inspect_diagnostic", lambda _item: diagnostic)
        context.setattr(
            compare,
            "preflight_configuration_evaluation",
            lambda *_args, **_kwargs: SimpleNamespace(
                supported=False,
                diagnostics=(object(),),
            ),
        )
        assert compare._admit_review(request, outcome.project) == (diagnostic,)
