"""Framework-neutral keyboard command contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec


class CommandId(StrEnum):
    """Stable user intentions dispatched independently from physical keys."""

    WORKSPACE_EXPLORER = "workspace.explorer"
    WORKSPACE_COMPARE = "workspace.compare"
    WORKSPACE_REPORT = "workspace.report"
    WORKSPACE_SETTINGS = "workspace.settings"
    NAVIGATION_BACK = "navigation.back"
    APP_HELP = "app.help"
    APP_PALETTE = "app.palette"
    APP_QUIT = "app.quit"
    PROJECT_RELOAD = "project.reload"
    COMPARE_PREVIEW = "compare.preview"
    COMPARE_START = "compare.start"
    COMPARE_CANCEL = "compare.cancel"
    REPORT_RELOAD = "report.reload"


class CommandScope(StrEnum):
    """Command availability scopes resolved by the active UI context."""

    GLOBAL = "global"
    EXPLORER = "workspace.explorer"
    COMPARE = "workspace.compare"
    REPORT = "workspace.report"
    SETTINGS = "workspace.settings"


@final
class CommandDescriptor(msgspec.Struct, frozen=True, kw_only=True):
    """Canonical label and default keys for one semantic command."""

    command_id: CommandId
    label: str
    description: str
    keys: tuple[str, ...]
    scope: CommandScope
    footer: bool = True
    palette: bool = True

    def __post_init__(self) -> None:
        """Require bounded labels, unique keys, and a canonical identifier."""
        if type(self.command_id) is not CommandId:
            raise TypeError("command identifier must be typed")
        if any(
            type(value) is not str or not value or len(value) > maximum
            for value, maximum in ((self.label, 64), (self.description, 256))
        ):
            raise ValueError("command presentation text must be bounded and non-empty")
        if (
            type(self.keys) is not tuple
            or not self.keys
            or any(type(key) is not str or not key or len(key) > 32 for key in self.keys)
        ):
            raise ValueError("command keys must be a non-empty immutable text tuple")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("command keys must be unique")
        if type(self.scope) is not CommandScope:
            raise TypeError("command scope must be typed")
        if type(self.footer) is not bool or type(self.palette) is not bool:
            raise TypeError("command visibility flags must be bool")


def commands_for_scopes(
    catalog: tuple[CommandDescriptor, ...],
    scopes: tuple[CommandScope, ...],
) -> tuple[CommandDescriptor, ...]:
    """Resolve active commands and fail closed on a physical-key collision."""
    if type(catalog) is not tuple or any(
        type(command) is not CommandDescriptor for command in catalog
    ):
        raise TypeError("command catalog must be an immutable descriptor tuple")
    if type(scopes) is not tuple or any(type(scope) is not CommandScope for scope in scopes):
        raise TypeError("command scopes must be an immutable typed tuple")
    if len(set(scopes)) != len(scopes):
        raise ValueError("command scopes must be unique")
    active = tuple(command for command in catalog if command.scope in scopes)
    identifiers = tuple(command.command_id for command in active)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("active command identifiers must be unique")
    keys: dict[str, CommandId] = {}
    for command in active:
        for key in command.keys:
            if key in keys:
                raise ValueError("active command keys must not collide")
            keys[key] = command.command_id
    return active


__all__ = (
    "CommandDescriptor",
    "CommandId",
    "CommandScope",
    "commands_for_scopes",
)
