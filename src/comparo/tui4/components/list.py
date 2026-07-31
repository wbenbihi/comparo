"""KeyValueList — aligned, styled key/value rows."""

from dataclasses import dataclass
from typing import override

from textual.app import ComposeResult
from textual.content import Content
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


@dataclass(frozen=True)
class Field:
    """One key/value row."""

    key: str
    value: str | Content
    tone: str | None = None


class KeyValueList(Widget):
    """Grid of aligned key/value rows, rebuilt from data."""

    entries: reactive[tuple[Field, ...]] = reactive((), recompose=True)

    def __init__(
        self,
        *entries: Field,
        id: str | None = None,
        separators: bool = False,
        classes: str | None = None,
    ) -> None:
        """Initialize the list from immutable fields."""
        super().__init__(id=id, classes=classes)
        self.set_reactive(KeyValueList.entries, tuple(entries))
        self._separators = separators

    @override
    def compose(self) -> ComposeResult:
        """One key cell and one value cell per field."""
        for idx, field in enumerate(self.entries):
            if idx and idx < len(self.entries) and self._separators:
                yield Static(classes="kv-sep")
            yield Static(field.key, classes="kv-key")
            value = Static(field.value, classes="kv-value")
            if field.tone:
                value.add_class(f"tone-{field.tone}")
            yield value
