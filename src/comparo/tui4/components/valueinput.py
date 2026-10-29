"""Compact labeled text value editor with a semantic change boundary."""

from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input


class ValueInput(Horizontal):
    """A reusable value field styled like FilterInput, without filter-specific semantics."""

    DEFAULT_CSS = """
    ValueInput {
        height: 3; border: round $comparo-border; border-title-color: $comparo-muted;
    }
    ValueInput:focus-within { border: round $primary; border-title-color: $foreground; }
    ValueInput Input { height: 1; border: none; background: transparent; padding: 0 1; }
    """

    class Changed(Message):
        """A value was edited inside the component."""

        def __init__(self, field: "ValueInput", value: str) -> None:
            """Retain the field identity and current input."""
            super().__init__()
            self.field = field
            self.value = value

        @property
        @override
        def control(self) -> "ValueInput":
            """Return the semantic event owner."""
            return self.field

    def __init__(
        self, title: str, *, placeholder: str = "", maximum_length: int = 0, id: str | None = None
    ) -> None:
        """Initialize a labeled compact field."""
        super().__init__(id=id)
        self.border_title = title
        self._placeholder = placeholder
        self._maximum_length = maximum_length

    @override
    def compose(self) -> ComposeResult:
        """Keep the underlying text editor private to the component."""
        yield Input(placeholder=self._placeholder, max_length=self._maximum_length)

    @property
    def value(self) -> str:
        """Return the current editor value."""
        return self.query_one(Input).value

    @value.setter
    def value(self, value: str) -> None:
        self.query_one(Input).value = value

    @override
    def focus(self, scroll_visible: bool = True) -> "ValueInput":
        """Forward focus without exposing the inner editor."""
        self.query_one(Input).focus(scroll_visible)
        return self

    def on_input_changed(self, event: Input.Changed) -> None:
        """Translate native input changes into the public component message."""
        event.stop()
        self.post_message(self.Changed(self, event.value))
