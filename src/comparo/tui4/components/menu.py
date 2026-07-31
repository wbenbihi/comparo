"""Menu — semantic list menu used across workspaces."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from typing import Self

from textual.message import Message
from textual.reactive import reactive
from textual.widgets import OptionList
from textual.widgets.option_list import Option


@dataclass(frozen=True)
class MenuItem:
    """A menu entry."""

    id: str
    label: str
    glyph: str | None = None
    disabled: bool = False

    @property
    def prompt(self) -> str:
        """Rendered label."""
        if self.glyph:
            return f"{self.glyph} {self.label}"
        return self.label


class Menu(OptionList):
    """Up/down menu posting semantic Highlighted/Selected messages."""

    items: reactive[tuple[MenuItem, ...]] = reactive(())

    class Highlighted(Message):
        """Cursor moved onto an item."""

        def __init__(self, menu: "Menu", item: str) -> None:
            """Init Message."""
            self.menu = menu
            self.item = item
            super().__init__()

        @property
        def control(self) -> "Menu":
            """Originating widget, for @on selectors."""
            return self.menu

    class Selected(Highlighted):
        """Item committed (enter/click)."""

    def __init__(
        self,
        *items: MenuItem,
        initial: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Init Menu."""
        super().__init__(id=id, classes=classes)
        self.set_reactive(Menu.items, tuple(items))
        self._initial = initial

    @classmethod
    def from_enum(cls, source: type[StrEnum], **kwargs: Any) -> Self:
        """Build a menu with one item per enum member."""
        return cls(
            *[MenuItem(id=member, label=member.value.title()) for member in source], **kwargs
        )

    @property
    def highlighted_id(self) -> str | None:
        """Id of the currently highlighted item, if any."""
        if self.highlighted is None:
            return None
        return self.get_option_at_index(self.highlighted).id

    def on_mount(self) -> None:
        """Build options and apply the initial highlight."""
        self._rebuild(prefer=self._initial)

    def watch_items(self) -> None:
        """Rebuild when the item set changes."""
        if self.is_mounted:
            self._rebuild()

    def _rebuild(self, prefer: str | None = None) -> None:
        """Recreate options, preserving the highlight when possible."""
        current = prefer if prefer is not None else self.highlighted_id
        self.clear_options()
        self.add_options(
            Option(item.prompt, id=item.id, disabled=item.disabled) for item in self.items
        )
        ids = [item.id for item in self.items if not item.disabled]
        target = current if current in ids else (ids[0] if ids else None)
        if target is not None:
            self.highlighted = self.get_option_index(target)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Translate the inner message."""
        event.stop()
        if event.option.id is not None:
            self.post_message(self.Highlighted(self, event.option.id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Translate the inner message."""
        event.stop()
        if event.option.id is not None:
            self.post_message(self.Selected(self, event.option.id))
