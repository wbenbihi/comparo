"""The shared UI components — one implementation per concept, ever.

The hard rule this module enforces: a view may never define a private renderer
for a concept that lives here. The summary strip, verdict pill, progress bar,
segmented pill, filter row, verdict box, rule-record chrome, error panel, and
navigation stack each exist exactly once, consume plain view-models (buildable
from live engine objects AND from a saved ``ReportRecord``), and are composed by
the Run/Diff views live, and by the Execution/Report surfaces as assembly.

Focus-model convention (shared by every results surface): ``tab`` moves focus
between the index pane and the inspect pane, ``↑↓`` drives whichever pane has
focus, ``enter`` drills the focused row, and a cross-view jump pushes onto the
:class:`NavStack` so ``esc`` returns to where the user came from.

Glyph grammar (locked by the Run/Diff Results specs): ``✓`` clean/held,
``✗`` a rule broke, ``!`` error (never a broken rule), ``~`` advisory
(gate-neutral warn break), ``⊘`` not run, ``◐`` running, ``·`` pending.
"""

import dataclasses
from collections.abc import Callable
from typing import Literal

from rich import box as rich_box
from rich.console import Group
from rich.console import RenderableType
from rich.table import Table as RichTable
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable
from textual.widgets import Static

from comparo.tui.tokens import _ACCENT
from comparo.tui.tokens import _DANGER
from comparo.tui.tokens import _DIM
from comparo.tui.tokens import _DRIFT
from comparo.tui.tokens import _INK
from comparo.tui.tokens import _LABEL
from comparo.tui.tokens import _SAME
from comparo.tui.tokens import _TEXT
from comparo.tui.tokens import _TEXT_HI
from comparo.tui.tokens import _WARN

#: A cell's display state — verdicts plus the TUI-transient lifecycle states.
CellState = Literal["pass", "fail", "error", "not_run", "advisory", "running", "pending"]

#: The one glyph map. ``advisory`` is a PASSED cell wearing its warning.
_CELL_GLYPHS: dict[str, tuple[str, str]] = {
    "pass": ("✓", _SAME),
    "fail": ("✗", _DRIFT),
    "error": ("!", _WARN),
    "not_run": ("⊘", _DIM),
    "advisory": ("~", _WARN),
    "running": ("◐", _WARN),
    "pending": ("·", _DIM),
}

_GATE_GLYPHS: dict[str, tuple[str, str]] = {
    "PASS": ("✓", _SAME),
    "FAIL": ("✗", _DANGER),
    "ERROR": ("!", _WARN),
}


def cell_glyph(state: str) -> tuple[str, str]:
    """The ``(glyph, color)`` for a cell state — every surface uses this map."""
    return _CELL_GLYPHS.get(state, ("·", _DIM))


def cell_mark(state: str) -> Text:
    """The cell state as a one-character styled Text."""
    glyph, color = cell_glyph(state)
    return Text(glyph, style=color)


def verdict_pill(gate: str, *, advisory: int = 0) -> Text:
    """The headline verdict — ``✗ FAIL`` — with an optional advisory suffix."""
    glyph, color = _GATE_GLYPHS.get(gate, ("?", _DIM))
    pill = Text(f"{glyph} {gate}", style=f"bold {color}")
    if advisory:
        pill.append(f"  ~ {advisory}", style=_WARN)
    return pill


def status_code_text(code: int | None) -> Text:
    """An HTTP status styled by the one rule: 2xx green, absent dim, else amber."""
    if code is None:
        return Text("—", style=_DIM)
    return Text(str(code), style=_SAME if 200 <= code < 300 else _WARN)


def progress_bar(done: int, total: int, *, width: int = 24) -> Text:
    """The one progress-bar primitive — ``━`` filled, ``╌`` remaining."""
    filled = int(width * done / total) if total else 0
    bar = Text()
    bar.append("━" * filled, style=_SAME)
    bar.append("╌" * (width - filled), style=_DIM)
    return bar


#: The pill/segment background and the table hairline — the mockup's --line.
_PILL_BG = "#1b2230"
_LINE = "#2a3345"


def seg_pill(options: tuple[str, ...], active: str) -> Text:
    """The segmented pill — quiet and readable, no background patches.

    Dim segments joined by hairline separators; the active one in bold accent.
    """
    text = Text()
    for index, option in enumerate(options):
        on = option == active
        if index:
            text.append(" │ ", style=_LINE)
        text.append(option, style=f"bold {_ACCENT}" if on else _DIM)
    return text


def filter_row(
    query: str, *, toggle_key: str, toggle_label: str, toggle_on: bool
) -> RenderableType:
    """The one ``/ filter`` strip — the mockup's bordered input row.

    Left: ``/`` and the ACTIVE query (the placeholder only when empty). Right:
    the fails/broken-only toggle as a chip. The host Static wears the
    ``filterrow`` CSS class, which draws the input's rounded border.
    """
    table = RichTable(box=None, show_header=False, expand=True, padding=0, pad_edge=False)
    table.add_column(ratio=1)
    table.add_column(justify="right")
    left = Text(no_wrap=True)
    left.append("/ ", style=f"bold {_ACCENT}")
    left.append(query if query else "filter…", style=_TEXT_HI if query else _DIM)
    right = Text(no_wrap=True)
    right.append(toggle_key, style=f"bold {_ACCENT}")
    right.append(f" {toggle_label} ", style=_DIM)
    state = " on " if toggle_on else " off "
    right.append(state, style=f"bold {_INK} on {_ACCENT}" if toggle_on else f"{_DIM} on {_PILL_BG}")
    table.add_row(left, right)
    return table


# ── summary strip — one slot, two costumes ────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class TallySegment:
    """One ``N label`` piece of the strip's tally cluster."""

    count: int
    label: str
    style: str


def tally_segments(segments: list[TallySegment]) -> Text:
    """Render ``5 ✓ · 1 ~ · 1 ✗`` — zero-count segments stay, dimmed."""
    text = Text()
    for index, segment in enumerate(segments):
        if index:
            text.append(" · ", style=_DIM)
        style = segment.style if segment.count else _DIM
        text.append(f"{segment.count} {segment.label}", style=style)
    return text


def summary_strip_running(
    title: Text | str,
    done: int,
    total: int,
    segments: list[TallySegment],
    right: Text | str = "",
) -> Text:
    """The mid-flight costume: title · bar · done/total · tallies · right cluster."""
    strip = Text()
    strip.append_text(Text.from_markup(title) if isinstance(title, str) else title)
    strip.append("  ")
    strip.append_text(progress_bar(done, total))
    strip.append(f"  {done}/{total}  ", style=f"bold {_TEXT_HI}")
    strip.append_text(tally_segments(segments))
    if right:
        strip.append("   ")
        strip.append_text(Text.from_markup(right) if isinstance(right, str) else right)
    return strip


def summary_strip_finished(
    gate: str,
    segments: list[TallySegment],
    right: Text | str = "",
    *,
    advisory: int = 0,
) -> Text:
    """The finished costume: the same slot swaps the bar for the verdict."""
    strip = Text()
    strip.append_text(verdict_pill(gate, advisory=advisory))
    strip.append("  ·  ", style=_DIM)
    strip.append_text(tally_segments(segments))
    if right:
        strip.append("   ")
        strip.append_text(Text.from_markup(right) if isinstance(right, str) else right)
    return strip


# ── the summary bar — the ONE bar RUN and DIFF share ──────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class SummarySegment:
    """One split-detail piece of the summary bar — ``✓ 4 same``.

    The icon leads, then the count, then the word, so every tab reads the same
    way. A zero-count segment stays (dimmed) so the shape never jumps.
    """

    icon: str
    count: int
    word: str
    style: str


def summary_bar(
    segments: list[SummarySegment],
    env: "Text | str" = "",
    *,
    ident: "Text | str" = "",
    gate: str = "",
    advisory: int = 0,
    detail: str = "",
    save_key: str = "",
) -> RichTable:
    """The one summary bar — RUN and DIFF build it, the host frames it.

    Left cluster, in order: the split details (icon · count · word), a
    separator, an id, the gate verdict pill, an extra detail, and the save
    hint. Right, aligned: the environment. The host widget wears ``.panel``
    with ``border_title = "SUMMARY"`` and a gate-tint class, so the background,
    the SUMMARY label, and the tinted border are identical on both tabs.
    """
    left = Text(no_wrap=True)
    for index, seg in enumerate(segments):
        if index:
            left.append(" · ", style=_DIM)
        style = seg.style if seg.count else _DIM
        left.append(f"{seg.icon} {seg.count} {seg.word}", style=style)
    if ident or gate or detail or save_key:
        left.append("   │   ", style=_DIM)
    if ident:
        left.append_text(Text.from_markup(ident) if isinstance(ident, str) else ident)
        left.append(" ", style=_DIM)
    if gate:
        left.append_text(verdict_pill(gate, advisory=advisory))
    if detail:
        left.append(f"  {detail}", style=_DIM)
    if save_key:
        left.append("   press ", style=_DIM)
        left.append(save_key, style=f"bold {_ACCENT}")
        left.append(" to save", style=_DIM)
    table = RichTable(box=None, expand=True, show_header=False, padding=0)
    table.add_column(justify="left")
    table.add_column(justify="right")
    right = Text.from_markup(env) if isinstance(env, str) else env
    table.add_row(left, right)
    return table


# ── verdict box ───────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class CheckRow:
    """One rule row in the verdict box — a view-model both tabs can build.

    ``state``: ``held`` / ``broke`` / ``warn_broke`` / ``warn_held`` / ``error``.
    ``evidence`` is the second line of a broken row (``expected · got`` for an
    assertion, ``baseline → candidate`` for a diff rule); empty renders one line.
    """

    label: str
    state: str
    provenance: str = ""
    evidence: str = ""
    detail: str = ""


_ROW_MARKS: dict[str, tuple[str, str]] = {
    "held": ("✓", _SAME),
    "broke": ("✗", _DRIFT),
    "warn_broke": ("~", _WARN),
    "warn_held": ("✓", _SAME),
    "error": ("!", _WARN),
}


def verdict_box_header(rows: list[CheckRow], total: int | None = None) -> Text:
    """The box's first line — always the ``N of M`` auditable form.

    *total* names the full effective rule count when the rows list only the
    interesting ones (the diff verdict box lists broken rules, not every
    volatile ignore).
    """
    total = total if total is not None else len(rows)
    broke = sum(1 for row in rows if row.state == "broke")
    advisory = sum(1 for row in rows if row.state == "warn_broke")
    if broke:
        head = Text(f"✗ {broke} of {total} rules broke on this cell", style=f"bold {_DRIFT}")
    elif advisory:
        head = Text(f"✓ every gating rule held — {advisory} advisory broke", style=f"bold {_SAME}")
    else:
        head = Text(f"✓ every rule held — {total}/{total}", style=f"bold {_SAME}")
    return head


def check_row_cells(row: CheckRow) -> tuple[Text, Text, Text]:
    """One rule row as THREE columns — ``(rule, source, evidence)`` cells.

    The DataTable costume of the verdict card (a focusable table) needs per-column
    cells rather than the wrapped lines ``check_row_lines`` builds; both are the
    same grammar over ``_ROW_MARKS``, so the row glyph/label/suffix rules can
    never fork between the Static box and the table.
    """
    glyph, color = _ROW_MARKS.get(row.state, ("·", _DIM))
    rule = Text(f"{glyph} ", style=f"bold {color}")
    rule.append(row.label, style=_TEXT_HI if row.state == "broke" else _TEXT)
    if row.state == "warn_broke":
        rule.append("  · warn", style=_DIM)
    elif row.state == "warn_held":
        rule.append("  · warn · held", style=_DIM)
    source = Text(row.provenance, style=_DIM)
    if row.evidence:
        evidence = Text(row.evidence, style=_DRIFT if row.state == "broke" else _WARN)
    else:
        evidence = Text(row.detail, style=_DIM)
    return rule, source, evidence


def check_row_lines(row: CheckRow, *, indent: int = 2) -> list[Text]:
    """One rule row in the verdict-box grammar — the single implementation.

    Broken rules render two lines — label + provenance, then the evidence —
    per the Run Results spec §4; held rules render one auditable line. Both the
    Static verdict box and the run detail tree (whose rows must be Tree leaves)
    consume this, so the grammar can never fork between surfaces.
    """
    glyph, color = _ROW_MARKS.get(row.state, ("·", _DIM))
    line = Text(" " * indent)
    line.append(f"{glyph} ", style=f"bold {color}")
    line.append(row.label, style=_TEXT_HI if row.state == "broke" else _TEXT)
    if row.provenance:
        line.append(f"  · {row.provenance}", style=_DIM)
    if row.state == "warn_broke":
        line.append("  · warn", style=_DIM)
    if row.state == "warn_held":
        line.append("  · warn · held", style=_DIM)
    if row.detail and not row.evidence:
        line.append(f"  {row.detail}", style=_DIM)
    lines = [line]
    if row.evidence:
        evidence = Text(" " * (indent + 4))
        evidence.append(row.evidence, style=_DRIFT if row.state == "broke" else _WARN)
        lines.append(evidence)
    return lines


def verdict_box(
    rows: list[CheckRow], *, total: int | None = None, focused: int | None = None
) -> Group:
    """The one verdict box: header, then one (or two) lines per rule.

    The ``focused`` row carries the selection style for the tab-focus model.
    """
    parts: list[RenderableType] = [verdict_box_header(rows, total)]
    for index, row in enumerate(rows):
        lines = check_row_lines(row)
        if focused is not None and index == focused:
            lines[0].stylize(f"on {_INK}")
        parts.extend(lines)
    return Group(*parts)


# ── rule record chrome ────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class StatChip:
    """One ``label N`` chip in a record card's stat row."""

    label: str
    count: int
    style: str


def stat_chips(chips: list[StatChip]) -> Text:
    """The record card's stat row — pill-shaped chips, like the mockup's ``.stat``.

    ``enforced 4`` ``✗ broke 3`` ``✓ held 1`` — each chip on its own subtle
    background so the tally reads as badges, not a sentence.
    """
    text = Text()
    for index, chip in enumerate(chips):
        if index:
            text.append("  ")
        style = chip.style if chip.count else _DIM
        text.append(f" {chip.label} {chip.count} ", style=f"{style} on {_PILL_BG}")
    return text


def record_table(*, header: bool = True, expand: bool = True) -> RichTable:
    """The one data-table chrome — the mockup's ``table.t tight``.

    A dim UPPERCASE header row and hairline separators between every row.
    Every ledger, record card, and spec block that renders as a Rich table
    builds on this frame; callers add their own columns and rows.
    """
    return RichTable(
        box=rich_box.HORIZONTALS,
        show_header=header,
        show_lines=True,
        show_edge=False,
        expand=expand,
        pad_edge=False,
        padding=(0, 1),
        border_style=_LINE,
        header_style=f"bold {_LABEL}",
    )


def spec_rows(rows: list[tuple[str, Text | str]]) -> list[Text]:
    """The record card's spec block as lines — dim labels, one value per line.

    The Static record card groups these; the run rule-record tree mounts them as
    leaves. One implementation, two mounts.
    """
    lines: list[Text] = []
    width = max((len(label) for label, _ in rows), default=0) + 2
    for label, value in rows:
        line = Text(f"{label:<{width}}", style=_DIM)
        line.append_text(Text(value, style=_TEXT) if isinstance(value, str) else value)
        lines.append(line)
    return lines


def spec_table(rows: list[tuple[str, Text | str]]) -> RichTable:
    """The record card's spec block — dim labels, hairline-separated rows."""
    table = record_table(header=False)
    table.add_column(no_wrap=True, style=_DIM, min_width=9)
    table.add_column()
    for label, value in rows:
        table.add_row(label, Text(value, style=_TEXT) if isinstance(value, str) else value)
    return table


# ── error panel ───────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class ErrorPanelModel:
    """The dead-cell story: what failed, how hard we tried, what survives."""

    message: str  # the verbatim engine error (already redacted for display)
    attempts: int = 1
    retry_policy: str | None = None
    meaning: str = ""  # "what this means" prose
    rerun_hint: str = ""  # e.g. "x re-runs the diff (all 6 cells)"


def error_panel_lines(model: ErrorPanelModel) -> list[Text]:
    """The error story as lines — the one implementation both costumes consume.

    The Static panel stacks these in a Group; the run detail tree mounts them as
    leaves (a Tree label cannot hold a Group). Either way the grammar is this.
    """
    head = Text("! ", style=f"bold {_WARN}")
    head.append(model.message, style=f"bold {_WARN}")
    lines = [head]
    tried = Text("  attempts  ", style=_DIM)
    tried.append(str(model.attempts), style=_TEXT_HI)
    if model.retry_policy:
        tried.append(f"  · retry {model.retry_policy}", style=_DIM)
    lines.append(tried)
    if model.meaning:
        lines.append(Text(f"  {model.meaning}", style=_DIM))
    return lines


def error_panel(model: ErrorPanelModel, evidence: RenderableType | None = None) -> Group:
    """The one error panel — no fake rows, no empty wells, the story in full."""
    parts: list[RenderableType] = list(error_panel_lines(model))
    if evidence is not None:
        parts.append(evidence)
    if model.rerun_hint:
        hint = Text("  → ", style=_DIM)
        hint.append_text(Text.from_markup(model.rerun_hint))
        parts.append(hint)
    return Group(*parts)


# ── navigation stack ──────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class NavEntry:
    """One place to return to: which index, which row, which pane had focus."""

    index_mode: str
    row_key: str | None = None
    pane: str = "index"
    label: str = ""  # human name for the crumb ("Price quote · plan=pro")


class NavStack:
    """The cross-view return stack — push on a jump, pop on ``esc``.

    Cleared on any re-run: a jump target's row keys do not survive fresh data.
    """

    def __init__(self) -> None:
        """Start empty — at the root, esc keeps its ordinary back meaning."""
        self._entries: list[NavEntry] = []

    def push(self, entry: NavEntry) -> None:
        """Record where the user is jumping FROM."""
        self._entries.append(entry)

    def pop(self) -> NavEntry | None:
        """The place to return to, or ``None`` when the stack is empty."""
        return self._entries.pop() if self._entries else None

    def clear(self) -> None:
        """Drop every entry — call whenever the underlying data re-runs."""
        self._entries.clear()

    def __bool__(self) -> bool:
        """Truthy while there is somewhere to return to."""
        return bool(self._entries)

    def crumb(self) -> Text | None:
        """The ``from X — esc returns`` arrival crumb, or ``None`` at the root."""
        if not self._entries:
            return None
        origin = self._entries[-1]
        crumb = Text("from ", style=_DIM)
        crumb.append(origin.label or origin.index_mode, style=_TEXT_HI)
        crumb.append(" — ", style=_DIM)
        crumb.append("esc", style=f"bold {_ACCENT}")
        crumb.append(" returns", style=_DIM)
        return crumb


# ── index pane frame ──────────────────────────────────────────────────────────


class IndexPane(Vertical):
    """The left-index frame every results surface shares.

    Header (title + pivot pill) · filter row · the rows table · a legend footer.
    The owning view populates the table and reacts to its events; the frame owns
    only the chrome, so the three pivots re-skin one widget instead of three.
    """

    DEFAULT_CSS = """
    IndexPane { border: round $primary-darken-2; }
    IndexPane > .index-header, IndexPane > .index-filter, IndexPane > .index-legend {
        height: auto; padding: 0 1;
    }
    IndexPane > DataTable { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        """The fixed skeleton: header, filter, table, legend."""
        yield Static("", classes="index-header")
        yield Static("", classes="index-filter filterrow")
        yield DataTable(cursor_type="row", show_header=False)
        yield Static("", classes="index-legend")

    @property
    def table(self) -> DataTable[Text]:
        """The rows table the owning view populates."""
        return self.query_one(DataTable)

    def set_header(self, title: str, pivots: tuple[str, ...], active: str) -> None:
        """Title on the left, the pivot pill on the right."""
        header = Text(title, style=f"bold {_LABEL}")
        header.append("  ")
        header.append_text(seg_pill(pivots, active))
        self.query_one(".index-header", Static).update(header)

    def set_filter(
        self, query: str, *, toggle_key: str, toggle_label: str, toggle_on: bool
    ) -> None:
        """The one filter strip; pass ``query=""`` for the placeholder."""
        self.query_one(".index-filter", Static).update(
            filter_row(query, toggle_key=toggle_key, toggle_label=toggle_label, toggle_on=toggle_on)
        )

    def set_legend(self, legend: Text | str) -> None:
        """The glyph legend under the table."""
        self.query_one(".index-legend", Static).update(
            Text.from_markup(legend) if isinstance(legend, str) else legend
        )


#: Provenance display grammar — the one place the suffix strings live.
_PROVENANCE_LABELS: dict[str, Callable[[str | None], str]] = {
    "profile": lambda name: f"profile {name}" if name else "profile",
    "inline": lambda name: f"inline · {name}" if name else "inline",
    "default": lambda name: "default",
    "synthetic": lambda name: "built-in",
}


def provenance_suffix(origin: str, name: str | None = None) -> str:
    """Render rule provenance for display — ``profile diff.pricing``, ``built-in``.

    Mirrors :func:`comparo.core.outcomes.provenance_label`, with the TUI's one
    divergence: synthetics read "built-in" so a user never hunts their profiles
    for a rule comparo made up.
    """
    render = _PROVENANCE_LABELS.get(origin)
    return render(name) if render is not None else origin


# The evidence tree, git wells, and payload renderers stay in render.py — they
# are the per-domain body renderers the components above compose around.
