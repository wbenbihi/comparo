"""Textual shell for Comparo's four persistent workspaces."""

from __future__ import annotations

from collections.abc import Coroutine
from contextlib import suppress
from pathlib import PurePath
from typing import ClassVar
from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.events import Resize
from textual.widgets import ContentSwitcher
from textual.widgets import Footer
from textual.widgets import Tabs

from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSession
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
from comparo.tui.bindings import textual_bindings
from comparo.tui.command_app import CommandRoutingApp
from comparo.tui.commands import COMMAND_CATALOG
from comparo.tui.commands import CommandDescriptor
from comparo.tui.commands import CommandId
from comparo.tui.commands import resolved_primary_key
from comparo.tui.components import COMPONENT_STYLESHEET
from comparo.tui.components import ComparoShellBar
from comparo.tui.components import ProjectResourceList
from comparo.tui.components import ReportList
from comparo.tui.components import ResultIndex
from comparo.tui.components import TuiOnlySettingsPanel
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import TuiBackend
from comparo.tui.contracts import TuiExecutionControls
from comparo.tui.contracts import TuiInteractionLimits
from comparo.tui.contracts import tui_interaction_limits_defaults_v1
from comparo.tui.controllers import CompareSurface
from comparo.tui.controllers import ReportSurface
from comparo.tui.controllers import TuiCoordinator
from comparo.tui.controllers import TuiOperationSlot
from comparo.tui.controllers import available_commands
from comparo.tui.layout_adapter import apply_responsive_class
from comparo.tui.layout_adapter import focus_workspace
from comparo.tui.navigation import NavigationFrame
from comparo.tui.navigation import NavigationState
from comparo.tui.navigation import Route
from comparo.tui.navigation import WorkspaceId
from comparo.tui.navigation import initial_navigation
from comparo.tui.navigation import navigate_back
from comparo.tui.navigation import push_route
from comparo.tui.navigation import switch_workspace
from comparo.tui.selection import restore_frame
from comparo.tui.state import TuiPreferences
from comparo.tui.theme_adapter import apply_tui_preferences
from comparo.tui.theme_adapter import register_builtin_themes
from comparo.tui.workspace_views import compare_workspace_view
from comparo.tui.workspace_views import report_workspace_view
from comparo.tui.workspaces import CompareWorkspace
from comparo.tui.workspaces import CompareWorkspaceState
from comparo.tui.workspaces import ExplorerWorkspace
from comparo.tui.workspaces import ReportWorkspace
from comparo.tui.workspaces import SettingsWorkspace


@final
class ComparoApp(CommandRoutingApp):
    """Translate Textual events into navigation and framework-neutral coordination."""

    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = COMPONENT_STYLESHEET
    DEFAULT_CSS = """
    #shell-bar { dock: top; }
    #workspace-content { height: 1fr; width: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = textual_bindings(None)

    def __init__(
        self,
        backend: TuiBackend,
        controls: TuiExecutionControls,
        *,
        preferences: TuiPreferences | None = None,
        interaction_limits: TuiInteractionLimits | None = None,
    ) -> None:
        """Create a shell with injected effects and TUI-only preferences."""
        if type(controls) is not TuiExecutionControls:
            raise TypeError("Comparo app execution controls must be typed")
        if preferences is not None and type(preferences) is not TuiPreferences:
            raise TypeError("Comparo app preferences must be typed")
        selected_limits = (
            tui_interaction_limits_defaults_v1()
            if interaction_limits is None
            else interaction_limits
        )
        if type(selected_limits) is not TuiInteractionLimits:
            raise TypeError("Comparo app interaction limits must be typed")
        selected = TuiPreferences() if preferences is None else preferences
        super().__init__()
        self.title = "comparo"
        self._system_theme = self.theme
        register_builtin_themes(self)
        self._preferences = selected
        self._interaction_limits = selected_limits
        self._navigation = switch_workspace(initial_navigation(), selected.default_workspace)
        self._coordinator = TuiCoordinator(backend, controls, self._render_workspace)
        self._shell_mounted = False

    @property
    def navigation(self) -> NavigationState:
        """Return the immutable independent workspace histories."""
        return self._navigation

    @property
    def project_result(self) -> ProjectWorkspaceResult | None:
        """Return the latest safe Explorer result."""
        return self._coordinator.project

    @property
    def report_listing(self) -> ReportArchiveListing | None:
        """Return the latest authenticated report listing."""
        return self._coordinator.report_listing

    @property
    def report_error_code(self) -> str | None:
        """Return a safe closed report failure code."""
        return self._coordinator.report_error_code

    @property
    def compare_plan(self) -> PlanInspection | None:
        """Return the latest admitted safe plan."""
        return self._coordinator.compare_plan

    @property
    def compare_progress(self) -> ExecutionProgressSnapshot | None:
        """Return the latest coalesced execution progress."""
        return self._coordinator.compare_progress

    @property
    def compare_result(self) -> ResultProjection | None:
        """Return the terminal manifest-only live result."""
        return self._coordinator.compare_result

    @property
    def compare_diagnostics(self) -> tuple[ApplicationDiagnostic, ...]:
        """Return safe preparation or execution diagnostics."""
        return self._coordinator.compare_diagnostics

    @property
    def active_session(self) -> ExecutionSession | None:
        """Return the active session, if any."""
        return self._coordinator.session

    @property
    @override
    def active_commands(self) -> tuple[CommandDescriptor, ...]:
        """Resolve global and workspace commands from the central catalog."""
        commands = available_commands(
            COMMAND_CATALOG,
            self._navigation.active,
            self._coordinator.compare_surface,
            can_execute=self._coordinator.can_execute,
        )
        if self._navigation.active is not WorkspaceId.COMPARE or not self._shell_mounted:
            return commands
        stage = self.query_one("#workspace-compare", CompareWorkspace).lifecycle_stage
        excluded = {
            "Target": {CommandId.COMPARE_START, CommandId.COMPARE_CANCEL},
            "Plan": {CommandId.COMPARE_PREVIEW, CommandId.COMPARE_CANCEL},
            "Run": {CommandId.COMPARE_PREVIEW, CommandId.COMPARE_START},
            "Analyze": {
                CommandId.COMPARE_PREVIEW,
                CommandId.COMPARE_START,
                CommandId.COMPARE_CANCEL,
            },
        }[stage]
        return tuple(command for command in commands if command.command_id not in excluded)

    @override
    def compose(self) -> ComposeResult:
        """Compose one top navigation, one switcher, and one command footer."""
        active = self._navigation.active
        yield ComparoShellBar(active, self.project_result, id="shell-bar")
        yield ContentSwitcher(
            ExplorerWorkspace(self.project_result, id="workspace-explorer"),
            compare_workspace_view(self._coordinator, self._interaction_limits),
            report_workspace_view(self._coordinator),
            SettingsWorkspace(self._preferences, id="workspace-settings"),
            initial=f"workspace-{active.value}",
            id="workspace-content",
        )
        yield Footer(id="command-footer")

    def on_mount(self) -> None:
        """Apply presentation state and begin ordered project/archive loading."""
        self._shell_mounted = True
        self._apply_preferences(self._preferences)
        self._apply_responsive_class(self.screen.size.width)
        self._coordinator.begin()

    async def on_unmount(self) -> None:
        """Close backend effects when Textual tears down the app."""
        self._shell_mounted = False
        await self._coordinator.close()

    @override
    def _handle_exception(self, error: Exception) -> None:
        """Exit with fixed text instead of Textual's value-bearing Rich traceback."""
        del error
        try:
            self.exit(result=1, return_code=1, message="Comparo encountered an internal error.")
        except Exception:  # noqa: BLE001 - fixed-text minimal exit is the safety fallback
            self.exit(result=1, return_code=1)

    def on_resize(self, event: Resize) -> None:
        """Drive the root responsive class through the shared breakpoint policy."""
        self._apply_responsive_class(event.size.width)

    @on(Tabs.TabActivated)
    async def activate_tab(self, event: Tabs.TabActivated) -> None:
        """Translate a current top tab into one workspace switch."""
        tab_id = event.tab.id or ""
        tabs = self.query_one("#workspace-tabs", Tabs)
        if not tab_id.startswith("tab-") or tabs.active != tab_id:
            return
        with suppress(ValueError):
            workspace = WorkspaceId(tab_id.removeprefix("tab-"))
            if workspace is not self._navigation.active:
                await self.select_workspace(workspace)

    @on(CompareWorkspace.DraftChanged)
    def update_compare_target(self, message: CompareWorkspace.DraftChanged) -> None:
        """Forward the native Target panel's complete typed draft."""
        self._coordinator.update_draft(message.draft)

    @on(CompareWorkspace.PlanRequested)
    def request_compare_plan(self, message: CompareWorkspace.PlanRequested) -> None:
        """Prepare the exact target submitted by the native Compare surface."""
        self._spawn_operation(
            self.prepare_compare(message.draft),
            TuiOperationSlot.COMPARE,
        )

    @on(CompareWorkspace.ExecuteRequested)
    def request_compare_execution(self) -> None:
        """Start the current admitted plan from its pointer action."""
        self._spawn_operation(self.start_compare(), TuiOperationSlot.COMPARE)

    @on(CompareWorkspace.CancelRequested)
    def request_compare_cancellation(self) -> None:
        """Cancel the active run from its pointer action."""
        self.cancel_compare()

    @on(CompareWorkspace.SurfaceChanged)
    def compare_surface_changed(self) -> None:
        """Refresh contextual commands after a local Target/Plan transition."""
        self.refresh_bindings()

    @on(TuiOnlySettingsPanel.PreferencesChanged)
    def update_preferences(self, message: TuiOnlySettingsPanel.PreferencesChanged) -> None:
        """Apply local presentation preferences only."""
        mouse_changed = message.preferences.mouse is not self._preferences.mouse
        self._preferences = message.preferences
        self._apply_preferences(message.preferences)
        if mouse_changed:
            self.notify("The mouse preference applies on the next launch.")

    @on(ReportList.ReportSelected)
    def select_report(self, message: ReportList.ReportSelected) -> None:
        """Open an authenticated stored report."""
        self._spawn_operation(
            self.open_report(message.bundle_id),
            TuiOperationSlot.REPORT,
        )

    @on(ProjectResourceList.ResourceSelected)
    def track_project_resource(self, message: ProjectResourceList.ResourceSelected) -> None:
        """Record Explorer-local resource navigation."""
        coordinate = message.resource.coordinate
        self._push_route(
            WorkspaceId.EXPLORER,
            "resource",
            f"{coordinate.kind}/{coordinate.resource_id}",
        )

    @on(ExplorerWorkspace.UseInCompareRequested)
    async def use_requested_resource(
        self,
        message: ExplorerWorkspace.UseInCompareRequested,
    ) -> None:
        """Seed Compare from the Explorer button through the semantic command path."""
        await self._use_resource_in_compare(message.resource)

    @on(ResultIndex.ResultSelected)
    def track_result_selection(self, message: ResultIndex.ResultSelected) -> None:
        """Record result detail navigation in its active owning workspace."""
        workspace = self._navigation.active
        if workspace in {WorkspaceId.COMPARE, WorkspaceId.REPORT}:
            self._push_route(
                workspace,
                "result-detail",
                f"{message.reference.entity.value}:{message.reference.result_id}",
            )

    @override
    async def select_workspace(self, workspace: WorkspaceId) -> None:
        """Switch workspace without changing any other local history or effect."""
        if type(workspace) is not WorkspaceId:
            raise TypeError("Comparo workspace selection must be typed")
        self._navigation = switch_workspace(self._navigation, workspace)
        content_id = f"workspace-{workspace.value}"
        tab_id = f"tab-{workspace.value}"
        self.query_one("#workspace-content", ContentSwitcher).current = content_id
        tabs = self.query_one("#workspace-tabs", Tabs)
        if tabs.active != tab_id:
            tabs.active = tab_id
        restore_frame(self, self._navigation.current)
        self._focus_active_workspace()
        self.call_after_refresh(self._focus_active_workspace)
        self.refresh_bindings()

    @override
    async def go_back(self) -> None:
        """Navigate within only the active workspace."""
        before = self._navigation.current
        self._navigation = navigate_back(self._navigation)
        after = self._navigation.current
        if before == after:
            if after.route.workspace is WorkspaceId.COMPARE:
                compare = self.query_one("#workspace-compare", CompareWorkspace)
                if compare.state is CompareWorkspaceState.PREPARE and compare.reviewing_plan:
                    compare.edit_target()
                    self.refresh_bindings()
                    return
            if (
                after.route.workspace is WorkspaceId.COMPARE
                and self._coordinator.compare_surface is CompareSurface.RESULTS
            ):
                self._coordinator.show_compare_prepare()
                await self._render_workspace(WorkspaceId.COMPARE)
            return
        if after.route.workspace is WorkspaceId.REPORT and after.route.view == "root":
            self._coordinator.show_report_archive()
        await self._render_workspace(after.route.workspace)
        restore_frame(self, after)

    @override
    async def reload_project(self) -> None:
        """Reload Explorer through the coordinator."""
        await self._coordinator.reload_project()

    @override
    async def reload_reports(self) -> None:
        """Reload Report through the coordinator."""
        await self._coordinator.reload_reports()

    async def open_report(self, bundle_id: str) -> None:
        """Open a stored report and retain Report-local navigation."""
        if await self._coordinator.open_report(bundle_id):
            self._push_route(WorkspaceId.REPORT, "result", bundle_id)

    @override
    async def prepare_compare(self, draft: CompareDraft | None = None) -> None:
        """Prepare a Compare draft through the coordinator."""
        if draft is None and self._shell_mounted:
            draft = self.query_one("#workspace-compare", CompareWorkspace).current_draft()
        await self._coordinator.prepare_compare(draft)

    @override
    async def start_compare(self) -> None:
        """Start the admitted comparison through the coordinator."""
        await self._coordinator.start_compare()

    @override
    def cancel_compare(self) -> bool:
        """Request cancellation through the coordinator."""
        return self._coordinator.cancel_compare()

    @override
    async def close_services(self) -> None:
        """Close the active session and backend idempotently."""
        await self._coordinator.close()

    @override
    async def _render_workspace(self, workspace: WorkspaceId) -> None:
        if not self._shell_mounted:
            return
        if workspace is WorkspaceId.EXPLORER:
            view = self.query_one("#workspace-explorer", ExplorerWorkspace)
            view.show_loading() if self.project_result is None else view.show_result(
                self.project_result
            )
            self.query_one(ComparoShellBar).show_project(self.project_result)
        elif workspace is WorkspaceId.COMPARE:
            self._render_compare()
        elif workspace is WorkspaceId.REPORT:
            self._render_report()
        if workspace is self._navigation.active:
            self._focus_active_workspace()
            self.call_after_refresh(self._focus_active_workspace)
            self.refresh_bindings()

    def _render_compare(self) -> None:
        view = self.query_one("#workspace-compare", CompareWorkspace)
        state = self._coordinator
        if state.compare_surface is CompareSurface.PREPARE:
            view.show_prepare(
                state.compare_draft,
                plan=state.compare_plan,
                diagnostics=state.compare_diagnostics,
                resources=state.project_resources,
            )
        elif state.compare_surface is CompareSurface.RUNNING:
            if state.compare_progress is None:
                view.show_starting()
            else:
                view.show_running(state.compare_progress)
        elif state.compare_result is not None:
            view.show_results(state.compare_result)

    def _render_report(self) -> None:
        view = self.query_one("#workspace-report", ReportWorkspace)
        state = self._coordinator
        if state.report_surface is ReportSurface.RESULT:
            view.show_error() if state.report_result is None else view.show_result(
                state.report_result
            )
        elif state.report_listing is None:
            view.show_loading()
        else:
            view.show_listing(state.report_listing)

    def _apply_responsive_class(self, width: int) -> None:
        if not self._shell_mounted:
            return
        apply_responsive_class(self.query_one("#workspace-content", ContentSwitcher), width)

    def _apply_preferences(self, preferences: TuiPreferences) -> None:
        apply_tui_preferences(self, preferences, self._system_theme)

    @override
    def _display_key(self, command_id: CommandId) -> str:
        """Return the resolved primary key for help and action labels."""
        return resolved_primary_key(command_id, self._preferences.keymap)

    def _focus_active_workspace(self) -> None:
        """Keep native contextual bindings active without stealing local focus."""
        if not self._shell_mounted:
            return
        focus_workspace(
            self.query_one(f"#workspace-{self._navigation.active.value}"),
            self.focused,
        )

    def _push_route(self, workspace: WorkspaceId, view: str, entity: str) -> None:
        self._navigation = push_route(
            self._navigation,
            NavigationFrame(route=Route(workspace=workspace, view=view, entity=entity)),
            maximum_frames_per_workspace=(
                self._interaction_limits.maximum_navigation_frames_per_workspace
            ),
        )

    @override
    def _spawn_operation(
        self,
        operation: Coroutine[object, object, None],
        slot: TuiOperationSlot,
    ) -> None:
        """Submit one coalesced operation and explain a duplicate request."""
        if not self._coordinator.spawn(operation, slot=slot):
            self.notify("That operation is already in progress.")


__all__ = ("ComparoApp",)
