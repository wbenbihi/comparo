"""Kind-aware inspectors — content-first views over view-models.

A request reads as an HTTP request: method-colored URL line, query and
headers with provenance, body, then compact strips. No facet tabs, no
metadata cards.
"""

from __future__ import annotations

from collections.abc import Mapping

from rich.text import Text
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget

from comparo.tuibis.theme import style
from comparo.tuibis.viewmodels import BodyLine
from comparo.tuibis.viewmodels import EntityKind
from comparo.tuibis.viewmodels import EnvironmentVM
from comparo.tuibis.viewmodels import ListItemVM
from comparo.tuibis.viewmodels import ProvenancedValue
from comparo.tuibis.viewmodels import RequestVM
from comparo.tuibis.viewmodels import StyledCell
from comparo.tuibis.viewmodels import StyledLine
from comparo.tuibis.widgets import fragments
from comparo.tuibis.widgets.cursor_list import CursorList
from comparo.tuibis.widgets.lines import LineBlock
from comparo.tuibis.widgets.sections import Rule
from comparo.tuibis.widgets.sections import SplitRow
from comparo.tuibis.widgets.sections import TextRow


def _kv_block(
    title: Text,
    rows: tuple[ProvenancedValue, ...],
    empty: str,
    key_width: int,
    value_width: int,
    narrow: bool = False,
) -> Vertical:
    children = [TextRow(title)]
    if rows:
        children.extend(
            TextRow(Text(" "), fragments.kv_row(row, key_width, value_width)) for row in rows
        )
    else:
        children.append(TextRow(fragments.dim(f" {empty}")))
    classes = "kv-block -narrow" if narrow else "kv-block"
    return Vertical(*children, classes=classes)


class RequestInspector(VerticalScroll):
    """E1 — the request IS the screen."""

    DEFAULT_CSS = """
    RequestInspector #body-walk { height: 8; }
    """

    def __init__(
        self,
        vm: RequestVM,
        *,
        body_explainers: Mapping[str, tuple[StyledLine, ...]] | None = None,
    ) -> None:
        """Initialize the component from its view-model inputs."""
        self._explainers: dict[str, tuple[StyledLine, ...]] = dict(body_explainers or {})
        self._explainer_pad = max((len(v) for v in self._explainers.values()), default=0)
        self._explanation: tuple[StyledLine, ...] = ()
        self._body_list: CursorList | None = None
        self._explainer_block: LineBlock | None = None
        url_left = Text(" ")
        url_left.append_text(fragments.method(vm.method, pad=5))
        url_left.append(vm.base_url, style=style("baseline"))
        url_left.append(vm.path, style=style("bright"))
        url_right = Text()
        url_right.append("resolved", style=style("fg"))
        url_right.append(" · env ", style=style("muted"))
        url_right.append(f"{vm.resolved_env} ▾", style=style("baseline"))
        url_right.append(" · cell ", style=style("muted"))
        url_right.append(f"{vm.resolved_cell} ▾ ", style=style("fg"))

        desc_right = Text()
        desc_right.append("v", style=style("fg"))
        desc_right.append(" authored ⇄ resolved ", style=style("muted"))

        strips = [
            TextRow(
                Text(" "),
                fragments.pill(vm.matrix_pill) if vm.matrix_pill else Text(),
                fragments.muted("   "),
                fragments.plain(vm.variants_line),
            ),
            TextRow(
                Text(" "),
                fragments.label("EXPECTED"),
                fragments.muted("    "),
                fragments.plain(vm.expected_line),
            ),
            TextRow(
                Text(" "),
                fragments.label("CHECKS"),
                fragments.muted("      "),
                fragments.plain(vm.checks_line),
            ),
            TextRow(
                Text(" "),
                fragments.label("HISTORY"),
                fragments.muted("     "),
                fragments.plain(vm.history_line),
            ),
        ]

        relations = Text(" ")
        relations.append("uses ", style=style("muted"))
        for index, rel in enumerate(vm.uses):
            if index:
                relations.append("  ·  ", style=style("dim"))
            relations.append_text(fragments.entity(rel.kind, rel.name))
        relations.append("      used by ", style=style("muted"))
        for index, rel in enumerate(vm.used_by):
            if index:
                relations.append("  ·  ", style=style("dim"))
            relations.append_text(fragments.entity(rel.kind, rel.name))
        relations_hint = Text()
        relations_hint.append("g", style=style("fg"))
        relations_hint.append(" full graph ", style=style("muted"))

        body_rows: list[Widget] = []
        if vm.body_lines and self._explainers:
            body_rows.append(
                TextRow(
                    fragments.label(" BODY"),
                    fragments.muted("  ↑↓ walk · the focused path is explained below"),
                )
            )
            self._body_list = CursorList(
                tuple(self._body_item(line) for line in vm.body_lines), id="body-walk"
            )
            start_key = next(
                (
                    f"body:{line.number}"
                    for line in vm.body_lines
                    if f"body:{line.number}" in self._explainers
                ),
                "",
            )
            self._explanation = self._explainers.get(start_key, ())
            self._explainer_block = LineBlock(self._padded(self._explanation))
            body_rows.append(self._body_list)
            body_rows.append(self._explainer_block)
        elif vm.body_lines:
            body_rows.append(TextRow(fragments.label(" BODY")))
            for line in vm.body_lines:
                row = Text("   ")
                row.append(f"{line.number:>2} ", style=style("dim"))
                row.append(
                    line.text,
                    style=style("warn") if line.redacted else style("fg"),
                )
                if line.provenance:
                    row.append("   ")
                    row.append_text(fragments.provenance(line.provenance))
                body_rows.append(TextRow(row))
        else:
            body_rows.append(TextRow(fragments.label(" BODY"), fragments.muted("  none — GET")))

        super().__init__(
            SplitRow(url_left, url_right),
            SplitRow(Text(f" {vm.description}", style=style("muted")), desc_right),
            Rule(),
            Horizontal(
                _kv_block(
                    fragments.label(" QUERY", str(len(vm.query))),
                    vm.query,
                    "none",
                    10,
                    8,
                    narrow=True,
                ),
                _kv_block(fragments.label(" HEADERS", vm.headers_note), vm.headers, "none", 15, 18),
                classes="kv-columns",
            ),
            *body_rows,
            Rule(),
            *strips,
            Rule(),
            SplitRow(relations, relations_hint),
        )

    def _body_item(self, line: BodyLine) -> ListItemVM:
        """Model one walkable body row for the cursor list."""
        key = f"body:{line.number}"
        cells: list[StyledCell] = [
            StyledCell(f"{line.number:>2} ", "dim"),
            StyledCell(line.text, "warn" if line.redacted else "fg"),
        ]
        if line.provenance:
            cells.append(StyledCell("   ← ", "dim"))
            cells.append(StyledCell(line.provenance, "dim"))
        return ListItemVM(
            key,
            StyledLine(tuple(cells)),
            header=key not in self._explainers,
            search_text=line.text,
        )

    def _padded(self, lines: tuple[StyledLine, ...]) -> tuple[StyledLine, ...]:
        """Pad an explanation to the tallest one so rows can update in place."""
        return lines + tuple(StyledLine(()) for _ in range(self._explainer_pad - len(lines)))

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """Follow the body cursor with the focused-path explainer."""
        if self._body_list is None or event.source is not self._body_list:
            return
        event.stop()
        self._explanation = self._explainers.get(event.key, ())
        if self._explainer_block is not None:
            self._explainer_block.update_lines(self._padded(self._explanation))

    @property
    def body_cursor_key(self) -> str:
        """Key of the body row the walk cursor is on, or empty."""
        return self._body_list.cursor_key if self._body_list is not None else ""

    @property
    def explanation_text(self) -> str:
        """Plain text of the current focused-path explanation."""
        return " ".join(cell.text for line in self._explanation for cell in line.cells)


class MatrixInspector(VerticalScroll):
    """E2 — matrix axes plus cases with live narrowing and a selected preview."""

    DEFAULT_CSS = """
    MatrixInspector #matrix-cases { height: 7; }
    """

    def __init__(
        self,
        *,
        name: str,
        summary: str,
        axes: tuple[StyledLine, ...],
        cases: tuple[ListItemVM, ...],
        details: Mapping[str, StyledLine],
    ) -> None:
        """Compose axes, the selectable case list, and the live SELECTED row."""
        self._details = dict(details)
        self._cases = CursorList(cases, id="matrix-cases")
        self._total = sum(1 for item in cases if item.selectable)
        first_key = next((item.key for item in cases if not item.header and not item.dimmed), "")
        initial = self._details.get(first_key, StyledLine(()))
        self._selected_block = LineBlock((initial,))
        self._count_row = TextRow(self._count_parts(self._total))
        super().__init__(
            TextRow(
                Text(" "),
                fragments.entity(EntityKind.MATRIX, name, bold=True),
                fragments.muted(f"   {summary}"),
            ),
            Rule(),
            LineBlock(axes),
            Rule(),
            TextRow(
                Text(" "),
                fragments.label("CASES"),
                fragments.muted("   space narrows a case out · ↑↓ preview injected values"),
            ),
            self._cases,
            self._selected_block,
            self._count_row,
        )

    def _count_parts(self, admitted: int) -> Text:
        """Render the admitted-count row content."""
        out = Text(" ")
        out.append_text(fragments.label("ADMITTED"))
        out.append_text(fragments.muted(f"   {admitted} of {self._total} cells enter the next run"))
        return out

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """Preview the highlighted case's injected values."""
        if event.source is not self._cases:
            return
        event.stop()
        line = self._details.get(event.key)
        if line is not None:
            self._selected_block.update_lines((line,))

    def on_cursor_list_toggled(self, event: CursorList.Toggled) -> None:
        """Refresh the admitted-cells tally after narrowing."""
        if event.source is not self._cases:
            return
        event.stop()
        self._count_row.update_parts(self._count_parts(len(self._cases.selected_keys)))


class GraphInspector(VerticalScroll):
    """E4 — a request's dependency graph as a navigable node list."""

    class NodeActivated(Message):
        """A graph node naming another resource was activated."""

        def __init__(self, key: str) -> None:
            """Carry the activated resource key."""
            super().__init__()
            self.key = key

    def __init__(self, subject: str, items: tuple[ListItemVM, ...]) -> None:
        """Compose the graph header and the dependency node list."""
        self._list = CursorList(items)
        super().__init__(
            TextRow(
                Text(" "),
                fragments.label("DEPENDENCY GRAPH"),
                fragments.muted(f"   {subject} · enter jumps to a resource · esc back"),
            ),
            self._list,
        )

    def focus_list(self) -> None:
        """Give the node list keyboard focus."""
        self._list.focus()

    def on_cursor_list_activated(self, event: CursorList.Activated) -> None:
        """Re-emit node activation as a typed graph message."""
        if event.source is not self._list:
            return
        event.stop()
        self.post_message(self.NodeActivated(event.key))


class EnvironmentInspector(VerticalScroll):
    """E3 — pills first, values front and center."""

    def __init__(self, vm: EnvironmentVM) -> None:
        """Initialize the component from its view-model inputs."""
        head = Text(" ")
        head.append(vm.url, style=style("baseline"))
        head.append(f"   {vm.connection_line}", style=style("fg"))

        table_rows: list[TextRow] = [
            TextRow(
                Text(" "),
                fragments.label("VALUES"),
                fragments.muted(f"  {vm.values_note}"),
            ),
            TextRow(
                Text("  "),
                Text(f"{'NAME':<15}{'CLASS':<15}{'SOURCE':<28}USED BY", style=style("label")),
            ),
        ]
        for name, cls, source, used_by in vm.values:
            row = Text("  ")
            row.append(f"{name:<15}", style=style("fg"))
            row.append(
                f"{cls:<15}",
                style=style("warn") if "REDACT" in cls or "ovr" in cls else style("muted"),
            )
            row.append(f"{source:<28}", style=style("fg"))
            row.append(used_by, style=style("muted"))
            table_rows.append(TextRow(row))

        super().__init__(
            TextRow(head),
            TextRow(Text(f" {vm.safety_line}", style=style("muted"))),
            Rule(),
            *table_rows,
            Rule(),
            TextRow(
                Text(" "),
                fragments.label("PRECEDENCE"),
                fragments.muted(f"  {vm.precedence_line}"),
            ),
            TextRow(
                Text(" "),
                fragments.label("USAGE"),
                fragments.muted(f"       {vm.usage_line}"),
            ),
        )
