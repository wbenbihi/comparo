"""FilterInput — the `/ filter…` row as a real input."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Input


class FilterInput(Input):
    """A slim filter input; emits Changed, esc clears and blurs."""

    class FilterChanged(Message):
        """The query changed."""

        def __init__(self, query: str, source: FilterInput) -> None:
            """Carry query and source."""
            super().__init__()
            self.query = query
            self.source = source

    def __init__(self, placeholder: str, *, id: str | None = None) -> None:
        """Build with the v0 placeholder styling."""
        super().__init__(placeholder=placeholder, id=id, classes="filter-input")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-emit as a typed message."""
        event.stop()
        self.post_message(self.FilterChanged(event.value, self))

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        """Esc clears and returns focus to the screen."""
        if event.key == "escape":
            event.stop()
            self.value = ""
            self.post_message(self.FilterChanged("", self))
            self.screen.focus_next()
