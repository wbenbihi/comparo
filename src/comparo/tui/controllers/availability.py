"""Pure command availability derived from workspace and Compare lifecycle state."""

from __future__ import annotations

from comparo.tui.commands import CommandDescriptor
from comparo.tui.commands import CommandId
from comparo.tui.commands import CommandScope
from comparo.tui.commands import commands_for_scopes
from comparo.tui.controllers.coordinator import CompareSurface
from comparo.tui.navigation import WorkspaceId

_COMPARE_COMMANDS = {
    CommandId.COMPARE_PREVIEW,
    CommandId.COMPARE_START,
    CommandId.COMPARE_CANCEL,
}


def available_commands(
    catalog: tuple[CommandDescriptor, ...],
    workspace: WorkspaceId,
    surface: CompareSurface,
    *,
    can_execute: bool,
) -> tuple[CommandDescriptor, ...]:
    """Filter centrally declared commands through the active lifecycle state."""
    scoped = commands_for_scopes(
        catalog,
        (CommandScope.GLOBAL, CommandScope[workspace.name]),
    )
    if workspace is not WorkspaceId.COMPARE:
        return scoped
    allowed = {
        CompareSurface.PREPARE: {CommandId.COMPARE_PREVIEW},
        CompareSurface.RUNNING: {CommandId.COMPARE_CANCEL},
        CompareSurface.RESULTS: set(),
    }[surface]
    if surface is CompareSurface.PREPARE and can_execute:
        allowed.add(CommandId.COMPARE_START)
    return tuple(
        command
        for command in scoped
        if command.command_id not in _COMPARE_COMMANDS or command.command_id in allowed
    )


__all__ = ("available_commands",)
