"""Legend — one-line glyph legend for any vocabulary."""

from typing import override

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from comparo.tui4.theme import style

type LegendItem = tuple[str, str, str]  # glyph character, style token, label

_GLUE = "\u00a0"  # non-breaking space: keeps glyph and label on one line


class Legend(Widget):
    """Renders (glyph, token, label) items; knows nothing about their meaning."""

    items: reactive[tuple[LegendItem, ...]] = reactive(())

    @override
    def render(self) -> Text:
        """Glyph + label per item; wraps only between items."""
        legend = Text()
        for character, token, label in self.items:
            if legend:
                legend.append("   ")
            legend.append(f"{character}{_GLUE}", style(token))
            legend.append(label.replace(" ", _GLUE), style("muted"))
        return legend
