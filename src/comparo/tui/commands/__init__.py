"""Central semantic command catalog and active-context resolution."""

from comparo.tui.commands.catalog import COMMAND_CATALOG as COMMAND_CATALOG
from comparo.tui.commands.keymap import DEFAULT_KEYMAP as DEFAULT_KEYMAP
from comparo.tui.commands.keymap import DefaultCommandBinding as DefaultCommandBinding
from comparo.tui.commands.keymap import KeymapOverride as KeymapOverride
from comparo.tui.commands.keymap import default_keys as default_keys
from comparo.tui.commands.keymap import resolved_keys as resolved_keys
from comparo.tui.commands.keymap import resolved_primary_key as resolved_primary_key
from comparo.tui.commands.keymap import textual_keymap as textual_keymap
from comparo.tui.commands.models import CommandDescriptor as CommandDescriptor
from comparo.tui.commands.models import CommandId as CommandId
from comparo.tui.commands.models import CommandScope as CommandScope
from comparo.tui.commands.models import commands_for_scopes as commands_for_scopes

__all__ = (
    "COMMAND_CATALOG",
    "DEFAULT_KEYMAP",
    "CommandDescriptor",
    "CommandId",
    "CommandScope",
    "DefaultCommandBinding",
    "KeymapOverride",
    "commands_for_scopes",
    "default_keys",
    "resolved_keys",
    "resolved_primary_key",
    "textual_keymap",
)
