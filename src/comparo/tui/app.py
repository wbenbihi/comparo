"""Textual shell for Comparo's four persistent workspaces."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import PurePath
from typing import ClassVar
from typing import final
from typing import override

from textual import on
from textual.app import App
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.events import Resize
from textual.widgets import ContentSwitcher
from textual.widgets import Tab
from textual.widgets import Tabs

from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSession
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
from comparo.tui.commands import COMMAND_CATALOG
from comparo.tui.commands import CommandDescriptor
from comparo.tui.commands import CommandId
from comparo.tui.components import COMPONENT_STYLESHEET
from comparo.tui.components import CommandFooter
from comparo.tui.components import CompareDraftForm
from comparo.tui.components import ProjectResourceList
from comparo.tui.components import ReportList
from comparo.tui.components import ResultIndex
from comparo.tui.components import TuiOnlySettingsPanel
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import TuiBackend
from comparo.tui.contracts import TuiExecutionControls
from comparo.tui.controllers import CompareSurface
from comparo.tui.controllers import ReportSurface
from comparo.tui.controllers import TuiCoordinator
from comparo.tui.controllers import available_commands
from comparo.tui.design import LayoutClass
from comparo.tui.design import layout_class
from comparo.tui.navigation import NavigationFrame
from comparo.tui.navigation import NavigationState
from comparo.tui.navigation import Route
from comparo.tui.navigation import WorkspaceId
from comparo.tui.navigation import initial_navigation
from comparo.tui.navigation import navigate_back
from comparo.tui.navigation import push_route
from comparo.tui.navigation import switch_workspace
from comparo.tui.state import ThemePreference
from comparo.tui.state import TuiPreferences
from comparo.tui.workspaces import CompareWorkspace
from comparo.tui.workspaces import CompareWorkspaceState
from comparo.tui.workspaces import ExplorerWorkspace
from comparo.tui.workspaces import ReportWorkspace
from comparo.tui.workspaces import ReportWorkspaceState
from comparo.tui.workspaces import SettingsWorkspace


def _bindings() -> list[BindingType]:
    """Build one physical-key declaration from the central semantic catalog."""
    keys = {key: command.label for command in COMMAND_CATALOG for key in command.keys}
    return [
        Binding(
            key,
            f"dispatch_catalog_key({key!r})",
            label,
            show=False,
            priority=key in {"escape", "ctrl+k"},
        )
        for key, label in keys.items()
    ]


@final
class ComparoApp(App[int]):
    """Translate Textual events into navigation and framework-neutral coordination."""

    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = COMPONENT_STYLESHEET
    DEFAULT_CSS = """
    #workspace-tabs { dock: top; }
    #workspace-content { height: 1fr; width: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = _bindings()

    def __init__(
        self,
        backend: TuiBackend,
        controls: TuiExecutionControls,
        *,
        preferences: TuiPreferences | None = None,
    ) -> None:
        """Create a shell with injected effects and TUI-only preferences."""
        if type(controls) is not TuiExecutionControls:
            raise TypeError("Comparo app execution controls must be typed")
        if preferences is not None and type(preferences) is not TuiPreferences:
            raise TypeError("Comparo app preferences must be typed")
        selected = TuiPreferences() if preferences is None else preferences
        super().__init__()
        self._system_theme = self.theme
        self._preferences = selected
        self._navigation = switch_workspace(initial_navigation(), selected.default_workspace)
        self._coordinator = TuiCoordinator(backend, controls, self._render_workspace)
        self._footer_lock = asyncio.Lock()
        self._footer_signature = tuple(command.command_id for command in self.active_commands)
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
    def active_commands(self) -> tuple[CommandDescriptor, ...]:
        """Resolve global and workspace commands from the central catalog."""
        return available_commands(
            COMMAND_CATALOG,
            self._navigation.active,
            self._coordinator.compare_surface,
            can_execute=self._coordinator.can_execute,
        )

    @override
    def compose(self) -> ComposeResult:
        """Compose one top navigation, one switcher, and one command footer."""
        active = self._navigation.active
        yield Tabs(
            *(Tab(item.value.title(), id=f"tab-{item.value}") for item in WorkspaceId),
            active=f"tab-{active.value}",
            id="workspace-tabs",
        )
        yield ContentSwitcher(
            ExplorerWorkspace(self.project_result, id="workspace-explorer"),
            self._compare_workspace(),
            self._report_workspace(),
            SettingsWorkspace(self._preferences, id="workspace-settings"),
            initial=f"workspace-{active.value}",
            id="workspace-content",
        )
        yield CommandFooter(self.active_commands, id="command-footer")

    def on_mount(self) -> None:
        """Apply presentation state and begin independent initial loads."""
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

    @on(CompareDraftForm.CompareDraftChanged)
    def update_compare_draft(self, message: CompareDraftForm.CompareDraftChanged) -> None:
        """Forward the complete typed form state without interpreting fields."""
        self._coordinator.update_draft(message.draft)

    @on(CompareDraftForm.Submitted)
    def submit_compare_draft(self, message: CompareDraftForm.Submitted) -> None:
        """Prepare the exact submitted draft."""
        self._coordinator.spawn(self.prepare_compare(message.draft))

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
        self._coordinator.spawn(self.open_report(message.bundle_id))

    @on(ProjectResourceList.ResourceSelected)
    def track_project_resource(self, message: ProjectResourceList.ResourceSelected) -> None:
        """Record Explorer-local resource navigation."""
        coordinate = message.resource.coordinate
        self._push_route(
            WorkspaceId.EXPLORER,
            "resource",
            f"{coordinate.kind}/{coordinate.resource_id}",
        )

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

    async def action_dispatch_catalog_key(self, key: str) -> None:
        """Resolve one physical key in the current semantic scopes."""
        command = next((item for item in self.active_commands if key in item.keys), None)
        if command is not None:
            await self.dispatch_command(command.command_id)

    async def dispatch_command(self, command_id: CommandId) -> None:
        """Dispatch one typed command without redeclaring keys."""
        if type(command_id) is not CommandId:
            raise TypeError("Comparo app command must be typed")
        if command_id not in {command.command_id for command in self.active_commands}:
            return
        workspace = {
            CommandId.WORKSPACE_EXPLORER: WorkspaceId.EXPLORER,
            CommandId.WORKSPACE_COMPARE: WorkspaceId.COMPARE,
            CommandId.WORKSPACE_REPORT: WorkspaceId.REPORT,
            CommandId.WORKSPACE_SETTINGS: WorkspaceId.SETTINGS,
        }.get(command_id)
        if workspace is not None:
            await self.select_workspace(workspace)
        elif command_id in {
            CommandId.NAVIGATION_BACK,
            CommandId.APP_HELP,
            CommandId.APP_PALETTE,
            CommandId.APP_QUIT,
        }:
            await self._dispatch_global(command_id)
        else:
            self._dispatch_workspace(command_id)

    async def _dispatch_global(self, command_id: CommandId) -> None:
        if command_id is CommandId.NAVIGATION_BACK:
            await self.go_back()
        elif command_id is CommandId.APP_HELP:
            self.notify(
                " · ".join(f"{item.keys[0]} {item.label}" for item in self.active_commands),
                title="Available commands",
            )
        elif command_id is CommandId.APP_PALETTE:
            self.action_command_palette()
        elif command_id is CommandId.APP_QUIT:
            await self.close_services()
            self.exit(result=0, return_code=0)

    def _dispatch_workspace(self, command_id: CommandId) -> None:
        operations = {
            CommandId.PROJECT_RELOAD: self.reload_project,
            CommandId.COMPARE_PREVIEW: self.prepare_compare,
            CommandId.COMPARE_START: self.start_compare,
            CommandId.REPORT_RELOAD: self.reload_reports,
        }
        if command_id is CommandId.COMPARE_CANCEL:
            self.cancel_compare()
        elif operation := operations.get(command_id):
            self._coordinator.spawn(operation())

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
        await self._render_footer()

    async def go_back(self) -> None:
        """Navigate within only the active workspace."""
        before = self._navigation.current
        self._navigation = navigate_back(self._navigation)
        after = self._navigation.current
        if before == after:
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

    async def reload_project(self) -> None:
        """Reload Explorer through the coordinator."""
        await self._coordinator.reload_project()

    async def reload_reports(self) -> None:
        """Reload Report through the coordinator."""
        await self._coordinator.reload_reports()

    async def open_report(self, bundle_id: str) -> None:
        """Open a stored report and retain Report-local navigation."""
        if await self._coordinator.open_report(bundle_id):
            self._push_route(WorkspaceId.REPORT, "result", bundle_id)

    async def prepare_compare(self, draft: CompareDraft | None = None) -> None:
        """Prepare a Compare draft through the coordinator."""
        if draft is None and self._shell_mounted:
            draft = self.query_one("#workspace-compare", CompareWorkspace).current_draft()
        await self._coordinator.prepare_compare(draft)

    async def start_compare(self) -> None:
        """Start the admitted comparison through the coordinator."""
        await self._coordinator.start_compare()

    def cancel_compare(self) -> bool:
        """Request cancellation through the coordinator."""
        return self._coordinator.cancel_compare()

    async def close_services(self) -> None:
        """Close the active session and backend idempotently."""
        await self._coordinator.close()

    async def _render_workspace(self, workspace: WorkspaceId) -> None:
        if not self._shell_mounted:
            return
        if workspace is WorkspaceId.EXPLORER:
            view = self.query_one("#workspace-explorer", ExplorerWorkspace)
            view.show_loading() if self.project_result is None else view.show_result(
                self.project_result
            )
        elif workspace is WorkspaceId.COMPARE:
            self._render_compare()
        elif workspace is WorkspaceId.REPORT:
            self._render_report()
        if workspace is self._navigation.active:
            await self._render_footer()

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
            if state.compare_progress is not None:
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

    async def _render_footer(self) -> None:
        if not self._shell_mounted:
            return
        commands = self.active_commands
        signature = tuple(command.command_id for command in commands)
        if signature == self._footer_signature:
            return
        async with self._footer_lock:
            if signature == self._footer_signature:
                return
            footer = self.query_one("#command-footer", CommandFooter)
            await footer.remove()
            await self.screen.mount(CommandFooter(commands, id="command-footer"))
            self._footer_signature = signature

    def _apply_responsive_class(self, width: int) -> None:
        if not self._shell_mounted:
            return
        switcher = self.query_one("#workspace-content", ContentSwitcher)
        selected = layout_class(width)
        for candidate in LayoutClass:
            switcher.remove_class(f"layout-{candidate.value}")
        switcher.add_class(f"layout-{selected.value}")

    def _apply_preferences(self, preferences: TuiPreferences) -> None:
        requested = {
            ThemePreference.SYSTEM: self._system_theme,
            ThemePreference.DARK: "textual-dark",
            ThemePreference.LIGHT: "textual-light",
        }[preferences.theme]
        if requested in self.available_themes:
            self.theme = requested
        elif self._system_theme in self.available_themes:
            self.theme = self._system_theme
        self.screen.set_class(preferences.dense, "density-dense")
        self.screen.set_class(preferences.mouse, "mouse-next-launch-enabled")
        self.screen.set_class(not preferences.mouse, "mouse-next-launch-disabled")

    def _compare_workspace(self) -> CompareWorkspace:
        state = self._coordinator
        return CompareWorkspace(
            mode=state.compare_draft.mode,
            draft=state.compare_draft,
            resources=state.project_resources,
            state=CompareWorkspaceState(state.compare_surface.value),
            plan=state.compare_plan,
            progress=state.compare_progress,
            results=state.compare_result,
            diagnostics=state.compare_diagnostics,
            id="workspace-compare",
        )

    def _report_workspace(self) -> ReportWorkspace:
        state = self._coordinator
        return ReportWorkspace(
            state=ReportWorkspaceState(state.report_surface.value),
            listing=state.report_listing,
            result=state.report_result,
            id="workspace-report",
        )

    def _push_route(self, workspace: WorkspaceId, view: str, entity: str) -> None:
        self._navigation = push_route(
            self._navigation,
            NavigationFrame(route=Route(workspace=workspace, view=view, entity=entity)),
        )


__all__ = ("ComparoApp",)
