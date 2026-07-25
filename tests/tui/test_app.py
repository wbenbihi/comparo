"""Headless journeys for the Textual application shell and coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from pathlib import Path
from typing import cast

import msgspec
import pytest
from textual.pilot import Pilot
from textual.widgets import ContentSwitcher
from textual.widgets import Input
from textual.widgets import Static
from textual.widgets import Switch
from textual.widgets import Tab
from textual.widgets import Tabs

from comparo.application.compare import ComparePreparationHandle
from comparo.application.compare import ComparePreparationResult
from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSession
from comparo.application.execution_session import ExecutionSessionPhase
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanCardinalityInspection
from comparo.application.inspection import PlanInspection
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import SourceCoordinate
from comparo.application.production_execution import ProductionExecutionFailure
from comparo.application.production_execution import ProductionExecutionFailureKind
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.production_execution import production_execution_defaults_v1
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.project_workspace import ProjectSourceSummary
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.application.project_workspace import ProjectWorkspaceSnapshot
from comparo.application.report_archive import ReportArchiveEntry
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultEntity
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.application.results import project_results
from comparo.core.artifacts import ARTIFACT_API_VERSION
from comparo.core.artifacts import ArtifactCompletion
from comparo.core.artifacts import ArtifactCompletionKind
from comparo.core.artifacts import ArtifactDefinition
from comparo.core.artifacts import ArtifactGate
from comparo.core.artifacts import ArtifactGateVerdict
from comparo.core.artifacts import ArtifactMetadata
from comparo.core.artifacts import ArtifactProvenance
from comparo.core.artifacts import ArtifactSummary
from comparo.core.artifacts import RunArtifact
from comparo.tui.app import ComparoApp
from comparo.tui.commands import CommandId
from comparo.tui.components import CommandFooter
from comparo.tui.components import CompareDraftForm
from comparo.tui.components import ProjectResourceDetail
from comparo.tui.components import ProjectResourceList
from comparo.tui.components import ReportList
from comparo.tui.components import ResultIndex
from comparo.tui.components import ResultsWorkspaceView
from comparo.tui.components import TuiOnlySettingsPanel
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareDraftValidationCode
from comparo.tui.contracts import CompareMode
from comparo.tui.contracts import TuiBackend
from comparo.tui.contracts import TuiBackendError
from comparo.tui.contracts import TuiBackendErrorCode
from comparo.tui.contracts import TuiExecutionControls
from comparo.tui.contracts import TuiInteractionLimits
from comparo.tui.controllers import CompareSurface
from comparo.tui.controllers import ReportSurface
from comparo.tui.controllers import TuiCoordinator
from comparo.tui.controllers import TuiOperationSlot
from comparo.tui.controllers.coordinator import failure_code
from comparo.tui.controllers.coordinator import same_compare_intent
from comparo.tui.navigation import NavigationFrame
from comparo.tui.navigation import Route
from comparo.tui.navigation import WorkspaceId
from comparo.tui.selection import restore_frame
from comparo.tui.selection import result_reference_from_route
from comparo.tui.state import ThemePreference
from comparo.tui.state import TuiPreferences
from comparo.tui.workspaces import CompareWorkspace
from comparo.tui.workspaces import ExplorerWorkspace
from comparo.tui.workspaces import ReportWorkspace

_CANARY = "app-secret-canary-9f31d26a"


def _coordinate(kind: str, resource_id: str) -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="local",
        kind=kind,
        resource_id=resource_id,
        fingerprint="a" * 64,
    )


def _result_reference() -> ResultReference:
    return ResultReference(entity=ResultEntity.EXECUTION, result_id="execution-1")


def _resource(kind: str, resource_id: str) -> ProjectResourceSummary:
    return ProjectResourceSummary(
        coordinate=_coordinate(kind, resource_id),
        name=resource_id.title(),
        description=None,
        tags=(),
        source=SourceCoordinate(
            source="project.yaml",
            document=1,
            line=1,
            column=1,
        ),
    )


def _project_result() -> ProjectWorkspaceResult:
    project = _resource("Project", "demo")
    resources = (
        _resource("Request", "ping"),
        _resource("Environment", "staging"),
        _resource("Environment", "prod"),
        _resource("ExecutionProfile", "release"),
    )
    return ProjectWorkspaceResult(
        snapshot=ProjectWorkspaceSnapshot(
            api_version="comparo/v1alpha1",
            namespace="local",
            project=project,
            resources=resources,
        ),
        sources=(
            ProjectSourceSummary(
                source_id="project.yaml",
                path=str(Path("/project.yaml")),
                entry=True,
                resources=(project.coordinate, *(item.coordinate for item in resources)),
            ),
        ),
    )


def _listing() -> ReportArchiveListing:
    return ReportArchiveListing(
        entries=(
            ReportArchiveEntry(
                bundle_id="bundle-1",
                run_id="run-1",
                invocation_id="invocation-1",
                created_unix_nanoseconds=1,
                completion="complete",
                gate="passed",
                subjects=("ping",),
                environments=("staging", "prod"),
                executions=2,
                comparisons=1,
                assertions=1,
                diffs=1,
                blobs=0,
                blob_bytes=0,
                coverage_issues=0,
            ),
        ),
        failures=(),
    )


def _progress(revision: int, phase: ExecutionSessionPhase) -> ExecutionProgressSnapshot:
    terminal = phase in {ExecutionSessionPhase.FINISHED, ExecutionSessionPhase.CANCELLED}
    return ExecutionProgressSnapshot(
        revision=revision,
        run_id="run-1",
        phase=phase,
        total_executions=2,
        total_comparisons=1,
        queued_executions=2,
        started_executions=2 if terminal else 1,
        finished_executions=2 if terminal else 0,
        finished_comparisons=1 if terminal else 0,
    )


def _definition(kind: str, resource_id: str) -> ArtifactDefinition:
    return ArtifactDefinition(
        namespace="local",
        kind=kind,
        resource_id=resource_id,
        fingerprint="b" * 64,
    )


def _artifact() -> RunArtifact:
    project = _definition("Project", "demo")
    policy = _definition("ExecutionPolicy", "default")
    return RunArtifact(
        api_version=ARTIFACT_API_VERSION,
        metadata=ArtifactMetadata(
            artifact_id="artifact-1",
            run_id="run-1",
            invocation_id="invocation-1",
            created_unix_nanoseconds=1,
            tool_name="comparo",
            tool_version="test",
            configuration_api_version="comparo/v1alpha1",
        ),
        provenance=ArtifactProvenance(
            project=project,
            execution_policy=policy,
            artifact_profile=None,
            redaction_profile=None,
            configuration_fingerprint="c" * 64,
            plan_fingerprint="d" * 64,
            ordering="plan-v1",
        ),
        completion=ArtifactCompletion(
            kind=ArtifactCompletionKind.COMPLETE,
            instant_unix_nanoseconds=2,
        ),
        gate=ArtifactGate(
            verdict=ArtifactGateVerdict.NOT_REQUESTED,
            profile=None,
            reasons=(),
            coverage=(),
            assessments=(),
        ),
        summary=ArtifactSummary(
            executions=(),
            comparisons=(),
            assertions=(),
            diffs=(),
        ),
        executions=(),
        comparisons=(),
        resources=tuple(
            sorted(
                (project, policy),
                key=lambda item: (item.namespace, item.kind, item.resource_id),
            )
        ),
        budgets=(),
        blobs=(),
    )


def _plan() -> PlanInspection:
    return PlanInspection(
        api_version="comparo.plan/v1alpha1",
        run_id="run-1",
        invocation_id="invocation-1",
        fingerprint="e" * 64,
        ordering="canonical",
        policy=_coordinate("ExecutionPolicy", "default"),
        cardinality=PlanCardinalityInspection(
            cells=1,
            trials=1,
            environment_executions=2,
            comparisons=1,
            maximum_attempts=2,
        ),
        slices=(),
        budgets=(),
        provenance=(),
    )


class _FakeSession:
    def __init__(self, result: ProductionExecutionResult) -> None:
        self._result = result
        self._finished = asyncio.Event()
        self.cancelled = False
        self.closed = False

    async def updates(self) -> AsyncIterator[ExecutionProgressSnapshot]:
        yield _progress(0, ExecutionSessionPhase.RUNNING)
        await self._finished.wait()
        phase = (
            ExecutionSessionPhase.CANCELLED if self.cancelled else ExecutionSessionPhase.FINISHED
        )
        yield _progress(1, phase)

    def cancel(self) -> bool:
        if self.cancelled or self._finished.is_set():
            return False
        self.cancelled = True
        self._finished.set()
        return True

    async def result(self) -> ProductionExecutionResult:
        await self._finished.wait()
        return self._result

    async def close(self) -> None:
        self.closed = True
        self.cancel()

    def finish(self) -> None:
        self._finished.set()


class _ExplodingSession:
    def __init__(
        self,
        *,
        cancel_error: bool = False,
        close_error: bool = False,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.cancel_error = cancel_error
        self.close_error = close_error
        self.on_close = on_close
        self.closed = False

    async def updates(self) -> AsyncIterator[ExecutionProgressSnapshot]:
        if self.close_error:  # pragma: no cover - keeps the fake a valid async iterator
            yield _progress(0, ExecutionSessionPhase.RUNNING)
        raise RuntimeError(_CANARY)

    def cancel(self) -> bool:
        if self.cancel_error:
            raise RuntimeError(_CANARY)
        return True

    async def result(self) -> ProductionExecutionResult:
        raise RuntimeError(_CANARY)

    async def close(self) -> None:
        self.closed = True
        if self.on_close is not None:
            self.on_close()
        if self.close_error:
            raise RuntimeError(_CANARY)


class _ResultGateSession:
    def __init__(self) -> None:
        self.result_started = asyncio.Event()
        self.release_result = asyncio.Event()
        self.closed = False

    async def updates(self) -> AsyncIterator[ExecutionProgressSnapshot]:
        if self.closed:  # pragma: no cover - keeps the fake a valid async iterator
            yield _progress(0, ExecutionSessionPhase.RUNNING)

    def cancel(self) -> bool:
        return False

    async def result(self) -> ProductionExecutionResult:
        self.result_started.set()
        await self.release_result.wait()
        return ProductionExecutionResult(artifact=_artifact())

    async def close(self) -> None:
        self.closed = True


def _failed_execution() -> ProductionExecutionResult:
    return ProductionExecutionResult(
        failure=ProductionExecutionFailure(
            kind=ProductionExecutionFailureKind.INTERNAL,
            diagnostics=(
                ApplicationDiagnostic(
                    code="execution.safe-failure",
                    severity="error",
                    stage="execution",
                ),
            ),
        )
    )


class _FakeBackend:
    def __init__(self, *, fail_with_canary: bool = False) -> None:
        self.calls: list[str] = []
        self.closed = False
        self.fail_with_canary = fail_with_canary
        self.projection = project_results(_artifact())
        self.session = _FakeSession(ProductionExecutionResult(artifact=_artifact()))
        self.discarded: list[ComparePreparationHandle] = []
        self.failures: dict[str, Exception] = {}
        self.hooks: dict[str, Callable[[], None]] = {}
        self.preparation: ComparePreparationResult | None = None
        self.prepared_drafts: list[CompareDraft] = []
        self.started_controls: list[TuiExecutionControls] = []
        self.prepare_started: asyncio.Event | None = None
        self.prepare_release: asyncio.Event | None = None

    def _before(self, operation: str) -> None:
        hook = self.hooks.get(operation)
        if hook is not None:
            hook()
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    async def load_project(self) -> ProjectWorkspaceResult:
        self.calls.append("load-project")
        self._before("load-project")
        if self.fail_with_canary:
            raise RuntimeError(_CANARY)
        return _project_result()

    async def prepare_compare(self, draft: CompareDraft) -> ComparePreparationResult:
        self.calls.append(f"prepare:{draft.mode.value}")
        self.prepared_drafts.append(draft)
        self._before("prepare")
        if self.prepare_started is not None:
            self.prepare_started.set()
        if self.prepare_release is not None:
            await self.prepare_release.wait()
        if self.fail_with_canary:
            raise RuntimeError(_CANARY)
        return self.preparation or ComparePreparationResult(
            handle=ComparePreparationHandle(value="prepared-handle"),
            inspection=_plan(),
        )

    async def start_compare(
        self,
        handle: ComparePreparationHandle,
        controls: TuiExecutionControls,
    ) -> ExecutionSession:
        self.started_controls.append(controls)
        self.calls.append(f"start:{handle.value}")
        self._before("start")
        if self.fail_with_canary:
            raise RuntimeError(_CANARY)
        return cast("ExecutionSession", self.session)

    async def discard_compare(self, handle: ComparePreparationHandle) -> bool:
        self.calls.append("discard")
        self._before("discard")
        self.discarded.append(handle)
        return True

    async def list_reports(self) -> ReportArchiveListing:
        self.calls.append("list-reports")
        self._before("list-reports")
        if self.fail_with_canary:
            raise RuntimeError(_CANARY)
        return _listing()

    async def open_report(self, bundle_id: str) -> ResultProjection:
        self.calls.append(f"open:{bundle_id}")
        self._before("open")
        if self.fail_with_canary:
            raise RuntimeError(_CANARY)
        return self.projection

    async def close(self) -> None:
        self.closed = True
        self.calls.append("close")
        self._before("close")


def _controls() -> TuiExecutionControls:
    return TuiExecutionControls(
        settings=production_execution_defaults_v1(),
        approve_mutations=False,
        tool_version="test",
    )


def _invalid_controls() -> TuiExecutionControls:
    return cast("TuiExecutionControls", object())


def _invalid_draft() -> CompareDraft:
    return cast("CompareDraft", object())


def _invalid_preferences() -> TuiPreferences:
    return cast("TuiPreferences", object())


def _app(backend: _FakeBackend) -> ComparoApp:
    return ComparoApp(cast("TuiBackend", backend), _controls())


async def _wait_until(
    pilot: Pilot[object],
    predicate: Callable[[], bool],
) -> None:
    for _attempt in range(100):
        if predicate():
            return
        await pilot.pause(0.01)
    pytest.fail("condition was not reached")


def _footer_text(app: ComparoApp) -> str:
    return " ".join(str(item.content) for item in app.query_one(CommandFooter).query(Static))


def test_result_route_identity_recovers_only_typed_closed_references() -> None:
    assert result_reference_from_route("execution:execution-1") == _result_reference()
    assert result_reference_from_route(None) is None
    assert result_reference_from_route("missing-separator") is None
    assert result_reference_from_route("unknown:result-1") is None


def test_shell_has_four_workspaces_one_switcher_and_contextual_commands() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        app = _app(backend)

        async with app.run_test(size=(80, 36)) as pilot:
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.project_result is not None and app.report_listing is not None,
            )
            tabs = app.query_one("#workspace-tabs", Tabs)
            assert tuple(tab.label.plain for tab in tabs.query(Tab)) == (
                "Explorer",
                "Compare",
                "Report",
                "Settings",
            )
            assert len(app.query(ContentSwitcher)) == 1
            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "workspace-explorer"
            assert switcher.has_class("layout-compact")
            assert {"load-project", "list-reports"}.issubset(backend.calls)
            assert "Reload" in _footer_text(app)

            await pilot.press("2")
            await pilot.pause()
            assert app.navigation.active.value == "compare"
            assert switcher.current == "workspace-compare"
            assert "Preview" in _footer_text(app)
            assert "Execute" not in _footer_text(app)
            assert "Cancel" not in _footer_text(app)
            await pilot.press("1")
            await pilot.pause()
            assert app.navigation.active.value == "explorer"
            await pilot.press("2")
            await pilot.pause()

            await app.select_workspace(WorkspaceId.REPORT)
            await app.open_report("bundle-1")
            await app.select_workspace(WorkspaceId.EXPLORER)
            resource = _project_result().snapshot
            assert resource is not None
            explorer = app.query_one("#workspace-explorer", ExplorerWorkspace)
            explorer.restore_resource(None)
            explorer.restore_resource("malformed")
            explorer.restore_resource("Request/missing")
            first_resource = resource.resources[0]
            second_resource = resource.resources[1]
            explorer.show_selected_resource(ProjectResourceList.ResourceSelected(first_resource))
            app.track_project_resource(ProjectResourceList.ResourceSelected(first_resource))
            explorer.show_selected_resource(ProjectResourceList.ResourceSelected(second_resource))
            app.track_project_resource(ProjectResourceList.ResourceSelected(second_resource))
            assert app.navigation.current.route.view == "resource"
            await app.go_back()
            assert first_resource.coordinate.resource_id in str(
                explorer.query_one(ProjectResourceDetail).render()
            )
            explorer.show_selected_resource(ProjectResourceList.ResourceSelected(second_resource))
            await app.select_workspace(WorkspaceId.REPORT)
            assert app.navigation.current.route.view == "result"
            results = app.query_one("#workspace-report ResultsWorkspaceView", ResultsWorkspaceView)
            first_reference = _result_reference()
            second_reference = ResultReference(
                entity=ResultEntity.DIFF,
                result_id="diff-2",
            )
            results.follow_reference(first_reference)
            app.track_result_selection(ResultIndex.ResultSelected(first_reference))
            results.follow_reference(second_reference)
            app.track_result_selection(ResultIndex.ResultSelected(second_reference))
            assert app.navigation.current.route.view == "result-detail"
            await app.go_back()
            assert app.navigation.current.route.entity == "execution:execution-1"
            selected_after_first_back: object = results.selected_reference
            assert selected_after_first_back == first_reference
            await app.go_back()
            assert app.navigation.current.route.view == "result"
            selected_after_second_back: object = results.selected_reference
            assert selected_after_second_back is None
            assert app._coordinator.report_surface.value == "result"
            await app.go_back()
            assert app.navigation.current.route.view == "root"
            assert app._coordinator.report_surface.value == "browse"
            await app.select_workspace(WorkspaceId.EXPLORER)
            assert first_resource.coordinate.resource_id in str(
                explorer.query_one(ProjectResourceDetail).render()
            )

        assert backend.closed

    asyncio.run(scenario())


def test_compare_prepares_runs_projects_results_without_focus_theft() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        app = _app(backend)
        draft = CompareDraft(
            mode=CompareMode.DIFFERENCES,
            subjects=("ping",),
            baseline="staging",
            candidates=("prod",),
        )

        async with app.run_test(size=(140, 44)) as pilot:
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.project_result is not None,
            )
            await app.select_workspace(WorkspaceId.COMPARE)
            await app.prepare_compare(draft)
            assert app.compare_plan is not None
            assert "prepare:differences" in backend.calls
            assert "Execute" in _footer_text(app)
            assert "Cancel" not in _footer_text(app)

            await app.start_compare()
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.compare_progress is not None,
            )
            assert cast("object", app.active_session) is backend.session
            assert app.query_one("#workspace-compare", CompareWorkspace).state.value == "running"
            assert "Cancel" in _footer_text(app)
            assert "Execute" not in _footer_text(app)

            await app.select_workspace(WorkspaceId.SETTINGS)
            focused = app.query_one("#tui-setting-dense", Switch)
            focused.focus()
            await pilot.pause()
            assert app.focused is focused
            preferences = TuiPreferences(
                theme=ThemePreference.LIGHT,
                default_workspace=WorkspaceId.SETTINGS,
                dense=True,
                mouse=False,
            )
            app.update_preferences(TuiOnlySettingsPanel.PreferencesChanged(preferences))
            assert app.theme == "textual-light"
            assert app.screen.has_class("density-dense")
            assert app.screen.has_class("mouse-next-launch-disabled")

            backend.session.finish()
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.compare_result is not None,
            )
            assert app.navigation.active is WorkspaceId.SETTINGS
            assert app.focused is focused
            compare = app.query_one("#workspace-compare", CompareWorkspace)
            assert len(compare.query(ResultsWorkspaceView)) == 1

            await app.select_workspace(WorkspaceId.COMPARE)
            assert "Execute" not in _footer_text(app)
            assert "Cancel" not in _footer_text(app)
            app.track_result_selection(ResultIndex.ResultSelected(_result_reference()))
            assert app.navigation.current.route.view == "result-detail"
            await app.go_back()
            assert app.navigation.current.route.view == "root"
            assert compare.state.value == "results"
            await app.go_back()
            assert compare.state.value == "prepare"
            assert compare.current_draft() == draft
            assert "Preview" in _footer_text(app)
            assert "Execute" not in _footer_text(app)

            backend.session = _FakeSession(ProductionExecutionResult(artifact=_artifact()))
            await app.prepare_compare()
            assert backend.prepared_drafts[-1] == draft
            await app.start_compare()
            backend.session.finish()
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.compare_result is not None,
            )

    asyncio.run(scenario())


def test_compare_shortcut_text_is_editable_and_visible_defaults_are_prepared() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        app = _app(backend)

        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.project_result is not None,
            )
            await app.select_workspace(WorkspaceId.COMPARE)
            form = app.query_one(CompareDraftForm)
            displayed = form.current_draft()
            assert displayed.subjects == ("ping",)
            assert displayed.baseline == "staging"
            assert displayed.candidates == ("prod",)

            await app.prepare_compare()
            assert backend.prepared_drafts[-1] == displayed

            form = app.query_one(CompareDraftForm)
            subject = form.query_one("#compare-draft-subjects", Input)
            subject.value = ""
            subject.focus()
            await pilot.pause()
            effect_calls = tuple(backend.calls)
            await pilot.press("1", "2", "3", "4", "q", "r", "p", "x", "a")
            await pilot.press("backspace")
            await pilot.pause()
            assert subject.value == "1234qrpx"
            assert tuple(backend.calls[: len(effect_calls)]) == effect_calls
            assert "start:prepared-handle" not in backend.calls
            assert app.navigation.active is WorkspaceId.COMPARE
            assert app.query_one(CompareDraftForm) is form
            assert app.focused is subject
            assert app.compare_plan is None

    asyncio.run(scenario())


def test_cancel_report_read_only_and_shutdown_close_active_session() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        app = _app(backend)
        draft = CompareDraft(
            mode=CompareMode.ASSERTIONS,
            subjects=("ping",),
            environments=("staging",),
        )

        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.report_listing is not None,
            )
            await app.prepare_compare(draft)
            await app.start_compare()
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.active_session is not None,
            )
            assert app.cancel_compare()
            assert backend.session.cancelled
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.compare_result is not None,
            )

            await app.select_workspace(WorkspaceId.REPORT)
            await app.open_report("bundle-1")
            await pilot.pause()
            report = app.query_one("#workspace-report", ReportWorkspace)
            assert len(report.query(ResultsWorkspaceView)) == 1
            assert "read-only" in app.export_screenshot(simplify=True)

        second_backend = _FakeBackend()
        second_app = _app(second_backend)
        async with second_app.run_test(size=(120, 40)) as pilot:
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: second_app.project_result is not None,
            )
            await second_app.prepare_compare(draft)
            await second_app.start_compare()
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: second_app.active_session is not None,
            )
        assert second_backend.session.closed
        assert second_backend.closed

    asyncio.run(scenario())


def test_generic_backend_failures_and_crash_handler_never_render_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _FakeBackend(fail_with_canary=True)
        app = _app(backend)

        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.project_result is not None and app.report_error_code is not None,
            )
            await app.prepare_compare(CompareDraft(mode=CompareMode.EXECUTION_PROFILE))
            assert app.compare_diagnostics[0].code == "tui.compare.prepare-failed"
            screenshot = app.export_screenshot(simplify=True)
            assert _CANARY not in screenshot
            assert _CANARY not in app.compare_diagnostics[0].code

        crash_app = _app(_FakeBackend())
        exits: list[tuple[int | None, int, object | None]] = []

        def record_exit(
            result: int | None = None,
            return_code: int = 0,
            message: object | None = None,
        ) -> None:
            exits.append((result, return_code, message))

        monkeypatch.setattr(crash_app, "exit", record_exit)
        crash_app._handle_exception(RuntimeError(_CANARY))
        assert exits == [(1, 1, "Comparo encountered an internal error.")]
        assert _CANARY not in repr(exits)

    asyncio.run(scenario())


def test_normal_quit_returns_stable_zero_result() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        app = _app(backend)

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("q")
            await pilot.pause()

        assert app.return_value == 0
        assert app.return_code == 0
        assert backend.closed

    asyncio.run(scenario())


def test_app_validation_dispatch_and_render_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        construct = cast("Callable[..., ComparoApp]", ComparoApp)
        with pytest.raises(TypeError, match="execution controls"):
            construct(cast("TuiBackend", backend), _invalid_controls())
        with pytest.raises(TypeError, match="preferences"):
            construct(
                cast("TuiBackend", backend),
                _controls(),
                preferences=_invalid_preferences(),
            )
        with pytest.raises(TypeError, match="interaction limits"):
            construct(
                cast("TuiBackend", backend),
                _controls(),
                interaction_limits=cast("TuiInteractionLimits", object()),
            )

        unmounted = _app(_FakeBackend())
        await unmounted._render_workspace(WorkspaceId.EXPLORER)
        await unmounted._render_footer()
        unmounted._apply_responsive_class(80)
        await unmounted.prepare_compare()
        await unmounted.close_services()
        ExplorerWorkspace(None).restore_resource(None)

        app = _app(backend)
        palette_calls: list[bool] = []
        monkeypatch.setattr(app, "action_command_palette", lambda: palette_calls.append(True))
        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.project_result is not None and app.report_listing is not None,
            )
            release_duplicate = asyncio.Event()

            async def held_project_operation() -> None:
                await release_duplicate.wait()

            app._spawn_operation(held_project_operation(), TuiOperationSlot.PROJECT)
            app._spawn_operation(held_project_operation(), TuiOperationSlot.PROJECT)
            release_duplicate.set()
            await pilot.pause()
            restore_frame(
                app, NavigationFrame(route=Route(workspace=WorkspaceId.SETTINGS, view="root"))
            )
            restore_frame(
                app, NavigationFrame(route=Route(workspace=WorkspaceId.COMPARE, view="root"))
            )
            await pilot.click("#tab-report")
            await pilot.pause()
            assert app.navigation.active.value == "report"
            await app.select_workspace(WorkspaceId.REPORT)

            app.select_report(ReportList.ReportSelected("bundle-1"))
            await _wait_until(
                cast("Pilot[object]", pilot),
                lambda: app.navigation.current.route.view == "result",
            )
            await app.dispatch_command(CommandId.NAVIGATION_BACK)
            await app.select_workspace(WorkspaceId.EXPLORER)
            app.track_result_selection(ResultIndex.ResultSelected(_result_reference()))
            assert app.navigation.current.route.view == "root"
            await app.go_back()

            await app.action_dispatch_catalog_key("not-a-key")
            dispatch = cast("Callable[[CommandId], Awaitable[None]]", app.dispatch_command)
            with pytest.raises(TypeError, match="command"):
                await dispatch(cast("CommandId", object()))
            select = cast("Callable[[WorkspaceId], Awaitable[None]]", app.select_workspace)
            with pytest.raises(TypeError, match="workspace"):
                await select(cast("WorkspaceId", object()))

            await app.dispatch_command(CommandId.APP_HELP)
            await app.dispatch_command(CommandId.APP_PALETTE)
            await app._dispatch_global(CommandId.PROJECT_RELOAD)
            app._dispatch_workspace(CommandId.APP_HELP)
            assert palette_calls == [True]
            await app.dispatch_command(CommandId.PROJECT_RELOAD)
            await app.select_workspace(WorkspaceId.REPORT)
            await app.dispatch_command(CommandId.REPORT_RELOAD)
            await pilot.pause()

            await app.select_workspace(WorkspaceId.COMPARE)
            await app.go_back()
            starts = backend.calls.count("start:prepared-handle")
            await app.dispatch_command(CommandId.COMPARE_START)
            assert backend.calls.count("start:prepared-handle") == starts
            app.submit_compare_draft(
                CompareDraftForm.Submitted(
                    CompareDraft(
                        mode=CompareMode.ASSERTIONS,
                        subjects=("ping",),
                        environments=("staging",),
                    )
                )
            )
            await _wait_until(cast("Pilot[object]", pilot), lambda: app.compare_plan is not None)
            await _wait_until(cast("Pilot[object]", pilot), lambda: "Execute" in _footer_text(app))
            await app.dispatch_command(CommandId.COMPARE_CANCEL)
            assert app.active_session is None
            assert "Execute" in _footer_text(app)
            await app.dispatch_command(CommandId.COMPARE_START)
            await _wait_until(cast("Pilot[object]", pilot), lambda: app.active_session is not None)
            await _wait_until(cast("Pilot[object]", pilot), lambda: "Cancel" in _footer_text(app))
            preparations = len(backend.prepared_drafts)
            starts = backend.calls.count("start:prepared-handle")
            await app.dispatch_command(CommandId.COMPARE_PREVIEW)
            await app.dispatch_command(CommandId.COMPARE_START)
            assert len(backend.prepared_drafts) == preparations
            assert backend.calls.count("start:prepared-handle") == starts
            assert "Cancel" in _footer_text(app)
            await app.dispatch_command(CommandId.COMPARE_CANCEL)
            await _wait_until(cast("Pilot[object]", pilot), lambda: app.compare_result is not None)
            await app.dispatch_command(CommandId.COMPARE_START)
            await app.dispatch_command(CommandId.COMPARE_CANCEL)
            assert backend.calls.count("start:prepared-handle") == starts

            app._coordinator.compare_surface = CompareSurface.RUNNING
            app._coordinator.compare_progress = None
            app._render_compare()
            await pilot.pause()
            compare = app.query_one("#workspace-compare", CompareWorkspace)
            assert compare.state.value == "running"
            assert len(compare.query(CompareDraftForm)) == 0
            starting = compare.query_one(".state-message", Static)
            assert starting.display
            assert "Starting the admitted execution" in str(starting.content)
            app._coordinator.compare_surface = CompareSurface.RESULTS
            app._coordinator.compare_result = None
            app._render_compare()
            await app._render_workspace(WorkspaceId.SETTINGS)

            backend.hooks["open"] = lambda: setattr(
                app._coordinator,
                "_report_revision",
                app._coordinator._report_revision + 1,
            )
            await app.open_report("stale")
            backend.hooks.pop("open")

            app.unregister_theme("textual-light")
            app._apply_preferences(
                TuiPreferences(theme=ThemePreference.LIGHT, default_workspace=WorkspaceId.COMPARE)
            )
            assert app.theme == app._system_theme
            app._system_theme = "missing-theme"
            app._apply_preferences(
                TuiPreferences(theme=ThemePreference.LIGHT, default_workspace=WorkspaceId.COMPARE)
            )

            app._footer_signature = ()
            await app._footer_lock.acquire()
            footer_update = asyncio.create_task(app._render_footer())
            await asyncio.sleep(0)
            app._footer_signature = tuple(command.command_id for command in app.active_commands)
            app._footer_lock.release()
            await footer_update

        crash_app = _app(_FakeBackend())
        exits: list[tuple[int | None, int, object | None]] = []

        def flaky_exit(
            result: int | None = None,
            return_code: int = 0,
            message: object | None = None,
        ) -> None:
            if message is not None:
                raise RuntimeError(_CANARY)
            exits.append((result, return_code, message))

        monkeypatch.setattr(crash_app, "exit", flaky_exit)
        crash_app._handle_exception(RuntimeError(_CANARY))
        assert exits == [(1, 1, None)]

    asyncio.run(scenario())


def test_coordinator_validates_drafts_helpers_and_bounded_task_lifecycle() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        changes: list[WorkspaceId] = []

        async def changed(workspace: WorkspaceId) -> None:
            changes.append(workspace)

        construct = cast("Callable[..., TuiCoordinator]", TuiCoordinator)
        with pytest.raises(TypeError, match="execution controls"):
            construct(
                cast("TuiBackend", backend),
                _invalid_controls(),
                changed,
            )
        coordinator = TuiCoordinator(cast("TuiBackend", backend), _controls(), changed)
        initial_resources = coordinator.project_resources
        assert len(initial_resources) == 0
        coordinator.project = _project_result()
        assert len(coordinator.project_resources) == 4

        update_draft = cast("Callable[[CompareDraft], None]", coordinator.update_draft)
        with pytest.raises(TypeError, match="draft"):
            update_draft(_invalid_draft())
        assertion = CompareDraft(
            mode=CompareMode.ASSERTIONS,
            subjects=("ping",),
            environments=("staging",),
        )
        assert not same_compare_intent(assertion, CompareDraft(mode=CompareMode.DIFFERENCES))
        assert same_compare_intent(
            assertion,
            CompareDraft(
                mode=CompareMode.ASSERTIONS,
                subjects=("ping",),
                environments=("staging",),
                baseline="ignored",
                execution_profile="hidden",
            ),
        )
        assert not same_compare_intent(
            assertion,
            CompareDraft(
                mode=CompareMode.ASSERTIONS,
                subjects=("ping",),
                environments=("staging",),
                approve_mutations=True,
            ),
        )
        differences = CompareDraft(
            mode=CompareMode.DIFFERENCES,
            subjects=("ping",),
            baseline="staging",
            candidates=("prod",),
        )
        assert same_compare_intent(differences, differences)
        profile = CompareDraft(
            mode=CompareMode.EXECUTION_PROFILE,
            execution_profile="release",
        )
        assert same_compare_intent(profile, profile)
        assert not same_compare_intent(
            profile,
            CompareDraft(mode=CompareMode.EXECUTION_PROFILE, execution_profile="other"),
        )
        assert not same_compare_intent(
            profile,
            CompareDraft(
                mode=CompareMode.EXECUTION_PROFILE,
                execution_profile="release",
                subjects=("ping",),
                environments=("staging",),
            ),
        )
        invalid = msgspec.structs.replace(
            profile,
            validation_code=CompareDraftValidationCode.INVALID_MATRIX,
        )
        assert same_compare_intent(invalid, invalid)
        assert failure_code(RuntimeError(_CANARY), "safe.generic") == "safe.generic"
        assert (
            failure_code(
                TuiBackendError(TuiBackendErrorCode.REPORT_OPEN_FAILED),
                "safe.generic",
            )
            == "tui.backend.report-open-failed"
        )

        coordinator.compare_draft = assertion
        coordinator.compare_handle = ComparePreparationHandle(value="old-handle")
        coordinator.compare_plan = _plan()
        coordinator.update_draft(differences)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert backend.discarded == [ComparePreparationHandle(value="old-handle")]
        assert vars(coordinator)["compare_plan"] is None
        assert WorkspaceId.COMPARE in changes

        changes.clear()
        coordinator.compare_diagnostics = (
            ApplicationDiagnostic(
                code="safe.invalidated",
                severity="error",
                stage="invocation",
            ),
        )
        coordinator.update_draft(assertion)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert WorkspaceId.COMPARE in changes

        changes.clear()
        coordinator.update_draft(differences)
        await asyncio.sleep(0)
        assert not changes
        discarded = len(backend.discarded)
        for index in range(100):
            coordinator.update_draft(
                CompareDraft(
                    mode=CompareMode.ASSERTIONS,
                    subjects=(f"ping-{index}",),
                    environments=("staging",),
                )
            )
        await asyncio.sleep(0)
        assert not coordinator._tasks
        assert len(backend.discarded) == discarded
        retained = coordinator.compare_draft
        coordinator.compare_surface = CompareSurface.RESULTS
        coordinator.update_draft(assertion)
        assert coordinator.compare_draft is retained
        coordinator.compare_surface = CompareSurface.PREPARE
        coordinator.session = cast(
            "ExecutionSession", _FakeSession(ProductionExecutionResult(artifact=_artifact()))
        )
        coordinator.update_draft(assertion)
        assert coordinator.compare_draft is retained

        await coordinator.close()
        await coordinator.close()
        operation = coordinator.reload_project()
        coordinator.spawn(operation, slot=TuiOperationSlot.PROJECT)

    asyncio.run(scenario())


def test_coordinator_coalesces_each_finite_operation_slot() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()

        async def changed(_workspace: WorkspaceId) -> None:
            return

        coordinator = TuiCoordinator(cast("TuiBackend", backend), _controls(), changed)
        release = asyncio.Event()
        started: list[str] = []

        async def held(name: str) -> None:
            started.append(name)
            await release.wait()

        assert coordinator.spawn(held("project"), slot=TuiOperationSlot.PROJECT)
        await asyncio.sleep(0)
        assert not coordinator.spawn(held("duplicate"), slot=TuiOperationSlot.PROJECT)
        assert coordinator.spawn(held("report"), slot=TuiOperationSlot.REPORT)
        invalid = held("invalid")
        with pytest.raises(TypeError, match="slot"):
            coordinator.spawn(invalid, slot=cast("TuiOperationSlot", object()))
        assert len(coordinator._tasks) == 2
        assert set(coordinator._slot_tasks) == {
            TuiOperationSlot.PROJECT,
            TuiOperationSlot.REPORT,
        }

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started == ["project", "report"]
        assert not coordinator._tasks
        assert not coordinator._slot_tasks

        async def completed() -> None:
            return

        stale = asyncio.create_task(completed())
        await stale
        replacement_release = asyncio.Event()

        async def replacement() -> None:
            await replacement_release.wait()

        assert coordinator.spawn(replacement(), slot=TuiOperationSlot.PROJECT)
        coordinator._task_finished(TuiOperationSlot.PROJECT, stale)
        assert coordinator._slot_tasks[TuiOperationSlot.PROJECT] is not stale
        replacement_release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        await coordinator.close()
        assert not coordinator.spawn(
            coordinator.reload_project(),
            slot=TuiOperationSlot.PROJECT,
        )

    asyncio.run(scenario())


def test_mutation_approval_is_snapshotted_for_only_the_exact_admitted_start() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()

        async def changed(_workspace: WorkspaceId) -> None:
            return

        coordinator = TuiCoordinator(cast("TuiBackend", backend), _controls(), changed)
        approved = CompareDraft(
            mode=CompareMode.DIFFERENCES,
            subjects=("ping",),
            baseline="staging",
            candidates=("prod",),
            approve_mutations=True,
        )
        await coordinator.prepare_compare(approved)
        assert coordinator.can_execute

        coordinator.compare_draft = msgspec.structs.replace(
            approved,
            approve_mutations=False,
        )
        await coordinator.start_compare()
        assert backend.started_controls[-1].approve_mutations is True
        assert coordinator.compare_handle is None

        backend.session.finish()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await coordinator.close()

    asyncio.run(scenario())


def test_coordinator_contains_backend_session_and_projection_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        changes: list[WorkspaceId] = []

        async def changed(workspace: WorkspaceId) -> None:
            changes.append(workspace)

        coordinator = TuiCoordinator(cast("TuiBackend", backend), _controls(), changed)
        coordinator.show_compare_prepare()
        backend.failures["load-project"] = TuiBackendError(TuiBackendErrorCode.PROJECT_LOAD_FAILED)
        await coordinator.reload_project()
        assert coordinator.project is not None
        assert coordinator.project.diagnostics[0].code == "tui.backend.project-load-failed"
        backend.failures.pop("load-project")
        coordinator.session = cast(
            "ExecutionSession",
            _FakeSession(ProductionExecutionResult(artifact=_artifact())),
        )
        await coordinator.reload_project()
        coordinator.session = None
        coordinator.compare_surface = CompareSurface.RUNNING
        await coordinator.reload_project()
        coordinator.compare_surface = CompareSurface.PREPARE

        backend.failures["list-reports"] = RuntimeError(_CANARY)
        await coordinator.reload_reports()
        assert coordinator.report_surface is ReportSurface.RESULT
        assert coordinator.report_error_code == "tui.report.list-failed"
        backend.failures.pop("list-reports")
        await coordinator.reload_reports()
        coordinator.show_report_archive()
        assert coordinator.report_surface.value == "browse"

        backend.failures["open"] = RuntimeError(_CANARY)
        assert await coordinator.open_report("bundle-1")
        assert coordinator.report_error_code == "tui.report.open-failed"
        backend.failures.pop("open")

        prepare = cast(
            "Callable[[CompareDraft], Awaitable[None]]",
            coordinator.prepare_compare,
        )
        with pytest.raises(TypeError, match="draft"):
            await prepare(_invalid_draft())
        diagnostic = ApplicationDiagnostic(
            code="prepare.safe-failure",
            severity="error",
            stage="invocation",
        )
        backend.preparation = ComparePreparationResult(diagnostics=(diagnostic,))
        await coordinator.prepare_compare()
        assert coordinator.compare_diagnostics == (diagnostic,)
        await coordinator.start_compare()
        assert coordinator.session is None

        retained_session = _FakeSession(ProductionExecutionResult(artifact=_artifact()))
        coordinator.session = cast("ExecutionSession", retained_session)
        prepare_calls = len(backend.prepared_drafts)
        await coordinator.prepare_compare()
        assert len(backend.prepared_drafts) == prepare_calls
        coordinator.session = None

        backend.preparation = None
        await coordinator.prepare_compare(CompareDraft(mode=CompareMode.DIFFERENCES))
        backend.failures["start"] = RuntimeError(_CANARY)
        await coordinator.start_compare()
        assert coordinator.compare_diagnostics[0].code == "tui.compare.execution-failed"
        backend.failures.pop("start")

        execution_slot_release = asyncio.Event()

        async def occupy_execution_slot() -> None:
            await execution_slot_release.wait()

        assert coordinator.spawn(
            occupy_execution_slot(),
            slot=TuiOperationSlot.EXECUTION,
        )
        await coordinator.prepare_compare(CompareDraft(mode=CompareMode.DIFFERENCES))
        await coordinator.start_compare()
        assert backend.session.closed
        assert coordinator.compare_diagnostics[0].code == "tui.compare.execution-failed"
        execution_slot_release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert not coordinator.cancel_compare()
        exploding = _ExplodingSession(cancel_error=True)
        coordinator.session = cast("ExecutionSession", exploding)
        assert not coordinator.cancel_compare()
        await coordinator._supervise_execution(cast("ExecutionSession", exploding), _plan())
        assert exploding.closed
        assert coordinator.compare_diagnostics[0].code == "tui.compare.execution-failed"

        mutating = _ExplodingSession(
            on_close=lambda: setattr(coordinator, "session", None),
        )
        coordinator.session = cast("ExecutionSession", mutating)
        await coordinator._supervise_execution(cast("ExecutionSession", mutating), _plan())
        assert mutating.closed

        coordinator.session = cast("ExecutionSession", exploding)
        await coordinator._show_terminal_result(_failed_execution(), _plan())
        assert coordinator.compare_diagnostics[0].code == "execution.safe-failure"
        coordinator._show_failure(())
        assert coordinator.compare_diagnostics[0].code == "tui.compare.execution-failed"

        def projection_failure(_artifact_value: RunArtifact) -> ResultProjection:
            raise RuntimeError(_CANARY)

        monkeypatch.setattr(
            "comparo.tui.controllers.coordinator.project_results",
            projection_failure,
        )
        coordinator.session = cast("ExecutionSession", exploding)
        await coordinator._show_terminal_result(
            ProductionExecutionResult(artifact=_artifact()), _plan()
        )
        assert coordinator.compare_diagnostics[0].code == ("tui.compare.result-projection-failed")

        refill_handle = ComparePreparationHandle(value="refilled-discard")
        refill_pending = True

        async def refill_during_change(_workspace: WorkspaceId) -> None:
            nonlocal refill_pending
            if refill_pending:
                refill_pending = False
                coordinator._discard_queue.append(refill_handle)

        coordinator._changed = refill_during_change
        first_handle = ComparePreparationHandle(value="first-discard")
        coordinator._discard_queue.append(first_handle)
        await coordinator._finish_invalidation()
        assert first_handle in backend.discarded
        assert refill_handle in backend.discarded

        backend.failures["discard"] = RuntimeError(_CANARY)
        await coordinator._finish_invalidation()
        coordinator.compare_handle = ComparePreparationHandle(value="discard-error")
        await coordinator._discard_prepared()
        coordinator.session = cast(
            "ExecutionSession",
            _ExplodingSession(close_error=True),
        )
        backend.failures["close"] = RuntimeError(_CANARY)
        await coordinator.close()
        assert coordinator.session is None

    asyncio.run(scenario())


def test_draft_edit_rejects_one_in_flight_stale_preparation() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()
        backend.prepare_started = asyncio.Event()
        backend.prepare_release = asyncio.Event()

        async def changed(_workspace: WorkspaceId) -> None:
            return

        coordinator = TuiCoordinator(cast("TuiBackend", backend), _controls(), changed)
        original = CompareDraft(
            mode=CompareMode.DIFFERENCES,
            subjects=("ping",),
            baseline="staging",
            candidates=("prod",),
        )
        preparation = asyncio.create_task(coordinator.prepare_compare(original))
        await backend.prepare_started.wait()

        edited = CompareDraft(
            mode=CompareMode.DIFFERENCES,
            subjects=("ping-edited",),
            baseline="staging",
            candidates=("prod",),
        )
        coordinator.update_draft(edited)
        backend.prepare_release.set()
        await preparation

        assert coordinator.compare_draft == edited
        assert coordinator.compare_handle is None
        assert coordinator.compare_plan is None
        assert tuple(item.value for item in backend.discarded) == ("prepared-handle",)

    asyncio.run(scenario())


def test_coordinator_rejects_stale_async_completions_and_closing_updates() -> None:
    async def scenario() -> None:
        backend = _FakeBackend()

        async def changed(_workspace: WorkspaceId) -> None:
            return

        coordinator = TuiCoordinator(cast("TuiBackend", backend), _controls(), changed)
        backend.hooks["load-project"] = lambda: setattr(
            coordinator,
            "_project_revision",
            coordinator._project_revision + 1,
        )
        await coordinator.reload_project()
        assert coordinator.project is None
        backend.failures["load-project"] = RuntimeError(_CANARY)
        await coordinator.reload_project()
        backend.hooks.pop("load-project")
        backend.failures.pop("load-project")

        backend.hooks["list-reports"] = lambda: setattr(
            coordinator,
            "_report_revision",
            coordinator._report_revision + 1,
        )
        await coordinator.reload_reports()
        backend.failures["list-reports"] = RuntimeError(_CANARY)
        await coordinator.reload_reports()
        backend.failures.pop("list-reports")
        assert coordinator.report_listing is None

        backend.hooks["open"] = lambda: setattr(
            coordinator,
            "_report_revision",
            coordinator._report_revision + 1,
        )
        assert not await coordinator.open_report("bundle-1")
        backend.failures["open"] = RuntimeError(_CANARY)
        assert not await coordinator.open_report("bundle-1")
        backend.failures.pop("open")
        backend.hooks.pop("open")
        backend.hooks.pop("list-reports")

        backend.hooks["prepare"] = lambda: setattr(
            coordinator,
            "_compare_revision",
            coordinator._compare_revision + 1,
        )
        await coordinator.prepare_compare()
        assert tuple(item.value for item in backend.discarded) == ("prepared-handle",)
        backend.preparation = ComparePreparationResult(
            diagnostics=(
                ApplicationDiagnostic(
                    code="safe",
                    severity="error",
                    stage="invocation",
                ),
            )
        )
        await coordinator.prepare_compare()
        backend.failures["prepare"] = RuntimeError(_CANARY)
        await coordinator.prepare_compare()
        backend.failures.pop("prepare")
        backend.hooks.pop("prepare")

        session = _FakeSession(ProductionExecutionResult(artifact=_artifact()))
        coordinator.session = cast("ExecutionSession", session)
        coordinator._closing = True
        await coordinator._supervise_execution(cast("ExecutionSession", session), _plan())
        exploding = _ExplodingSession()
        coordinator.session = cast("ExecutionSession", exploding)
        await coordinator._supervise_execution(cast("ExecutionSession", exploding), _plan())

        gate = _ResultGateSession()
        coordinator._closing = False
        coordinator.session = cast("ExecutionSession", gate)
        supervision = asyncio.create_task(
            coordinator._supervise_execution(cast("ExecutionSession", gate), _plan())
        )
        await gate.result_started.wait()
        coordinator.session = cast(
            "ExecutionSession",
            _FakeSession(ProductionExecutionResult(artifact=_artifact())),
        )
        gate.release_result.set()
        await supervision

    asyncio.run(scenario())
