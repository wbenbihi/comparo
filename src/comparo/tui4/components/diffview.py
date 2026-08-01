"""DiffView — local textual diff viewer / single-source explorer.

Presentation only: never a Comparo verdict. Markup is never parsed.
"""

from collections.abc import Iterable
from typing import ClassVar
from typing import Literal
from typing import override

from rich.color import ColorType
from rich.segment import Segment
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App
from textual.binding import BindingType
from textual.geometry import Size
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip

from comparo.tui4.bindings import diff_view_bindings
from comparo.tui4.models.diff import DiffDocument
from comparo.tui4.models.diff import VisibleEntry
from comparo.tui4.models.diff import project_visible
from comparo.tui4.models.diff import region_at

SYNTAX_THEME = "ansi_dark"

_UNIFIED_MARKERS = {"add": "+", "remove": "-", "change": "~", "context": " "}


class DiffView(ScrollView, can_focus=True):
    """Unified/split diff, or structural explorer when the document is one-sided."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "diffview--add",
        "diffview--remove",
        "diffview--change",
        "diffview--emphasis-add",
        "diffview--emphasis-remove",
        "diffview--marker-add",
        "diffview--marker-remove",
        "diffview--marker-change",
        "diffview--lineno",
        "diffview--fold",
        "diffview--cursor",
    }

    BINDINGS: ClassVar[list[BindingType]] = diff_view_bindings()

    document: reactive[DiffDocument | None] = reactive(None, init=False)
    view_mode: reactive[Literal["unified", "split"]] = reactive("unified", init=False)
    cursor: reactive[int] = reactive(0, init=False)

    def __init__(
        self,
        document: DiffDocument | None = None,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the viewer with an optional immutable document."""
        super().__init__(id=id, classes=classes)
        self._folds: frozenset[int] = frozenset()
        self._entries: tuple[VisibleEntry, ...] = ()
        self._region_headers: dict[int, int] = {}
        self._old_lines: list[Text] = []
        self._new_lines: list[Text] = []
        self.set_reactive(DiffView.document, document)

    # ------------------------------------------------------------- document

    @override
    def on_mount(self) -> None:
        """First projection, with real geometry."""
        self._ingest()

    def watch_document(self) -> None:
        """New document after mount: re-ingest."""
        if self.is_mounted:
            self._ingest()

    def watch_view_mode(self) -> None:
        """Repaint on projection change."""
        self.refresh()

    def watch_cursor(self) -> None:
        """Keep the cursor visible."""
        if not self.is_mounted:
            return
        height = self.scrollable_content_region.height
        if self.cursor < self.scroll_offset.y:
            self.scroll_to(y=self.cursor, animate=False)
        elif height and self.cursor >= self.scroll_offset.y + height:
            self.scroll_to(y=self.cursor - height + 1, animate=False)
        self.refresh()

    def _ingest(self) -> None:
        """Re-highlight both sides, reset folds and cursor, reproject."""
        self._folds = frozenset()
        self.cursor = 0
        document = self.document
        if document is None:
            self._old_lines, self._new_lines = [], []
            self._region_headers = {}
        else:
            self._old_lines = self._highlight(document.old_text, document.language)
            self._new_lines = self._highlight(document.new_text, document.language)
            headers: dict[int, int] = {}
            for index, region in enumerate(document.regions):
                current = headers.get(region.start)
                if current is None or region.end > document.regions[current].end:
                    headers[region.start] = index
            self._region_headers = headers
        self._reproject()

    @staticmethod
    def _highlight(text: str, language: str | None) -> list[Text]:
        """Per-line styled text; tokenization only, never markup."""
        if not text:
            return []
        if language is None:
            return [Text(line) for line in text.splitlines()]
        syntax = Syntax("", language, theme=SYNTAX_THEME, background_color="default")
        return list(syntax.highlight(text).split("\n"))

    def _reproject(self) -> None:
        """Derive visible entries from (document, folds); size the canvas."""
        document = self.document
        if document is None:
            self._entries = ()
        else:
            self._entries = project_visible(len(document.rows), document.regions, self._folds)
        self.cursor = min(self.cursor, max(0, len(self._entries) - 1))
        self.virtual_size = Size(self.scrollable_content_region.width, len(self._entries))
        self.refresh()

    def on_resize(self) -> None:
        """Re-derive widths."""
        self._reproject()

    # -------------------------------------------------------------- actions

    def action_cursor_up(self) -> None:
        """Move the cursor line up."""
        self.cursor = max(0, self.cursor - 1)

    def action_cursor_down(self) -> None:
        """Move the cursor line down."""
        self.cursor = min(max(0, len(self._entries) - 1), self.cursor + 1)

    def action_toggle_fold(self) -> None:
        """Fold/unfold at the cursor."""
        document = self.document
        if document is None or not self._entries:
            return
        kind, index = self._entries[self.cursor]
        region = index if kind == "fold" else region_at(document.regions, index)
        if region is None:
            return
        folds = set(self._folds)
        folds.symmetric_difference_update({region})
        self._folds = frozenset(folds)
        self._reproject()

    def action_toggle_view(self) -> None:
        """Switch unified/split."""
        self.view_mode = "split" if self.view_mode == "unified" else "unified"

    @override
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide the split toggle for one-sided documents."""
        if action == "toggle_view":
            return True if self.document is not None and self.document.sided else None
        return True

    # ------------------------------------------------------------ rendering

    def _row_base(self, kind: str, cursor: bool) -> Style:
        """The single background authority for one line."""
        base = Style(bgcolor=self.rich_style.bgcolor)
        if kind in ("add", "remove", "change"):
            base = base + self.get_component_rich_style(f"diffview--{kind}")
        if cursor:
            base = base + self.get_component_rich_style("diffview--cursor")
        return base

    def _merge(self, segments: Iterable[Segment], base: Style) -> list[Segment]:
        """Lay the row base under every segment, evicting junk backgrounds.

        A segment background survives only when deliberately set (emphasis);
        unset, terminal-default, and baked widget backgrounds are replaced.
        """
        widget_bg = self.rich_style.bgcolor
        override = Style(bgcolor=base.bgcolor)
        merged: list[Segment] = []
        for segment in segments:
            style = segment.style
            if style is None:
                merged.append(Segment(segment.text, base))
                continue
            bg = style.bgcolor
            junk = bg is None or bg.type == ColorType.DEFAULT or bg == widget_bg
            merged.append(
                Segment(segment.text, (base + style + override) if junk else (base + style))
            )
        return merged

    @override
    def render_line(self, y: int) -> Strip:
        """Render one visible line."""
        index = y + self.scroll_offset.y
        width = self.scrollable_content_region.width
        document = self.document
        if document is None or index >= len(self._entries):
            return Strip.blank(width, self.rich_style)
        kind, payload = self._entries[index]
        if kind == "fold":
            region = document.regions[payload]
            row_index = region.start
            fold_char = "▸"
            hidden = region.end - region.start
        else:
            row_index = payload
            fold_char = "▾" if row_index in self._region_headers else " "
            hidden = 0
        row = document.rows[row_index]
        base = self._row_base(row.kind, index == self.cursor and self.has_focus)
        if document.sided and self.view_mode == "split":
            return self._render_split(document, row_index, width, base, fold_char, hidden)
        return self._render_unified(document, row_index, width, base, fold_char, hidden)

    def _gutter(self) -> int:
        """Line-number column width."""
        document = self.document
        if document is None:
            return 4
        last = document.first_lineno + len(document.rows)
        return max(4, len(str(last)) + 1)

    def _lineno(self, number: int | None) -> list[Segment]:
        """One gutter cell."""
        style = self.get_component_rich_style("diffview--lineno")
        text = f"{number:>{self._gutter() - 1}} " if number is not None else " " * self._gutter()
        return [Segment(text, style)]

    def _marker(self, symbol: str, kind: str) -> list[Segment]:
        """One marker cell: +, -, ~, or blank."""
        if symbol == " ":
            return [Segment("  ")]
        style = self.get_component_rich_style(f"diffview--marker-{kind}")
        return [Segment(f"{symbol} ", style)]

    def _fold_cell(self, fold_char: str) -> list[Segment]:
        """Fold indicator column: ▸ folded, ▾ foldable, blank otherwise."""
        if fold_char == " ":
            return [Segment("  ")]
        style = self.get_component_rich_style("diffview--fold")
        return [Segment(f"{fold_char} ", style)]

    def _badge(self, hidden: int) -> list[Segment]:
        """Collapsed-body badge appended after a folded header."""
        if not hidden:
            return []
        style = self.get_component_rich_style("diffview--fold")
        return [Segment(f" ··· {hidden} lines", style)]

    def _side_text(self, lines: list[Text], lineno: int | None, fallback: str) -> Text:
        """Highlighted line for one side, defensively."""
        offset = self.document.first_lineno if self.document else 1
        if lineno is not None and 0 <= lineno - offset < len(lines):
            return lines[lineno - offset].copy()
        return Text(fallback)

    def _emphasize(self, text: Text, spans: tuple[tuple[int, int], ...], style_name: str) -> Text:
        """Apply side-specific intra-line change emphasis."""
        style = self.get_component_rich_style(style_name)
        for start, end in spans:
            text.stylize(style, start, end)
        return text

    def _segments(self, text: Text) -> list[Segment]:
        """Render styled text to segments."""
        self.app: App[None]
        return [
            Segment(segment.text, segment.style or Style.null(), segment.control)
            for segment in text.render(self.app.console, end="")
        ]

    def _content_text(self, document: DiffDocument, row_index: int) -> Text:
        """The emphasized side of one row for single-column rendering."""
        row = document.rows[row_index]
        if row.kind == "remove":
            return self._emphasize(
                self._side_text(self._old_lines, row.old_lineno, row.old_text),
                row.emphasis_old,
                "diffview--emphasis-remove",
            )
        return self._emphasize(
            self._side_text(self._new_lines, row.new_lineno, row.new_text),
            row.emphasis_new,
            "diffview--emphasis-add",
        )

    def _render_unified(
        self,
        document: DiffDocument,
        row_index: int,
        width: int,
        base: Style,
        fold_char: str,
        hidden: int,
    ) -> Strip:
        """Single-column row (explorer, and unified diff)."""
        row = document.rows[row_index]
        segments: list[Segment] = []
        if document.sided:
            segments += self._lineno(row.old_lineno)
        segments += self._lineno(row.new_lineno if row.kind != "remove" else None)
        if document.sided:
            segments += self._marker(_UNIFIED_MARKERS[row.kind], row.kind)
        segments += self._fold_cell(fold_char)
        segments += self._segments(self._content_text(document, row_index))
        segments += self._badge(hidden)
        return Strip(self._merge(segments, base)).adjust_cell_length(width, base)

    def _render_split(
        self,
        document: DiffDocument,
        row_index: int,
        width: int,
        base: Style,
        fold_char: str,
        hidden: int,
    ) -> Strip:
        """Two half-width columns with a divider."""
        row = document.rows[row_index]
        half = max(12, (width - 1) // 2)
        left_marker = "-" if row.kind in ("remove", "change") else " "
        right_marker = "+" if row.kind in ("add", "change") else " "
        marker_kind = {"-": "remove", "+": "add"}

        def side(
            marker: str,
            lineno: int | None,
            lines: list[Text],
            raw: str,
            spans: tuple[tuple[int, int], ...],
            emphasis_name: str,
        ) -> list[Segment]:
            segments = self._lineno(lineno)
            segments += self._marker(marker, marker_kind.get(marker, "change"))
            segments += self._fold_cell(fold_char)
            text = self._emphasize(self._side_text(lines, lineno, raw), spans, emphasis_name)
            text.truncate(half - self._gutter() - 4)
            segments += self._segments(text)
            segments += self._badge(hidden)
            return list(Strip(self._merge(segments, base)).adjust_cell_length(half, base))

        left = side(
            left_marker,
            row.old_lineno,
            self._old_lines,
            row.old_text,
            row.emphasis_old,
            "diffview--emphasis-remove",
        )
        right = side(
            right_marker,
            row.new_lineno,
            self._new_lines,
            row.new_text,
            row.emphasis_new,
            "diffview--emphasis-add",
        )
        divider = self._merge(
            [Segment("│", self.get_component_rich_style("diffview--lineno"))], base
        )
        return Strip(left + divider + right).adjust_cell_length(width, base)
