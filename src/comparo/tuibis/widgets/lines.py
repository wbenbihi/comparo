"""Line rendering — turns pre-modelled StyledLines into rows and panels.

Every remaining surface (launch stages, analyse, reports, settings) is a
composition of titled panels whose rows are fixture-driven ``StyledLine``s;
this module is the single renderer for them.
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import VerticalScroll

from comparo.tuibis.theme import style
from comparo.tuibis.viewmodels import PanelVM
from comparo.tuibis.viewmodels import StyledLine
from comparo.tuibis.widgets.panel import Panel
from comparo.tuibis.widgets.sections import TextRow


def render_line(line: StyledLine) -> Text:
    """Render one StyledLine to a Rich Text row."""
    out = Text()
    if line.cursor:
        out.append("▌", style=style("cursor"))
        out.append(" " * max(line.indent - 1, 0))
    else:
        out.append(" " * line.indent)
    for cell in line.cells:
        text = f"{cell.text:<{cell.width}}" if cell.width else cell.text
        out.append(text, style=style(cell.style))
    return out


class LineBlock(VerticalScroll):
    """A scrollable block of StyledLine rows."""

    def __init__(self, lines: tuple[StyledLine, ...]) -> None:
        """Build one TextRow per line."""
        rows = [
            TextRow(
                render_line(line),
                classes="row-cursor" if line.cursor else None,
            )
            for line in lines
        ]
        super().__init__(*rows, classes="line-block")

    def update_lines(self, lines: tuple[StyledLine, ...]) -> None:
        """Replace every row in place (same row count) or re-render fewer."""
        rows = list(self.query(TextRow))
        for index, line in enumerate(lines):
            if index < len(rows):
                rows[index].update(render_line(line))
        for index in range(len(lines), len(rows)):
            rows[index].update(render_line(StyledLine(())))


def line_panel(vm: PanelVM, *, id: str | None = None) -> Panel:
    """Build a titled Panel from a PanelVM."""
    return Panel(
        LineBlock(vm.lines),
        title=vm.title,
        glyph=vm.glyph,
        hint=vm.hint,
        tone=vm.tone,
        id=id,
    )
