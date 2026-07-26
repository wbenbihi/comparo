"""Central command catalog and key-collision contracts."""

from __future__ import annotations

import msgspec
import pytest

from comparo.tui.commands.catalog import COMMAND_CATALOG
from comparo.tui.commands.keymap import DEFAULT_KEYMAP
from comparo.tui.commands.keymap import DefaultCommandBinding
from comparo.tui.commands.keymap import KeymapOverride
from comparo.tui.commands.keymap import default_keys
from comparo.tui.commands.keymap import textual_keymap
from comparo.tui.commands.models import CommandId
from comparo.tui.commands.models import CommandScope
from comparo.tui.commands.models import commands_for_scopes


def test_global_and_workspace_commands_resolve_without_collisions() -> None:
    for scope in (
        CommandScope.EXPLORER,
        CommandScope.COMPARE,
        CommandScope.REPORT,
        CommandScope.SETTINGS,
    ):
        active = commands_for_scopes(COMMAND_CATALOG, (CommandScope.GLOBAL, scope))
        keys = tuple(key for command in active for key in default_keys(command.command_id))
        assert len(keys) == len(set(keys))
        assert CommandId.APP_QUIT in {command.command_id for command in active}
    assert {item.command_id for item in DEFAULT_KEYMAP} == {
        command.command_id for command in COMMAND_CATALOG
    }


def test_command_resolution_rejects_invalid_catalogs_and_duplicate_identities() -> None:
    descriptor = COMMAND_CATALOG[0]
    with pytest.raises(TypeError, match="immutable descriptor"):
        commands_for_scopes([], (CommandScope.GLOBAL,))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable typed"):
        commands_for_scopes(COMMAND_CATALOG, ("global",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unique"):
        commands_for_scopes(
            COMMAND_CATALOG,
            (CommandScope.GLOBAL, CommandScope.GLOBAL),
        )
    with pytest.raises(ValueError, match="identifiers"):
        commands_for_scopes((descriptor, descriptor), (CommandScope.GLOBAL,))


def test_command_descriptors_reject_invalid_shapes() -> None:
    descriptor = COMMAND_CATALOG[0]
    replacements = (
        ({"command_id": "workspace.explorer"}, TypeError, "identifier"),
        ({"label": ""}, ValueError, "presentation"),
        ({"scope": "global"}, TypeError, "scope"),
        ({"footer": 1}, TypeError, "visibility"),
    )
    for changes, error, message in replacements:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(descriptor, **changes)


def test_default_and_user_keymaps_are_typed_and_separate_from_commands() -> None:
    override = KeymapOverride(command_id=CommandId.APP_PALETTE, keys=("ctrl+k",))
    assert textual_keymap((override,)) == {CommandId.APP_PALETTE.value: "ctrl+k"}
    with pytest.raises(TypeError, match="CommandId"):
        default_keys("app.palette")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at most once"):
        textual_keymap((override, override))
    with pytest.raises(ValueError, match="conflict"):
        textual_keymap((KeymapOverride(command_id=CommandId.APP_PALETTE, keys=("1",)),))
    with pytest.raises(TypeError, match="immutable typed"):
        textual_keymap([])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty"):
        msgspec.structs.replace(override, keys=())
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(override, keys=("x", "x"))
    with pytest.raises(TypeError, match="typed"):
        DefaultCommandBinding(
            command_id="app.palette",  # type: ignore[arg-type]
            keys=("ctrl+p",),
        )
