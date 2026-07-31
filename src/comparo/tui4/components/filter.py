"""FilterInput — bordered filter box with key hint and semantic messages."""

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input
from textual.widgets import Static


class FilterInput(Horizontal):
    """Filter box: live Changed, Submitted on enter, two-stage escape."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "release", "Clear / close", show=False),
    ]

    class Changed(Message):
        """Filter text edited."""

        def __init__(self, filter_input: "FilterInput", value: str) -> None:
            """Init Message."""
            self.filter_input = filter_input
            self.value = value
            super().__init__()

        @property
        def control(self) -> "FilterInput":
            """Originating widget, for @on selectors."""
            return self.filter_input

    class Submitted(Changed):
        """Filter committed (enter)."""

    class Dismissed(Message):
        """Filter closed (escape on empty)."""

        def __init__(self, filter_input: "FilterInput") -> None:
            """Init Message."""
            self.filter_input = filter_input
            super().__init__()

        @property
        def control(self) -> "FilterInput":
            """Originating widget, for @on selectors."""
            return self.filter_input

    def __init__(
        self,
        placeholder: str = "filter…",
        *,
        key_hint: str = "/",
        title: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Init the filter box."""
        super().__init__(id=id, classes=classes)
        self._placeholder = placeholder
        self._key_hint = key_hint
        if title:
            self.border_title = title

    def compose(self) -> ComposeResult:
        """Key hint chip, then the borderless field."""
        yield Static(self._key_hint, classes="filter-key")
        yield Input(placeholder=self._placeholder)

    @property
    def value(self) -> str:
        """Current filter text."""
        return self.query_one(Input).value

    def focus(self, scroll_visible: bool = True) -> "FilterInput":
        """Forward focus to the inner field."""
        self.query_one(Input).focus(scroll_visible)
        return self

    def action_release(self) -> None:
        """First escape clears; second escape dismisses."""
        field = self.query_one(Input)
        if field.value:
            field.value = ""
        else:
            self.post_message(self.Dismissed(self))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Translate the inner message."""
        event.stop()
        self.post_message(self.Changed(self, event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Translate the inner message."""
        event.stop()
        self.post_message(self.Submitted(self, event.value))
