"""Seg pills and chip rows — the clickable option strips of the v0 design."""

from __future__ import annotations

from typing import override

from rich.text import Text
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static

from comparo.tui3.theme import style


class _Opt(Static):
    """One clickable option."""

    def __init__(self, value: str, label: Text) -> None:
        """Store the option value."""
        super().__init__(label, classes="seg-opt")
        self.value = value

    def on_click(self) -> None:
        """Report the press."""
        self.post_message(Seg.Pressed(self.value))


class Seg(Horizontal):
    """Single-active segmented pill (``raw · resolved · headers …``)."""

    class Pressed(Message):
        """Internal option press."""

        def __init__(self, value: str) -> None:
            """Carry the value."""
            super().__init__()
            self.value = value

    class Changed(Message):
        """The active option changed."""

        def __init__(self, value: str, source: Seg) -> None:
            """Carry value and source."""
            super().__init__()
            self.value = value
            self.source = source

    def __init__(self, options: tuple[str, ...], active: str, *, id: str | None = None) -> None:
        """Build the strip."""
        super().__init__(id=id, classes="seg")
        self._options = options
        self._active = active

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield the options."""
        for opt in self._options:
            yield _Opt(opt, self._label(opt))

    def _label(self, opt: str) -> Text:
        on = opt == self._active
        return Text(f" {opt} ", style=style("seg.on") if on else style("dim"))

    def set_active(self, value: str) -> None:
        """Re-highlight."""
        self._active = value
        for widget in self.query(_Opt):
            widget.update(self._label(widget.value))

    def cycle(self, step: int = 1) -> str:
        """Advance the active option and return it."""
        index = self._options.index(self._active)
        value = self._options[(index + step) % len(self._options)]
        self.set_active(value)
        return value

    @property
    def active(self) -> str:
        """The active option."""
        return self._active

    def on_seg_pressed(self, event: Seg.Pressed) -> None:
        """Promote a press to a change."""
        event.stop()
        self.set_active(event.value)
        self.post_message(self.Changed(event.value, self))


class ChipRow(Horizontal):
    """A row of independently clickable chips (tags, overrides)."""

    class Pressed(Message):
        """A chip was pressed."""

        def __init__(self, key: str, source: ChipRow) -> None:
            """Carry key and source."""
            super().__init__()
            self.key = key
            self.source = source

    def __init__(self, chips: tuple[tuple[str, Text], ...], *, id: str | None = None) -> None:
        """Chips = ((key, label), …)."""
        super().__init__(id=id, classes="chip-row")
        self._chips = chips

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield the chips."""
        for key, label in self._chips:
            chip = _Chip(key, label)
            yield chip

    def set_chips(self, chips: tuple[tuple[str, Text], ...]) -> None:
        """Replace every chip label (state changed)."""
        self._chips = chips
        widgets = list(self.query(_Chip))
        for widget, (key, label) in zip(widgets, chips, strict=False):
            widget.key = key
            widget.update(label)


class _Chip(Static):
    """One clickable chip."""

    def __init__(self, key: str, label: Text) -> None:
        """Store the chip key."""
        super().__init__(label, classes="chip")
        self.key = key

    def on_click(self) -> None:
        """Report the press to the row's ancestors."""
        parent = self.parent
        if isinstance(parent, ChipRow):
            self.post_message(ChipRow.Pressed(self.key, parent))
