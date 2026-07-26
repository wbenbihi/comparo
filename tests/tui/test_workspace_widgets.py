"""Headless tests for the four thin persistent workspace widgets."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from functools import wraps
from pathlib import PurePath
from typing import ClassVar
from typing import cast
from typing import override

import pytest
from textual import on
from textual.app import App
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Switch
from textual.widgets import Tab
from textual.widgets import Tabs

from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSessionPhase
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanCardinalityInspection
from comparo.application.inspection import PlanInspection
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import SourceCoordinate
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.project_workspace import ProjectSourceSummary
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.application.project_workspace import ProjectWorkspaceSnapshot
from comparo.application.report_archive import ReportArchiveEntry
from comparo.application.report_archive import ReportArchiveEntryFailure
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
from comparo.application.results import ResultResource
from comparo.application.results import ResultSummary
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.tui.components import COMPONENT_STYLESHEET
from comparo.tui.components import ComparePlanPanel
from comparo.tui.components import CompareRunPanel
from comparo.tui.components import CompareTargetPanel
from comparo.tui.components import DiagnosticsList
from comparo.tui.components import ProjectResourceDetail
from comparo.tui.components import ProjectResourceList
from comparo.tui.components import ReportList
from comparo.tui.components import ResultsWorkspaceView
from comparo.tui.components import StatePanel
from comparo.tui.components import StatePanelMode
from comparo.tui.components import TuiOnlySettingsPanel
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareMode
from comparo.tui.design import COMPARO_DARK_THEME
from comparo.tui.navigation import WorkspaceId
from comparo.tui.state import TuiPreferences
from comparo.tui.workspaces import CompareWorkspace
from comparo.tui.workspaces import CompareWorkspaceState
from comparo.tui.workspaces import ExplorerWorkspace
from comparo.tui.workspaces import ReportWorkspace
from comparo.tui.workspaces import ReportWorkspaceState
from comparo.tui.workspaces import SettingsWorkspace


def _run_async[**P](
    function: Callable[P, Coroutine[object, object, None]],
) -> Callable[P, None]:
    """Adapt one async Textual test to the repository's synchronous runner."""

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(function(*args, **kwargs))

    return wrapper


class _WorkspaceApp(App[None]):
    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = COMPONENT_STYLESHEET

    def __init__(self, *workspaces: Vertical) -> None:
        super().__init__()
        self._workspaces = workspaces
        self.preferences: TuiPreferences | None = None

    @override
    def compose(self) -> ComposeResult:
        yield from self._workspaces

    @on(TuiOnlySettingsPanel.PreferencesChanged)
    def record_preferences(self, message: TuiOnlySettingsPanel.PreferencesChanged) -> None:
        self.preferences = message.preferences


def _coordinate(kind: str, resource_id: str) -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="local",
        kind=kind,
        resource_id=resource_id,
        fingerprint="a" * 64,
    )


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
    snapshot = ProjectWorkspaceSnapshot(
        api_version="comparo/v1alpha1",
        namespace="local",
        project=project,
        resources=resources,
    )
    return ProjectWorkspaceResult(
        snapshot=snapshot,
        sources=(
            ProjectSourceSummary(
                source_id="project.yaml",
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


def _plan() -> PlanInspection:
    return PlanInspection(
        api_version="comparo.plan/v1alpha1",
        run_id="run-1",
        invocation_id="invocation-1",
        fingerprint="b" * 64,
        ordering="canonical",
        policy=_coordinate("ExecutionPolicy", "ci"),
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


def _projection() -> ResultProjection:
    project = ResultResource(
        namespace="local",
        kind="Project",
        resource_id="demo",
        fingerprint="a" * 64,
    )
    return ResultProjection(
        summary=ResultSummary(
            artifact_api_version="comparo.artifact/v1alpha1",
            artifact_id="artifact",
            run_id="run-1",
            invocation_id="invocation-1",
            created_unix_nanoseconds=1,
            project=project,
            completion="complete",
            completion_instant_unix_nanoseconds=2,
            completion_reasons=(),
            completion_stage=None,
            completion_code=None,
            gate_verdict="passed",
            gate_profile=None,
            gate_reasons=(),
            gate_coverage=(),
            gate_assessments=(),
            executions=(),
            comparisons=(),
            assertions=(),
            diffs=(),
            coverage_issues=0,
        ),
        executions=(),
        assertions=(),
        comparisons=(),
        diffs=(),
        links=(),
    )


def _progress(*, finished: int = 0) -> ExecutionProgressSnapshot:
    return ExecutionProgressSnapshot(
        revision=finished,
        run_id="run-1",
        phase=ExecutionSessionPhase.RUNNING,
        total_executions=2,
        total_comparisons=1,
        queued_executions=2,
        started_executions=finished,
        finished_executions=finished,
        finished_comparisons=0,
    )


@_run_async
async def test_explorer_updates_internal_state_without_replacing_workspace() -> None:
    workspace = ExplorerWorkspace(None)
    app = _WorkspaceApp(workspace)

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        identity = id(workspace)
        assert workspace.query_one(StatePanel).mode is StatePanelMode.LOADING

        workspace.show_result(_project_result())
        await pilot.pause()
        assert id(workspace) == identity
        assert len(workspace.query(ProjectResourceList)) == 1
        result = _project_result()
        assert result.snapshot is not None
        workspace.show_selected_resource(
            ProjectResourceList.ResourceSelected(result.snapshot.project)
        )
        workspace.show_highlighted_resource(
            ProjectResourceList.ResourceHighlighted(result.snapshot.project)
        )
        workspace.request_use_in_compare(
            ProjectResourceDetail.UseInCompare(result.snapshot.project)
        )
        filters = workspace.query_one("#explorer-filters", Tabs)
        workspace.filter_resource_kind(Tabs.TabActivated(filters, Tab("Other", id="other")))
        workspace.filter_resource_kind(
            Tabs.TabActivated(filters, filters.query_one("#explorer-filter-env", Tab))
        )
        workspace.restore_resource("Request/ping")
        workspace.restore_resource("invalid")
        workspace.restore_resource("Request/missing")
        workspace.restore_resource(None)
        assert workspace.selected_resource() is not None
        workspace.show_result(result)

        workspace.show_loading()
        await pilot.pause()
        assert id(workspace) == identity
        assert workspace.query_one(StatePanel).mode is StatePanelMode.LOADING


@_run_async
async def test_compare_keeps_all_modes_and_lifecycle_states_in_one_workspace() -> None:
    project = _project_result().snapshot
    assert project is not None
    draft = CompareDraft(mode=CompareMode.ASSERTIONS)
    workspace = CompareWorkspace(
        draft=draft,
        resources=project.resources,
        plan=_plan(),
    )
    app = _WorkspaceApp(workspace)
    projection = _projection()

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        identity = id(workspace)
        assert workspace.current_draft().environments == ("staging",)
        assert workspace.state is CompareWorkspaceState.PREPARE

        profile_draft = CompareDraft(
            mode=CompareMode.EXECUTION_PROFILE,
            execution_profile="release",
        )
        workspace.show_prepare(profile_draft, plan=_plan())
        await pilot.pause()
        assert workspace.current_draft().mode is CompareMode.EXECUTION_PROFILE
        assert workspace.current_draft().execution_profile == "release"
        assert len(workspace.query(ComparePlanPanel)) == 1
        workspace.target_changed(CompareTargetPanel.DraftChanged(profile_draft))
        workspace.target_submitted(CompareTargetPanel.Submitted(profile_draft))
        workspace.execute_plan()
        workspace.cancel_run()
        workspace.edit_target()
        await pilot.pause()
        assert workspace.query_one(CompareTargetPanel).query_one("#compare-target-profile").display

        workspace.show_running(_progress())
        await pilot.pause()
        progress_widget = workspace.query_one(CompareRunPanel)
        workspace.show_running(_progress(finished=1))
        await pilot.pause()
        assert workspace.query_one(CompareRunPanel) is progress_widget

        workspace.show_results(projection)
        await pilot.pause()
        assert id(workspace) == identity
        assert len(workspace.query(ResultsWorkspaceView)) == 1


@_run_async
async def test_report_reuses_result_components_and_supports_safe_updates() -> None:
    projection = _projection()
    compare = CompareWorkspace(
        state=CompareWorkspaceState.RESULTS,
        results=projection,
    )
    report = ReportWorkspace(listing=_listing())
    app = _WorkspaceApp(compare, report)

    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        identity = id(report)
        assert len(report.query(ReportList)) == 1

        report.show_result(projection)
        await pilot.pause()
        assert id(report) == identity
        shared = app.query(ResultsWorkspaceView)
        assert len(shared) == 2
        assert type(shared[0]) is type(shared[1]) is ResultsWorkspaceView

        report.show_loading()
        await pilot.pause()
        assert report.query_one(StatePanel).mode is StatePanelMode.LOADING

        report.show_listing(_listing())
        await pilot.pause()
        assert len(report.query(ReportList)) == 1

        report.show_error("safe-code")
        await pilot.pause()
        assert report.state is ReportWorkspaceState.RESULT
        assert report.query_one(StatePanel).mode is StatePanelMode.ERROR


@_run_async
async def test_settings_controls_publish_and_update_tui_preferences_only() -> None:
    initial = TuiPreferences()
    workspace = SettingsWorkspace(initial)
    app = _WorkspaceApp(workspace)

    async with app.run_test(size=(80, 32)) as pilot:
        await pilot.pause()
        panel = workspace.query_one(TuiOnlySettingsPanel)
        panel.query_one("#tui-setting-dense", Switch).value = True
        panel.query_one("#tui-setting-mouse", Switch).value = False
        await pilot.pause()
        assert app.preferences is not None
        assert app.preferences.dense is True

        updated = TuiPreferences(
            theme=COMPARO_DARK_THEME.theme_id,
            default_workspace=WorkspaceId.COMPARE,
            dense=True,
            mouse=False,
        )
        workspace.show_preferences(updated)
        await pilot.pause()
        assert workspace.query_one(TuiOnlySettingsPanel) is panel
        assert panel.current_preferences() == updated


@_run_async
async def test_workspace_error_and_empty_states_use_shared_state_panel() -> None:
    diagnostic = ApplicationDiagnostic(
        code="project.invalid",
        severity="error",
        stage="configuration",
    )
    explorer = ExplorerWorkspace(ProjectWorkspaceResult(diagnostics=(diagnostic,)))
    report = ReportWorkspace(listing=ReportArchiveListing(entries=(), failures=()))
    app = _WorkspaceApp(explorer, report)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert explorer.query_one(StatePanel).mode is StatePanelMode.ERROR
        assert report.query_one(StatePanel).mode is StatePanelMode.EMPTY


def test_unmounted_workspace_and_settings_contracts_are_typed() -> None:
    project = _project_result()
    assert project.snapshot is not None
    draft = CompareDraft(mode=CompareMode.DIFFERENCES)
    projection = _projection()
    progress = _progress()

    explorer = ExplorerWorkspace(None)
    assert explorer.selected_resource() is None
    explorer.show_loading()
    explorer.show_result(project)
    with pytest.raises(TypeError, match="ProjectWorkspaceResult"):
        explorer.show_result(cast("ProjectWorkspaceResult", object()))

    compare = CompareWorkspace(draft=draft)
    assert compare.current_draft() is draft
    compare.show_prepare(draft, resources=project.snapshot.resources)
    compare.show_running(progress)
    compare.show_results(projection)
    with pytest.raises(TypeError, match="mode"):
        CompareWorkspace(mode=cast("CompareMode", object()))
    with pytest.raises(TypeError, match="state"):
        CompareWorkspace(state=cast("CompareWorkspaceState", object()))
    with pytest.raises(TypeError, match="draft"):
        CompareWorkspace(draft=cast("CompareDraft", object()))
    with pytest.raises(TypeError, match="draft"):
        compare.show_prepare(cast("CompareDraft", object()))
    with pytest.raises(TypeError, match="ExecutionProgressSnapshot"):
        compare.show_running(cast("ExecutionProgressSnapshot", object()))
    with pytest.raises(TypeError, match="ResultProjection"):
        compare.show_results(cast("ResultProjection", object()))

    report = ReportWorkspace()
    report.show_loading()
    report.show_listing(_listing())
    report.show_result(projection)
    report.show_error()
    with pytest.raises(TypeError, match="state"):
        ReportWorkspace(state=cast("ReportWorkspaceState", object()))
    with pytest.raises(TypeError, match="listing"):
        report.show_listing(cast("ReportArchiveListing", object()))
    with pytest.raises(TypeError, match="result"):
        report.show_result(cast("ResultProjection", object()))

    preferences = TuiPreferences()
    panel = TuiOnlySettingsPanel(preferences)
    assert panel.current_preferences() is preferences
    panel.show_preferences(preferences)
    with pytest.raises(TypeError, match="TuiPreferences"):
        TuiOnlySettingsPanel(cast("TuiPreferences", object()))
    with pytest.raises(TypeError, match="TuiPreferences"):
        panel.show_preferences(cast("TuiPreferences", object()))
    settings = SettingsWorkspace(preferences)
    settings.show_preferences(preferences)
    with pytest.raises(TypeError, match="TuiPreferences"):
        settings.show_preferences(cast("TuiPreferences", object()))

    state = StatePanel()
    state.show_state(StatePanelMode.EMPTY, "nothing")
    assert state.mode is StatePanelMode.EMPTY


@_run_async
async def test_compare_composes_every_absent_and_diagnostic_state() -> None:
    diagnostic = ApplicationDiagnostic(
        code="comparison.invalid",
        severity="error",
        stage="planning",
    )
    preparing_error = CompareWorkspace(diagnostics=(diagnostic,))
    starting = CompareWorkspace(state=CompareWorkspaceState.RUNNING)
    missing_results = CompareWorkspace(state=CompareWorkspaceState.RESULTS)
    project = _project_result()
    assert project.snapshot is not None
    project_resources = project.snapshot.resources
    app = _WorkspaceApp(preparing_error, starting, missing_results)

    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        assert preparing_error.query_one(StatePanel).mode is StatePanelMode.ERROR
        assert starting.query_one(StatePanel).mode is StatePanelMode.LOADING
        assert missing_results.query_one(StatePanel).mode is StatePanelMode.ERROR
        assert starting.current_draft().mode is CompareMode.DIFFERENCES

        preparing_error.show_prepare(CompareDraft(mode=CompareMode.ASSERTIONS))
        await pilot.pause()
        assert len(preparing_error.query(DiagnosticsList)) == 0

        starting.show_running(_progress())
        await pilot.pause()
        assert len(starting.query(CompareRunPanel)) == 1

        preparing_error.show_prepare(
            CompareDraft(mode=CompareMode.ASSERTIONS),
            resources=project_resources,
        )
        await pilot.pause()
        assert preparing_error.query_one(StatePanel).mode is StatePanelMode.CONTENT


@_run_async
async def test_report_distinguishes_invalid_archive_entries_from_empty_archive() -> None:
    failure = ReportArchiveEntryFailure(
        bundle_id="broken",
        code=ArtifactStoreErrorCode.DIGEST_MISMATCH,
    )
    report = ReportWorkspace(
        listing=ReportArchiveListing(entries=(), failures=(failure,)),
    )
    app = _WorkspaceApp(report)

    async with app.run_test(size=(80, 28)) as pilot:
        await pilot.pause()
        state = report.query_one(StatePanel)
        assert state.mode is StatePanelMode.EMPTY
        assert "invalid entries" in str(state.query_one(".state-message").render())
