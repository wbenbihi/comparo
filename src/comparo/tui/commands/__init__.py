"""Central semantic command catalog and active-context resolution."""

from comparo.tui.commands.catalog import COMMAND_CATALOG as COMMAND_CATALOG
from comparo.tui.commands.models import CommandDescriptor as CommandDescriptor
from comparo.tui.commands.models import CommandId as CommandId
from comparo.tui.commands.models import CommandScope as CommandScope
from comparo.tui.commands.models import commands_for_scopes as commands_for_scopes

__all__ = (
    "COMMAND_CATALOG",
    "CommandDescriptor",
    "CommandId",
    "CommandScope",
    "commands_for_scopes",
)
