"""Default physical keys and framework-neutral user key-map overrides."""

from __future__ import annotations

from typing import Final
from typing import final

import msgspec

from comparo.tui.commands.models import CommandId


@final
class DefaultCommandBinding(msgspec.Struct, frozen=True, kw_only=True):
    """Default physical keys for one stable semantic command identity."""

    command_id: CommandId
    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require unique bounded keys and a typed command identity."""
        if type(self.command_id) is not CommandId:
            raise TypeError("default key binding command must be typed")
        if (
            type(self.keys) is not tuple
            or not self.keys
            or any(type(key) is not str or not key or len(key) > 32 for key in self.keys)
        ):
            raise ValueError("default command keys must be bounded and non-empty")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("default command keys must be unique")


@final
class KeymapOverride(msgspec.Struct, frozen=True, kw_only=True):
    """A future persisted replacement for one Textual binding ID."""

    command_id: CommandId
    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject untyped, empty, duplicate, or unbounded remaps."""
        if type(self.command_id) is not CommandId:
            raise TypeError("key-map override command must be typed")
        if (
            type(self.keys) is not tuple
            or not self.keys
            or any(type(key) is not str or not key or len(key) > 32 for key in self.keys)
        ):
            raise ValueError("key-map override keys must be bounded and non-empty")
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("key-map override keys must be unique")


DEFAULT_KEYMAP: Final = (
    DefaultCommandBinding(command_id=CommandId.WORKSPACE_EXPLORER, keys=("1",)),
    DefaultCommandBinding(command_id=CommandId.WORKSPACE_COMPARE, keys=("2",)),
    DefaultCommandBinding(command_id=CommandId.WORKSPACE_REPORT, keys=("3",)),
    DefaultCommandBinding(command_id=CommandId.WORKSPACE_SETTINGS, keys=("4",)),
    DefaultCommandBinding(
        command_id=CommandId.NAVIGATION_BACK,
        keys=("escape", "backspace"),
    ),
    DefaultCommandBinding(command_id=CommandId.APP_HELP, keys=("?",)),
    DefaultCommandBinding(command_id=CommandId.APP_PALETTE, keys=("ctrl+p",)),
    DefaultCommandBinding(command_id=CommandId.APP_QUIT, keys=("q",)),
    DefaultCommandBinding(command_id=CommandId.PROJECT_RELOAD, keys=("r",)),
    DefaultCommandBinding(command_id=CommandId.PROJECT_FILTER, keys=("/",)),
    DefaultCommandBinding(command_id=CommandId.PROJECT_USE_IN_COMPARE, keys=("c",)),
    DefaultCommandBinding(command_id=CommandId.COMPARE_PREVIEW, keys=("p",)),
    DefaultCommandBinding(command_id=CommandId.COMPARE_START, keys=("x",)),
    DefaultCommandBinding(command_id=CommandId.COMPARE_CANCEL, keys=("a",)),
    DefaultCommandBinding(command_id=CommandId.REPORT_RELOAD, keys=("shift+r",)),
)


def default_keys(command_id: CommandId) -> tuple[str, ...]:
    """Return the immutable default keys for one known command."""
    if type(command_id) is not CommandId:
        raise TypeError("default key lookup requires CommandId")
    binding = next((item for item in DEFAULT_KEYMAP if item.command_id is command_id), None)
    if binding is None:
        raise ValueError("every command must have a default key binding")
    return binding.keys


def textual_keymap(overrides: tuple[KeymapOverride, ...]) -> dict[str, str]:
    """Translate validated overrides to Textual's native binding-ID key map."""
    if type(overrides) is not tuple or any(type(item) is not KeymapOverride for item in overrides):
        raise TypeError("key-map overrides must be an immutable typed tuple")
    command_ids = tuple(item.command_id for item in overrides)
    if len(set(command_ids)) != len(command_ids):
        raise ValueError("key-map overrides must contain each command at most once")
    effective = {item.command_id: item.keys for item in DEFAULT_KEYMAP}
    effective.update({item.command_id: item.keys for item in overrides})
    _validate_contextual_collisions(effective)
    return {item.command_id.value: ",".join(item.keys) for item in overrides}


def resolved_primary_key(
    command_id: CommandId,
    overrides: tuple[KeymapOverride, ...],
) -> str:
    """Return the primary effective key used by help and action labels."""
    if type(command_id) is not CommandId:
        raise TypeError("resolved key lookup requires CommandId")
    mapped = textual_keymap(overrides).get(command_id.value)
    return default_keys(command_id)[0] if mapped is None else mapped.partition(",")[0]


def resolved_keys(
    command_id: CommandId,
    overrides: tuple[KeymapOverride, ...],
) -> tuple[str, ...]:
    """Return every effective physical key for one semantic command."""
    if type(command_id) is not CommandId:
        raise TypeError("resolved key lookup requires CommandId")
    override = next((item for item in overrides if item.command_id is command_id), None)
    return default_keys(command_id) if override is None else override.keys


def _validate_contextual_collisions(effective: dict[CommandId, tuple[str, ...]]) -> None:
    from comparo.tui.commands.catalog import COMMAND_CATALOG
    from comparo.tui.commands.models import CommandScope
    from comparo.tui.commands.models import commands_for_scopes

    for scope in (
        CommandScope.EXPLORER,
        CommandScope.COMPARE,
        CommandScope.REPORT,
        CommandScope.SETTINGS,
    ):
        active = commands_for_scopes(COMMAND_CATALOG, (CommandScope.GLOBAL, scope))
        owners: dict[str, CommandId] = {}
        for command in active:
            for key in effective[command.command_id]:
                owner = owners.setdefault(key, command.command_id)
                if owner is not command.command_id:
                    raise ValueError(f"key-map conflict in {scope.value} context")


__all__ = (
    "DEFAULT_KEYMAP",
    "DefaultCommandBinding",
    "KeymapOverride",
    "default_keys",
    "resolved_keys",
    "resolved_primary_key",
    "textual_keymap",
)
