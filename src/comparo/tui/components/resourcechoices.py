"""Resource choices using the same identity rendering as Explorer."""

from typing import override

from rich.text import Text

from comparo.tui.components.choicemenu import ChoiceMenu
from comparo.tui.components.menu import MenuItem
from comparo.tui.components.resourcelabel import resource_label
from comparo.tui.models.explorer import ResourceEntry
from comparo.tui.theme import style


class ResourceChoices(ChoiceMenu):
    """A committed resource selection with named, colored entity rows."""

    def load_entries(self, entries: tuple[ResourceEntry, ...]) -> None:
        """Refresh visible resources while preserving committed hidden choices."""
        self._entries = {entry.summary.coordinate.resource_id: entry for entry in entries}
        self.items = tuple(
            MenuItem(key, entry.summary.name or key) for key, entry in self._entries.items()
        )

    @override
    def choice_label(self, item: MenuItem) -> Text:
        """Share method colors, glyphs and role badges with Explorer."""
        entry = self._entries.get(item.id)
        if entry is None:
            return super().choice_label(item)
        label = resource_label(entry)
        if entry.facts.route:
            label.append(f"  {entry.facts.route}")
        return label


class ProfileChoices(ResourceChoices):
    """Starting-point cards with profile purpose and source identity."""

    @override
    def choice_label(self, item: MenuItem) -> Text:
        """Retain the shared profile glyph and add its safe descriptive context."""
        label = super().choice_label(item)
        entry = self._entries.get(item.id)
        if entry is not None:
            if entry.summary.description:
                label.append("\n  " + entry.summary.description, style("muted"))
            label.append("\n  Source: " + item.id + "\n", style("dim"))
        return label
