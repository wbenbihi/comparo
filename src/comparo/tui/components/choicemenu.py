"""Committed single/multiple choices over the shared Menu navigation primitive."""

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.binding import Binding
from textual.binding import BindingType
from textual.message import Message
from textual.reactive import reactive

from comparo.tui.components.menu import Menu
from comparo.tui.components.menu import MenuItem
from comparo.tui.theme import style


class ChoiceMenu(Menu):
    """Keep cursor navigation separate from committed choices, including filtered choices."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("space", "choose", "Select", show=False)]
    selected: reactive[tuple[str, ...]] = reactive(())

    class Changed(Message):
        """A user committed a new selection; assigning state never emits this event."""

        def __init__(self, menu: "ChoiceMenu") -> None:
            """Capture the immutable committed selection."""
            super().__init__()
            self.menu = menu
            self.selected = menu.selected

        @property
        @override
        def control(self) -> "ChoiceMenu":
            """Return the semantic event owner."""
            return self.menu

    def __init__(
        self,
        *items: MenuItem,
        selected: tuple[str, ...] = (),
        multiple: bool = False,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Reuse Menu cursor, pointer, scrolling, and disabled-item behavior."""
        super().__init__(*items, initial=selected[0] if selected else None, id=id, classes=classes)
        self.multiple = multiple
        self.set_reactive(ChoiceMenu.selected, selected)

    @override
    def format_item(self, item: MenuItem) -> Text:
        """Add a terminal-native committed marker using shared theme tokens."""
        chosen = item.id in self.selected
        marker = ("[✓] " if chosen else "[ ] ") if self.multiple else ("● " if chosen else "○ ")
        prompt = Text()
        prompt.append(marker, style("ok" if chosen else "dim"))
        label = self.choice_label(item)
        prompt.append(label)
        return prompt

    def choice_label(self, item: MenuItem) -> Text:
        """Allow resource selectors to reuse their established row grammar."""
        return super().format_item(item)

    def watch_selected(self) -> None:
        """Refresh marks without dropping choices hidden by a filter."""
        if self.is_mounted:
            self._rebuild(prefer=self.selected[0] if self.selected and not self.multiple else None)

    def choose(self, item_id: str) -> None:
        """Commit an enabled item and emit one semantic change."""
        item = next((item for item in self.items if item.id == item_id), None)
        if item is None or item.disabled:
            return
        if self.multiple:
            self.selected = (
                tuple(value for value in self.selected if value != item_id)
                if item_id in self.selected
                else (*self.selected, item_id)
            )
        else:
            if self.selected == (item_id,):
                return
            self.selected = (item_id,)
        self.post_message(self.Changed(self))

    def action_choose(self) -> None:
        """Space commits the current row, matching Enter and pointer activation."""
        if self.highlighted_id is not None:
            self.choose(self.highlighted_id)

    def on_menu_selected(self, event: Menu.Selected) -> None:
        """Translate the shared Menu activation into a committed choice."""
        event.stop()
        self.choose(event.item)
