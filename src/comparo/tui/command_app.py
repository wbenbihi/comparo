"""Semantic command routing base for the Comparo Textual shell."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Coroutine
from contextlib import suppress
from typing import override

import msgspec
from textual.app import App
from textual.widgets import Input

from comparo.application.project_workspace import ProjectResourceSummary
from comparo.tui.commands import CommandDescriptor
from comparo.tui.commands import CommandId
from comparo.tui.commands import resolved_keys
from comparo.tui.components import switch_compare_draft_mode
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareMode
from comparo.tui.controllers import TuiCoordinator
from comparo.tui.controllers import TuiOperationSlot
from comparo.tui.navigation import WorkspaceId
from comparo.tui.state import TuiPreferences
from comparo.tui.workspaces import ExplorerWorkspace


class CommandRoutingApp(App[int]):
    """Implement command semantics independently from shell composition and rendering."""

    _coordinator: TuiCoordinator
    _preferences: TuiPreferences

    @property
    @abstractmethod
    def active_commands(self) -> tuple[CommandDescriptor, ...]:
        """Return commands available in the current application context."""

    async def action_dispatch_command_id(self, command_id: str) -> None:
        """Dispatch a semantic command selected by Textual's resolved key map."""
        with suppress(ValueError):
            await self.dispatch_command(CommandId(command_id))

    @override
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Expose only commands available in the active workspace and lifecycle."""
        if action != "dispatch_command_id":
            return super().check_action(action, parameters)
        if len(parameters) != 1 or type(parameters[0]) is not str:
            return False
        with suppress(ValueError):
            command_id = CommandId(parameters[0])
            if isinstance(self.focused, Input) and any(
                len(key) == 1 for key in resolved_keys(command_id, self._preferences.keymap)
            ):
                return False
            return command_id in {command.command_id for command in self.active_commands}
        return False

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
            await self._dispatch_workspace(command_id)

    async def _dispatch_global(self, command_id: CommandId) -> None:
        if command_id is CommandId.NAVIGATION_BACK:
            await self.go_back()
        elif command_id is CommandId.APP_HELP:
            self.notify(
                " · ".join(
                    f"{self._display_key(item.command_id)} {item.label}"
                    for item in self.active_commands
                ),
                title="Available commands",
            )
        elif command_id is CommandId.APP_PALETTE:
            self.action_command_palette()
        elif command_id is CommandId.APP_QUIT:
            await self.close_services()
            self.exit(result=0, return_code=0)

    async def _dispatch_workspace(self, command_id: CommandId) -> None:
        operations = {
            CommandId.PROJECT_RELOAD: (self.reload_project, TuiOperationSlot.PROJECT),
            CommandId.COMPARE_PREVIEW: (self.prepare_compare, TuiOperationSlot.COMPARE),
            CommandId.COMPARE_START: (self.start_compare, TuiOperationSlot.COMPARE),
            CommandId.REPORT_RELOAD: (self.reload_reports, TuiOperationSlot.REPORT),
        }
        if command_id is CommandId.COMPARE_CANCEL:
            self.cancel_compare()
        elif command_id is CommandId.PROJECT_FILTER:
            search = self.query_one("#explorer-search", Input)
            search.focus()
            search.action_end()
        elif command_id is CommandId.PROJECT_USE_IN_COMPARE:
            resource = self.query_one("#workspace-explorer", ExplorerWorkspace).selected_resource()
            if resource is not None:
                await self._use_resource_in_compare(resource)
        elif selected := operations.get(command_id):
            operation, slot = selected
            self._spawn_operation(operation(), slot)

    async def _use_resource_in_compare(self, resource: ProjectResourceSummary) -> None:
        """Transfer only safe resource identity into the Compare target draft."""
        kind = resource.coordinate.kind
        resource_id = resource.coordinate.resource_id
        draft = self._coordinator.compare_draft
        if kind == "ExecutionProfile":
            draft = switch_compare_draft_mode(
                draft,
                CompareMode.EXECUTION_PROFILE,
                self._coordinator.project_resources,
            )
            draft = msgspec.structs.replace(
                draft,
                execution_profile=resource_id,
            )
        elif kind in {"Request", "Scenario"}:
            draft = switch_compare_draft_mode(
                draft,
                CompareMode.DIFFERENCES,
                self._coordinator.project_resources,
            )
            draft = msgspec.structs.replace(
                draft,
                subjects=(resource_id,),
            )
        else:
            return
        self._coordinator.update_draft(draft)
        await self._render_workspace(WorkspaceId.COMPARE)
        await self.select_workspace(WorkspaceId.COMPARE)

    @abstractmethod
    async def select_workspace(self, workspace: WorkspaceId) -> None:
        """Switch the active persistent workspace."""

    @abstractmethod
    async def go_back(self) -> None:
        """Navigate one local frame backward."""

    @abstractmethod
    async def reload_project(self) -> None:
        """Reload the project catalog."""

    @abstractmethod
    async def reload_reports(self) -> None:
        """Reload the report archive."""

    @abstractmethod
    async def prepare_compare(self, draft: CompareDraft | None = None) -> None:
        """Prepare a Compare target."""

    @abstractmethod
    async def start_compare(self) -> None:
        """Start the admitted plan."""

    @abstractmethod
    def cancel_compare(self) -> bool:
        """Cancel the active run."""

    @abstractmethod
    async def close_services(self) -> None:
        """Close frontend services."""

    @abstractmethod
    async def _render_workspace(self, workspace: WorkspaceId) -> None:
        """Render controller state into one mounted workspace."""

    @abstractmethod
    def _display_key(self, command_id: CommandId) -> str:
        """Return one resolved command key for human display."""

    @abstractmethod
    def _spawn_operation(
        self,
        operation: Coroutine[object, object, None],
        slot: TuiOperationSlot,
    ) -> None:
        """Start one coalesced frontend operation."""


__all__ = ("CommandRoutingApp",)
