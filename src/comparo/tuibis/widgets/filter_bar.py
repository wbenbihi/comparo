"""FilterBar — the one filter pattern, now a real input with chips."""

from __future__ import annotations

from rich.text import Text
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input

from comparo.tuibis.theme import style
from comparo.tuibis.widgets.sections import TextRow


class FilterBar(Vertical):
    """`/`-focusable query input plus kind chips with a live count."""

    class Changed(Message):
        """The filter query changed."""

        def __init__(self, query: str) -> None:
            """Carry the new query."""
            super().__init__()
            self.query = query

    def __init__(self, placeholder: str, chips: tuple[str, ...], active: str) -> None:
        """Build the input row and the chip row."""
        self._input = Input(placeholder=placeholder)
        self._chips = chips
        self._active = active
        self._chip_row = TextRow(self._render_chips())
        super().__init__(self._input, self._chip_row, classes="filter-bar")

    def _render_chips(self) -> Text:
        row = Text(" ")
        for chip in self._chips:
            if chip == self._active:
                row.append(chip, style=style("chip.active"))
            else:
                row.append(chip, style=style("muted"))
            row.append("  ")
        return row

    def focus_query(self) -> None:
        """Focus the query input (bound to `/`)."""
        self._input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-emit as a typed FilterBar message."""
        event.stop()
        self.post_message(self.Changed(event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Return focus to the sibling list on enter."""
        event.stop()
        self.screen.focus_next()
