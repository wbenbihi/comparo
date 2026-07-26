"""Production TUI backend delegation, lifecycle, and failure-containment tests."""

from __future__ import annotations

import ast
import asyncio
import threading
import traceback
from collections.abc import Awaitable
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from comparo.application import ApplicationDiagnostic
from comparo.application import BudgetOverride
from comparo.application import CompareExecutionControls
from comparo.application import CompareIntent
from comparo.application import ComparePreparationHandle
from comparo.application import ComparePreparationResult
from comparo.application import ComparePrepareRequest
from comparo.application import CompareService
from comparo.application import CompareServiceError
from comparo.application import CompareServiceErrorCode
from comparo.application import ExecutionSession
from comparo.application import MatrixOverride
from comparo.application import PlanningOptions
from comparo.application import ProductionExecutionSettings
from comparo.application import ProjectWorkspaceRequest
from comparo.application import ProjectWorkspaceResult
from comparo.application import ProjectWorkspaceSnapshot
from comparo.application import ReportArchiveError
from comparo.application import ReportArchiveErrorCode
from comparo.application import ReportArchiveLimits
from comparo.application import ReportArchiveListing
from comparo.application import ResultProjection
from comparo.application import ValueResourceOverride
from comparo.application import persisted_production_execution_defaults_v1
from comparo.application import production_execution_defaults_v1
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import SourceCoordinate
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.project_workspace import ProjectSourceSummary
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.value_references import ValueNamespace
from comparo.tui import backend as backend_module
from comparo.tui.backend import DEFAULT_TUI_ARTIFACT_READ_LIMITS
from comparo.tui.backend import DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS
from comparo.tui.backend import DEFAULT_TUI_REPORT_ARCHIVE_LIMITS
from comparo.tui.backend import ProductionTuiBackend
from comparo.tui.backend import default_tui_execution_controls
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareMode
from comparo.tui.contracts import TuiBackend
from comparo.tui.contracts import TuiBackendError
from comparo.tui.contracts import TuiBackendErrorCode
from comparo.tui.contracts import TuiExecutionControls
from comparo.tui.contracts import TuiLaunchRequest
from comparo.tui.contracts import tui_launch_limits_defaults_v1

_INVOCATION_ID = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("20000000-0000-0000-0000-000000000002")
_HANDLE = ComparePreparationHandle(value="30000000-0000-0000-0000-000000000003")
_SECRET = "backend-private-path-and-exception-canary"


def _diagnostic(code: str = "test") -> ApplicationDiagnostic:
    return ApplicationDiagnostic(code=code, severity="error", stage="test")


def _project_failure() -> ProjectWorkspaceResult:
    return ProjectWorkspaceResult(diagnostics=(_diagnostic("project"),))


def _prepare_failure() -> ComparePreparationResult:
    return ComparePreparationResult(diagnostics=(_diagnostic("prepare"),))


def _project_success(tmp_path: Path) -> ProjectWorkspaceResult:
    coordinate = ResourceCoordinate(
        namespace="local",
        kind="Project",
        resource_id="demo",
        fingerprint="a" * 64,
    )
    project = ProjectResourceSummary(
        coordinate=coordinate,
        name="Demo",
        description=None,
        tags=(),
        source=SourceCoordinate(
            source="project.yaml",
            document=1,
            line=1,
            column=1,
        ),
    )
    return ProjectWorkspaceResult(
        snapshot=ProjectWorkspaceSnapshot(
            api_version="comparo/v1alpha1",
            namespace="local",
            project=project,
            resources=(),
        ),
        sources=(
            ProjectSourceSummary(
                source_id="project.yaml",
                entry=True,
                resources=(coordinate,),
            ),
        ),
    )


def _unclassified_compare_service_error() -> CompareServiceError:
    error = CompareServiceError(CompareServiceErrorCode.CLOSED)
    error.code = cast("CompareServiceErrorCode", object())
    return error


def _controls() -> TuiExecutionControls:
    return TuiExecutionControls(
        settings=production_execution_defaults_v1(),
        approve_mutations=False,
        tool_version="test",
    )


def _launch(tmp_path: Path) -> TuiLaunchRequest:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700, exist_ok=True)
    return TuiLaunchRequest(
        config=tmp_path / "project" / "comparo.yaml",
        additional_sources=(tmp_path / "extra.yaml",),
        policy=tmp_path / "operator-policy.yaml",
        artifact_root=artifact_root,
    )


def _backend(tmp_path: Path) -> ProductionTuiBackend:
    value = ProductionTuiBackend(_launch(tmp_path))
    typed: TuiBackend = value
    assert typed is value
    return value


def test_backend_uses_only_application_facades() -> None:
    source = Path(backend_module.__file__).read_text(encoding="utf-8")
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "verify_artifact" in imported
    assert "project_results" in imported
    assert not any(
        module.startswith(
            (
                "comparo.adapters",
                "comparo.configuration",
                "comparo.core",
                "comparo.cli",
            )
        )
        for module in modules
    )


def test_backend_defaults_are_explicit_and_bounded() -> None:
    assert DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS == 8
    assert DEFAULT_TUI_REPORT_ARCHIVE_LIMITS.maximum_entries == 4096
    assert DEFAULT_TUI_REPORT_ARCHIVE_LIMITS.artifact is DEFAULT_TUI_ARTIFACT_READ_LIMITS
    assert DEFAULT_TUI_ARTIFACT_READ_LIMITS.maximum_blobs == 4096
    assert DEFAULT_TUI_ARTIFACT_READ_LIMITS.maximum_blob_bytes == 64 * 1024 * 1024
    assert DEFAULT_TUI_ARTIFACT_READ_LIMITS.maximum_total_blob_bytes == 512 * 1024 * 1024
    assert DEFAULT_TUI_ARTIFACT_READ_LIMITS.maximum_manifest_bytes == 64 * 1024 * 1024


def test_default_execution_controls_persist_for_report_without_mutation_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "version", lambda _distribution: "1.2.3")
    controls = default_tui_execution_controls()
    assert controls.settings == persisted_production_execution_defaults_v1()
    assert controls.settings != production_execution_defaults_v1()
    assert not controls.approve_mutations
    assert controls.tool_version == "1.2.3"


def test_default_execution_controls_use_safe_version_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_distribution: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(backend_module, "version", missing)
    assert default_tui_execution_controls().tool_version == "unknown"


def test_default_execution_controls_reject_untyped_settings() -> None:
    with pytest.raises(TypeError, match="ProductionExecutionSettings"):
        default_tui_execution_controls(cast("ProductionExecutionSettings", object()))


def test_project_load_runs_off_loop_with_launch_owned_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        caller = threading.get_ident()
        captured: list[tuple[ProjectWorkspaceRequest, int]] = []

        def inspect(request: ProjectWorkspaceRequest) -> ProjectWorkspaceResult:
            captured.append((request, threading.get_ident()))
            return _project_failure()

        monkeypatch.setattr(backend_module, "inspect_project_workspace", inspect)
        assert await backend.load_project() == _project_failure()
        request, worker = captured[0]
        assert request.config == _launch(tmp_path).config
        assert request.additional_sources == _launch(tmp_path).additional_sources
        assert worker != caller
        await backend.close()

    asyncio.run(scenario())


def test_successful_project_load_treats_missing_relative_archive_as_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        project_root = tmp_path / "project"
        project_root.mkdir(mode=0o700)
        launch = TuiLaunchRequest(
            config=project_root / "comparo.yaml",
            artifact_root=Path(".comparo/artifacts"),
        )
        backend = ProductionTuiBackend(launch)
        result = _project_success(tmp_path)
        monkeypatch.setattr(backend_module, "inspect_project_workspace", lambda _request: result)
        assert await backend.load_project() is result
        archive = project_root / ".comparo" / "artifacts"
        assert not archive.exists()
        assert await backend.list_reports() == ReportArchiveListing(entries=(), failures=())
        assert not archive.exists()
        await backend.close()

    asyncio.run(scenario())


def test_absolute_archive_is_already_initialized_after_successful_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        result = _project_success(tmp_path)
        monkeypatch.setattr(backend_module, "inspect_project_workspace", lambda _request: result)
        assert await backend.load_project() is result
        await backend.close()

    asyncio.run(scenario())


def test_relative_archive_is_unavailable_until_project_load(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = ProductionTuiBackend(
            TuiLaunchRequest(
                config=tmp_path / "project" / "comparo.yaml",
                artifact_root=Path("reports"),
            )
        )
        with pytest.raises(TuiBackendError) as caught:
            await backend.list_reports()
        assert caught.value.code is TuiBackendErrorCode.REPORT_LIST_UNAVAILABLE
        await backend.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("draft", "intent", "options"),
    [
        (
            CompareDraft(
                mode=CompareMode.ASSERTIONS,
                subjects=("ping",),
                environments=("staging",),
                execution_profile="hidden-profile",
            ),
            CompareIntent.ASSERTION,
            PlanningOptions(subjects=("ping",), environments=("staging",)),
        ),
        (
            CompareDraft(
                mode=CompareMode.DIFFERENCES,
                subjects=("ping",),
                baseline="staging",
                candidates=("production",),
                execution_profile="hidden-profile",
            ),
            CompareIntent.COMPARISON,
            PlanningOptions(
                subjects=("ping",),
                baseline="staging",
                candidates=("production",),
            ),
        ),
        (
            CompareDraft(
                mode=CompareMode.EXECUTION_PROFILE,
                execution_profile="release",
                subjects=("ping", "health"),
                baseline="staging",
                candidates=("production", "canary"),
                matrices=(MatrixOverride(binding="region", matrix="regions"),),
                trials=3,
                maximum_attempts=2,
                fail_fast=False,
                budgets=(BudgetOverride(resource=BudgetResource.CONCURRENCY, limit=4),),
                value_resources=(
                    ValueResourceOverride(
                        namespace=ValueNamespace.PARAMETERS,
                        environment=None,
                        resource="Value/params",
                    ),
                ),
            ),
            CompareIntent.EXECUTION_PROFILE,
            PlanningOptions(
                execution_profile="release",
                subjects=("ping", "health"),
                baseline="staging",
                candidates=("production", "canary"),
                matrices=(MatrixOverride(binding="region", matrix="regions"),),
                trials=3,
                maximum_attempts=2,
                fail_fast=False,
                budgets=(BudgetOverride(resource=BudgetResource.CONCURRENCY, limit=4),),
                value_resources=(
                    ValueResourceOverride(
                        namespace=ValueNamespace.PARAMETERS,
                        environment=None,
                        resource="Value/params",
                    ),
                ),
            ),
        ),
    ],
)
def test_prepare_maps_every_draft_and_creates_fresh_occurrence_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    draft: CompareDraft,
    intent: CompareIntent,
    options: PlanningOptions,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        identifiers = iter((_INVOCATION_ID, _RUN_ID))
        captured: list[tuple[ComparePrepareRequest, int]] = []

        def prepare(
            _service: CompareService,
            request: ComparePrepareRequest,
        ) -> ComparePreparationResult:
            captured.append((request, threading.get_ident()))
            return _prepare_failure()

        monkeypatch.setattr(backend_module, "uuid4", lambda: next(identifiers))
        monkeypatch.setattr(CompareService, "prepare", prepare)
        caller = threading.get_ident()
        assert await backend.prepare_compare(draft, _controls()) == _prepare_failure()
        request, worker = captured[0]
        assert request.intent is intent
        assert request.project.options == options
        assert request.project.config == _launch(tmp_path).config
        assert request.project.additional_sources == _launch(tmp_path).additional_sources
        assert request.project.policy == _launch(tmp_path).policy
        assert request.project.invocation_id == _INVOCATION_ID
        assert request.project.run_id == _RUN_ID
        assert worker != caller
        await backend.close()

    asyncio.run(scenario())


def test_start_consumes_typed_handle_on_loop_and_uses_exact_no_persist_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        sentinel = cast("ExecutionSession", object())
        validated: list[tuple[ComparePreparationHandle, CompareExecutionControls]] = []
        captured: list[tuple[ComparePreparationHandle, CompareExecutionControls, int]] = []

        def validate_start(
            _service: CompareService,
            handle: ComparePreparationHandle,
            controls: CompareExecutionControls,
        ) -> None:
            validated.append((handle, controls))

        def start(
            _service: CompareService,
            handle: ComparePreparationHandle,
            controls: CompareExecutionControls,
        ) -> ExecutionSession:
            captured.append((handle, controls, threading.get_ident()))
            return sentinel

        monkeypatch.setattr(CompareService, "validate_start", validate_start)
        monkeypatch.setattr(CompareService, "start", start)
        caller = threading.get_ident()
        assert await backend.start_compare(_HANDLE, _controls()) is sentinel
        handle, controls, execution_thread = captured[0]
        assert handle is _HANDLE
        assert execution_thread == caller
        assert controls.settings == _controls().settings
        assert controls.artifact_root is None
        assert not controls.approve_mutations
        assert controls.tool_version == "test"
        assert validated == [(_HANDLE, controls)]
        await backend.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            CompareServiceError(CompareServiceErrorCode.CLOSED),
            TuiBackendErrorCode.CLOSED,
        ),
        (
            CompareServiceError(CompareServiceErrorCode.HANDLE_UNKNOWN),
            TuiBackendErrorCode.COMPARE_HANDLE_UNKNOWN,
        ),
        (
            _unclassified_compare_service_error(),
            TuiBackendErrorCode.COMPARE_START_FAILED,
        ),
        (RuntimeError(_SECRET), TuiBackendErrorCode.COMPARE_START_FAILED),
    ],
)
def test_start_contains_and_maps_compare_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: TuiBackendErrorCode,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)

        def validate_start(*_args: object) -> None:
            raise failure

        monkeypatch.setattr(CompareService, "validate_start", validate_start)
        with pytest.raises(TuiBackendError) as caught:
            await backend.start_compare(_HANDLE, _controls())
        assert caught.value.code is expected
        _assert_value_free(caught.value)
        await backend.close()

    asyncio.run(scenario())


def test_discard_delegates_and_contains_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        captured: list[ComparePreparationHandle] = []

        def discard(_service: CompareService, handle: ComparePreparationHandle) -> bool:
            captured.append(handle)
            return True

        monkeypatch.setattr(CompareService, "discard", discard)
        assert await backend.discard_compare(_HANDLE)
        assert captured == [_HANDLE]

        def fail(*_args: object) -> bool:
            raise RuntimeError(_SECRET)

        monkeypatch.setattr(CompareService, "discard", fail)
        with pytest.raises(TuiBackendError) as failed:
            await backend.discard_compare(_HANDLE)
        assert failed.value.code is TuiBackendErrorCode.COMPARE_DISCARD_FAILED
        _assert_value_free(failed.value)
        await backend.close()

    asyncio.run(scenario())


def test_report_listing_runs_off_loop_with_exact_launch_root_and_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        custom = ReportArchiveLimits(
            maximum_entries=7,
            artifact=DEFAULT_TUI_ARTIFACT_READ_LIMITS,
        )
        backend = ProductionTuiBackend(_launch(tmp_path), report_limits=custom)
        expected = ReportArchiveListing(entries=(), failures=())
        captured: list[tuple[Path, ReportArchiveLimits, int]] = []

        def list_reports(root: Path, limits: ReportArchiveLimits) -> ReportArchiveListing:
            captured.append((root, limits, threading.get_ident()))
            return expected

        monkeypatch.setattr(backend_module, "list_report_archive", list_reports)
        caller = threading.get_ident()
        assert await backend.list_reports() is expected
        assert captured == [(_launch(tmp_path).artifact_root.resolve(), custom, captured[0][2])]
        assert captured[0][2] != caller
        await backend.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            ReportArchiveError(ReportArchiveErrorCode.BUDGET_EXCEEDED),
            TuiBackendErrorCode.REPORT_LIST_LIMIT_EXCEEDED,
        ),
        (
            ReportArchiveError(ReportArchiveErrorCode.ARCHIVE_UNAVAILABLE),
            TuiBackendErrorCode.REPORT_LIST_UNAVAILABLE,
        ),
        (
            ReportArchiveError(ReportArchiveErrorCode.INVALID_LIMIT),
            TuiBackendErrorCode.REPORT_LIST_UNAVAILABLE,
        ),
        (RuntimeError(_SECRET), TuiBackendErrorCode.REPORT_LIST_UNAVAILABLE),
    ],
)
def test_report_listing_contains_and_maps_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: TuiBackendErrorCode,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)

        def fail(*_args: object) -> ReportArchiveListing:
            raise failure

        monkeypatch.setattr(backend_module, "list_report_archive", fail)
        with pytest.raises(TuiBackendError) as caught:
            await backend.list_reports()
        assert caught.value.code is expected
        _assert_value_free(caught.value)
        await backend.close()

    asyncio.run(scenario())


def test_report_open_verifies_then_projects_off_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        artifact = object()
        expected = cast("ResultProjection", object())
        calls: list[tuple[str, int]] = []

        def verify(root: Path, bundle_id: str, limits: object) -> object:
            assert root == _launch(tmp_path).artifact_root.resolve()
            assert bundle_id == "artifact-0"
            assert limits == DEFAULT_TUI_ARTIFACT_READ_LIMITS
            calls.append(("verify", threading.get_ident()))
            return artifact

        def project(value: object) -> ResultProjection:
            assert value is artifact
            calls.append(("project", threading.get_ident()))
            return expected

        monkeypatch.setattr(backend_module, "verify_artifact", verify)
        monkeypatch.setattr(backend_module, "project_results", project)
        caller = threading.get_ident()
        assert await backend.open_report("artifact-0") is expected
        assert [name for name, _thread in calls] == ["verify", "project"]
        assert all(thread != caller for _name, thread in calls)
        assert calls[0][1] == calls[1][1]
        await backend.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["verify", "project"])
def test_report_open_contains_application_or_projection_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        artifact = object()

        def verify(*_args: object) -> object:
            if stage == "verify":
                raise RuntimeError(_SECRET)
            return artifact

        def project(_artifact_value: object) -> ResultProjection:
            raise RuntimeError(_SECRET)

        monkeypatch.setattr(backend_module, "verify_artifact", verify)
        monkeypatch.setattr(backend_module, "project_results", project)
        with pytest.raises(TuiBackendError) as caught:
            await backend.open_report("artifact-0")
        assert caught.value.code is TuiBackendErrorCode.REPORT_OPEN_FAILED
        _assert_value_free(caught.value)
        await backend.close()

    asyncio.run(scenario())


def test_backend_contains_project_and_prepare_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)

        def fail_project(_request: object) -> ProjectWorkspaceResult:
            raise RuntimeError(_SECRET)

        monkeypatch.setattr(backend_module, "inspect_project_workspace", fail_project)
        with pytest.raises(TuiBackendError) as project:
            await backend.load_project()
        assert project.value.code is TuiBackendErrorCode.PROJECT_LOAD_FAILED
        _assert_value_free(project.value)

        def fail_uuid() -> UUID:
            raise RuntimeError(_SECRET)

        monkeypatch.setattr(backend_module, "uuid4", fail_uuid)
        with pytest.raises(TuiBackendError) as prepare:
            await backend.prepare_compare(
                CompareDraft(
                    mode=CompareMode.ASSERTIONS,
                    subjects=("ping",),
                    environments=("staging",),
                ),
                _controls(),
            )
        assert prepare.value.code is TuiBackendErrorCode.COMPARE_PREPARE_FAILED
        _assert_value_free(prepare.value)
        await backend.close()

    asyncio.run(scenario())


def test_thread_cancellation_waits_for_blocking_facade_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def inspect(_request: object) -> ProjectWorkspaceResult:
            entered.set()
            release.wait(timeout=5)
            finished.set()
            return _project_failure()

        monkeypatch.setattr(backend_module, "inspect_project_workspace", inspect)
        task = asyncio.create_task(backend.load_project())
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        await backend.close()

    asyncio.run(scenario())


def test_close_is_idempotent_contains_failure_and_rejects_later_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        calls = 0

        def fail_close(_service: CompareService) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError(_SECRET)

        monkeypatch.setattr(CompareService, "close", fail_close)
        with pytest.raises(TuiBackendError) as closed:
            await backend.close()
        assert closed.value.code is TuiBackendErrorCode.CLOSE_FAILED
        _assert_value_free(closed.value)
        await backend.close()
        assert calls == 1

        with pytest.raises(TuiBackendError) as operation:
            await backend.load_project()
        assert operation.value.code is TuiBackendErrorCode.CLOSED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "operation",
    [
        lambda backend: backend.prepare_compare(cast("CompareDraft", object()), _controls()),
        lambda backend: backend.start_compare(
            cast("ComparePreparationHandle", object()),
            _controls(),
        ),
        lambda backend: backend.start_compare(_HANDLE, cast("TuiExecutionControls", object())),
        lambda backend: backend.discard_compare(cast("ComparePreparationHandle", object())),
        lambda backend: backend.open_report(cast("str", object())),
        lambda backend: backend.open_report(""),
        lambda backend: backend.open_report("x" * 64),
    ],
)
def test_backend_rejects_invalid_requests(
    tmp_path: Path,
    operation: Callable[[ProductionTuiBackend], Awaitable[object]],
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        with pytest.raises(TuiBackendError) as caught:
            await operation(backend)
        assert caught.value.code is TuiBackendErrorCode.INVALID_REQUEST
        await backend.close()

    asyncio.run(scenario())


def test_backend_constructor_and_public_contract_validation(tmp_path: Path) -> None:
    with pytest.raises(TuiBackendError) as launch:
        ProductionTuiBackend(cast("TuiLaunchRequest", object()))
    assert launch.value.code is TuiBackendErrorCode.INVALID_REQUEST
    with pytest.raises(TuiBackendError) as limits:
        ProductionTuiBackend(
            _launch(tmp_path),
            report_limits=cast("ReportArchiveLimits", object()),
        )
    assert limits.value.code is TuiBackendErrorCode.INVALID_REQUEST
    with pytest.raises(TuiBackendError) as outstanding:
        ProductionTuiBackend(_launch(tmp_path), maximum_outstanding_preparations=0)
    assert outstanding.value.code is TuiBackendErrorCode.INVALID_REQUEST

    error = TuiBackendError(TuiBackendErrorCode.CLOSED)
    assert str(error) == "closed"
    with pytest.raises(TypeError, match="error code"):
        TuiBackendError(cast("TuiBackendErrorCode", object()))


@pytest.mark.parametrize(
    "changes",
    [
        {"config": object()},
        {"additional_sources": []},
        {"additional_sources": (object(),)},
        {"policy": object()},
        {"artifact_root": object()},
        {"limits": object()},
    ],
)
def test_tui_launch_request_rejects_invalid_values(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "config": Path("comparo.yaml"),
        "additional_sources": (),
        "policy": None,
        "artifact_root": Path(".comparo/artifacts"),
        "limits": tui_launch_limits_defaults_v1(),
    }
    values.update(changes)
    with pytest.raises(TypeError):
        TuiLaunchRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("artifact_root", [Path(), Path(".."), Path("../reports")])
def test_tui_launch_request_rejects_non_descendant_relative_artifact_roots(
    artifact_root: Path,
) -> None:
    with pytest.raises(ValueError, match="descendant path"):
        TuiLaunchRequest(artifact_root=artifact_root)


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": object()},
        {"subjects": object()},
        {"candidates": object()},
        {"subjects": ("",)},
        {"subjects": ("x" * 64,)},
        {"baseline": ""},
        {"approve_mutations": 1},
        {"validation_code": object()},
    ],
)
def test_compare_draft_rejects_invalid_values(changes: dict[str, object]) -> None:
    values: dict[str, object] = {"mode": CompareMode.ASSERTIONS}
    values.update(changes)
    with pytest.raises((TypeError, ValueError)):
        CompareDraft(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"settings": object()}, TypeError),
        ({"approve_mutations": 1}, TypeError),
        ({"tool_version": 1}, ValueError),
        ({"tool_version": ""}, ValueError),
        ({"tool_version": "x" * 65}, ValueError),
    ],
)
def test_tui_execution_controls_reject_invalid_values(
    changes: dict[str, object],
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "settings": production_execution_defaults_v1(),
        "approve_mutations": False,
        "tool_version": "test",
    }
    values.update(changes)
    with pytest.raises(error):
        TuiExecutionControls(**values)  # type: ignore[arg-type]


def _assert_value_free(error: TuiBackendError) -> None:
    rendered = "".join(traceback.format_exception(error))
    assert _SECRET not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None
