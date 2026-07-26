"""Unified interactive Compare preparation and start facade tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application import compare
from comparo.application import execution_session
from comparo.application.compare import CompareExecutionControls
from comparo.application.compare import CompareIntent
from comparo.application.compare import ComparePreparationHandle
from comparo.application.compare import ComparePreparationResult
from comparo.application.compare import ComparePrepareRequest
from comparo.application.compare import CompareService
from comparo.application.compare import CompareServiceError
from comparo.application.compare import CompareServiceErrorCode
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.plan_review import PlanReviewInspection
from comparo.application.planning import PlanningOptions
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.production_execution import ProductionExecutionFailure
from comparo.application.production_execution import ProductionExecutionFailureKind
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.production_execution import production_execution_defaults_v1

_INVOCATION_ID = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("20000000-0000-0000-0000-000000000002")
_HANDLE_ID = UUID("30000000-0000-0000-0000-000000000003")
_SECRET_CANARY = "compare-secret-canary"


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


@pytest.mark.parametrize("value", [0, True])
def test_compare_service_rejects_invalid_limits(value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        CompareService(maximum_outstanding=cast("int", value))


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
        ({"approve_mutations": 1}, TypeError, "approval"),
        ({"tool_version": 1}, ValueError, "tool version"),
        ({"tool_version": ""}, ValueError, "tool version"),
        ({"tool_version": "x" * 65}, ValueError, "tool version"),
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
    }
    values.update(changes)
    with pytest.raises(error, match=message):
        CompareExecutionControls(**values)  # type: ignore[arg-type]


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

    prepared = service.prepare(_assertion_request(_write_project(tmp_path)))
    assert prepared.handle is not None
    with pytest.raises(CompareServiceError) as captured:
        service.start(ComparePreparationHandle(value=str(_HANDLE_ID)), _controls())
    assert captured.value.code is CompareServiceErrorCode.HANDLE_UNKNOWN
