"""Central command catalog and key-collision contracts."""

from __future__ import annotations

import msgspec
import pytest

from comparo.tui.commands.catalog import COMMAND_CATALOG
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
        keys = tuple(key for command in active for key in command.keys)
        assert len(keys) == len(set(keys))
        assert CommandId.APP_QUIT in {command.command_id for command in active}


def test_command_resolution_rejects_invalid_catalogs_and_collisions() -> None:
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
    colliding = msgspec.structs.replace(
        COMMAND_CATALOG[1],
        command_id=CommandId.WORKSPACE_COMPARE,
        keys=descriptor.keys,
    )
    with pytest.raises(ValueError, match="keys must not collide"):
        commands_for_scopes((descriptor, colliding), (CommandScope.GLOBAL,))


def test_command_descriptors_reject_invalid_shapes() -> None:
    descriptor = COMMAND_CATALOG[0]
    replacements = (
        ({"command_id": "workspace.explorer"}, TypeError, "identifier"),
        ({"label": ""}, ValueError, "presentation"),
        ({"keys": ()}, ValueError, "keys"),
        ({"keys": ("1", "1")}, ValueError, "unique"),
        ({"scope": "global"}, TypeError, "scope"),
        ({"footer": 1}, TypeError, "visibility"),
    )
    for changes, error, message in replacements:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(descriptor, **changes)
