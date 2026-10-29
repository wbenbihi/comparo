"""Compact labeled choice field, composed from the TUI4 action and menu primitives."""

from typing import ClassVar
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import ModalScreen

from comparo.tui4.components.actionbar import Action
from comparo.tui4.components.actionbar import ActionBar
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.menu import Menu
from comparo.tui4.components.menu import MenuItem
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.typography import Caption


class ChoicePicker(ModalScreen[str | None]):
    """Searchable keyboard/pointer picker with an explicit cancel route."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("/", "focus_filter", "Search"),
    ]
    DEFAULT_CSS = """
    ChoicePicker { align: center middle; background: $background 80%; }
    ChoicePicker > Panel { width: 64; max-width: 95%; height: 24; max-height: 90%; }
    ChoicePicker Menu { height: 1fr; }
    """

    def __init__(self, title: str, items: tuple[MenuItem, ...], selected: str) -> None:
        """Keep the choice set immutable while its visible search window changes."""
        super().__init__()
        self.title_text = title
        self.items = items
        self.selected = selected

    @override
    def compose(self) -> ComposeResult:
        yield Panel(
            FilterInput("Find a choice…"),
            Menu(*self.items, initial=self.selected),
            ActionBar((Action("cancel", "Cancel", key="Esc"),)),
            title=self.title_text,
        )

    def on_mount(self) -> None:
        """Start on the current choice, ready for arrows and Enter."""
        self.query_one(Menu).focus()

    @on(Menu.Selected)
    def _select(self, event: Menu.Selected) -> None:
        event.stop()
        self.dismiss(event.item)

    @on(FilterInput.Changed)
    def _filter(self, event: FilterInput.Changed) -> None:
        event.stop()
        self.query_one(Menu).items = tuple(
            item for item in self.items if event.value.casefold() in item.label.casefold()
        )

    @on(FilterInput.Submitted)
    @on(FilterInput.Dismissed)
    def _menu_focus(self, event: FilterInput.Dismissed | FilterInput.Submitted) -> None:
        event.stop()
        self.query_one(Menu).focus()

    @on(ActionBar.Requested)
    def _cancel_button(self, event: ActionBar.Requested) -> None:
        event.stop()
        self.action_cancel()

    def action_cancel(self) -> None:
        """Close without changing the selected value."""
        self.dismiss(None)

    def action_focus_filter(self) -> None:
        """Enter picker search with the same shortcut as the archive."""
        self.query_one(FilterInput).focus()


class SelectField(Horizontal):
    """A one-line field that opens the shared searchable choice picker."""

    DEFAULT_CSS = """
    SelectField { height: 1; }
    SelectField > Caption { width: auto; margin-right: 1; color: $comparo-muted; }
    SelectField ActionBar { width: 1fr; }
    SelectField ActionBar Button { width: 100%; text-align: left; margin: 0; }
    """

    class Changed(Message):
        """An explicitly committed field value."""

        def __init__(self, field: "SelectField") -> None:
            """Capture the committed value and semantic field identity."""
            super().__init__()
            self.field = field
            self.value = field.value

        @property
        @override
        def control(self) -> "SelectField":
            return self.field

    def __init__(
        self, title: str, items: tuple[MenuItem, ...], *, value: str = "all", id: str | None = None
    ) -> None:
        """Initialize one committed value and its immutable choices."""
        super().__init__(id=id)
        self.title_text = title
        self.items = items
        self.value = value

    @override
    def compose(self) -> ComposeResult:
        yield Caption(self.title_text, markup=False)
        yield ActionBar(self._actions())

    def _actions(self) -> tuple[Action, ...]:
        label = next((item.label for item in self.items if item.id == self.value), self.value)
        return (Action("pick", f"{label} ▾", primary=self.value != "all"),)

    def set_items(self, items: tuple[MenuItem, ...]) -> None:
        """Refresh available choices while retaining an active filter."""
        self.items = items
        self.query_one(ActionBar).actions = self._actions()

    def set_value(self, value: str) -> None:
        """Synchronize a value without emitting a user interaction."""
        self.value = value
        self.query_one(ActionBar).actions = self._actions()

    @on(ActionBar.Requested)
    def _pick(self, event: ActionBar.Requested) -> None:
        event.stop()
        self.app.push_screen(ChoicePicker(self.title_text, self.items, self.value), self._chosen)

    def _chosen(self, value: str | None) -> None:
        if value is not None and value != self.value:
            self.set_value(value)
            self.post_message(self.Changed(self))
        self.call_after_refresh(self.focus)

    @override
    def focus(self, scroll_visible: bool = True) -> "SelectField":
        """Keep native action buttons behind the shared field boundary."""
        self.query_one(ActionBar).focus_action()
        return self
