"""The only declaration site for Comparo's semantic product commands."""

from __future__ import annotations

from comparo.tui.commands.models import CommandDescriptor
from comparo.tui.commands.models import CommandId
from comparo.tui.commands.models import CommandScope

COMMAND_CATALOG = (
    CommandDescriptor(
        command_id=CommandId.WORKSPACE_EXPLORER,
        label="Explorer",
        description="Open the project Explorer workspace.",
        scope=CommandScope.GLOBAL,
    ),
    CommandDescriptor(
        command_id=CommandId.WORKSPACE_COMPARE,
        label="Compare",
        description="Open the active comparison workspace.",
        scope=CommandScope.GLOBAL,
    ),
    CommandDescriptor(
        command_id=CommandId.WORKSPACE_REPORT,
        label="Report",
        description="Browse persisted sanitized reports.",
        scope=CommandScope.GLOBAL,
    ),
    CommandDescriptor(
        command_id=CommandId.WORKSPACE_SETTINGS,
        label="Settings",
        description="Configure the terminal application.",
        scope=CommandScope.GLOBAL,
    ),
    CommandDescriptor(
        command_id=CommandId.NAVIGATION_BACK,
        label="Back",
        description="Close the active overlay or return one local navigation level.",
        scope=CommandScope.GLOBAL,
    ),
    CommandDescriptor(
        command_id=CommandId.APP_HELP,
        label="Help",
        description="Show commands available in the current context.",
        scope=CommandScope.GLOBAL,
    ),
    CommandDescriptor(
        command_id=CommandId.APP_PALETTE,
        label="Commands",
        description="Open the command palette.",
        scope=CommandScope.GLOBAL,
        footer=False,
    ),
    CommandDescriptor(
        command_id=CommandId.APP_QUIT,
        label="Quit",
        description="Quit Comparo.",
        scope=CommandScope.GLOBAL,
    ),
    CommandDescriptor(
        command_id=CommandId.PROJECT_RELOAD,
        label="Reload",
        description="Reload and validate the current project.",
        scope=CommandScope.EXPLORER,
    ),
    CommandDescriptor(
        command_id=CommandId.COMPARE_PREVIEW,
        label="Preview",
        description="Compile the current comparison into an admitted plan preview.",
        scope=CommandScope.COMPARE,
    ),
    CommandDescriptor(
        command_id=CommandId.COMPARE_START,
        label="Execute",
        description="Start the admitted comparison plan.",
        scope=CommandScope.COMPARE,
    ),
    CommandDescriptor(
        command_id=CommandId.COMPARE_CANCEL,
        label="Cancel",
        description="Request cancellation of the active comparison.",
        scope=CommandScope.COMPARE,
    ),
    CommandDescriptor(
        command_id=CommandId.REPORT_RELOAD,
        label="Reload",
        description="Reload the authenticated report archive.",
        scope=CommandScope.REPORT,
    ),
)


__all__ = ("COMMAND_CATALOG",)
