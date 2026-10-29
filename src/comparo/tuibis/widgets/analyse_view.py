"""AnalyseView — the constant 3-panel composition, reused live and in replay.

Run strip · index chips · LIST (a real CursorList) · DETAIL · INSPECT with
facet chips. Moving the list cursor re-binds the detail and inspect panels to
the highlighted row, `s` cycles the inspect facet, `i` cycles indexes, `z`/`d`
open deep dives, `esc` closes them. Reports mounts the identical component
read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from itertools import chain
from typing import ClassVar

from textual.binding import Binding
from textual.containers import Horizontal
from textual.containers import Vertical

from comparo.tuibis.viewmodels import AnalyseIndexVM
from comparo.tuibis.viewmodels import AnalyseVM
from comparo.tuibis.viewmodels import IndexItemVM
from comparo.tuibis.viewmodels import ListItemVM
from comparo.tuibis.viewmodels import StyledLine
from comparo.tuibis.widgets.cursor_list import CursorList
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.lines import LineBlock
from comparo.tuibis.widgets.lines import line_panel
from comparo.tuibis.widgets.panel import Panel
from comparo.tuibis.widgets.strips import FacetChips
from comparo.tuibis.widgets.strips import IndexStrip
from comparo.tuibis.widgets.strips import RunStrip

FACETS = ("ALL", "REQUEST", "RESPONSE", "HEADERS", "EVENTS", "RAW")

_FACET_NOTE = "  cell pro·USD ▾ · side candidate ▾"
_INSPECT_HINT = "z expand · d sent diff · s facet"


@dataclass(frozen=True)
class AnalyseIndexData(AnalyseIndexVM):
    """An analyse index enriched with cursor rows and per-selection variants.

    ``items`` feed the CursorList; ``detail_for``/``inspect_for`` map row keys
    to the lines each panel shows when that row is highlighted (keys without a
    variant fall back to the panel defaults). ``facet_for`` maps a facet name
    to per-key variants, with ``""`` as the shared default for that facet.
    """

    items: tuple[ListItemVM, ...] = ()
    detail_titles: dict[str, str] = field(default_factory=dict)
    detail_for: dict[str, tuple[StyledLine, ...]] = field(default_factory=dict)
    inspect_for: dict[str, tuple[StyledLine, ...]] = field(default_factory=dict)
    facet_for: dict[str, dict[str, tuple[StyledLine, ...]]] = field(default_factory=dict)


def _pad(lines: tuple[StyledLine, ...], depth: int) -> tuple[StyledLine, ...]:
    """Pad a variant with blank rows so every later variant fits in place."""
    return lines + tuple(StyledLine(()) for _ in range(depth - len(lines)))


class AnalyseView(Vertical):
    """The whole analyse surface as one reusable component."""

    BINDINGS: ClassVar = [
        Binding("s", "cycle_facet", "Facet", show=False),
    ]

    def __init__(self, vm: AnalyseVM, *, replay: bool = False) -> None:
        """Compose strip, index chips, and the per-index 3-panel bodies."""
        self._vm = vm
        self._order = [index.key for index in vm.indexes]
        self._active = self._order[0]
        self._diving = False
        self._data: dict[str, AnalyseIndexData] = {}
        self._lists: dict[str, CursorList] = {}
        self._cursor_keys: dict[str, str] = {}
        self._facets: dict[str, str] = {}
        self._detail_blocks: dict[str, LineBlock] = {}
        self._detail_panels: dict[str, Panel] = {}
        self._inspect_blocks: dict[str, LineBlock] = {}
        self._inspect_panels: dict[str, Panel] = {}
        self._chips: dict[str, FacetChips] = {}
        strip_vm = vm.strip
        if replay:
            strip_vm = type(strip_vm)(
                run_id=strip_vm.run_id,
                profile=strip_vm.profile,
                baseline=strip_vm.baseline,
                candidate=strip_vm.candidate,
                verdict=strip_vm.verdict,
                tallies=strip_vm.tallies,
                right=strip_vm.right,
                replay=True,
            )
        self._strip = RunStrip(strip_vm)
        self._index_strip = IndexStrip(
            tuple(IndexItemVM(index.key, _LABELS[index.key]) for index in vm.indexes),
            self._active,
        )
        bodies: dict[str, Vertical | Horizontal] = {
            index.key: self._build_body(index) for index in vm.indexes
        }
        for key, dive in vm.deep_dives.items():
            dive_panel = line_panel(dive)
            dive_panel.add_class("dive-panel")
            bodies[f"dive:{key}"] = Vertical(dive_panel, classes="stack")
        self._host = InspectorHost(bodies, initial=self._active)
        super().__init__(self._strip, self._index_strip, self._host, id="analyse-view")

    # ------------------------------------------------------------------ build

    def _build_body(self, index: AnalyseIndexVM) -> Horizontal:
        """Build one index body: interactive when the fixture provides rows."""
        if isinstance(index, AnalyseIndexData) and index.items:
            initial_key = self._register(index)
            list_side = self._build_list(index)
            detail = self._build_detail(index, initial_key)
            inspect = self._build_inspect(index, initial_key)
        else:
            list_side = line_panel(index.list_panel)
            list_side.add_class("analyse-list")
            detail = line_panel(index.detail_panel)
            detail.add_class("analyse-detail")
            chips = FacetChips(FACETS, index.inspect_facet, note=_FACET_NOTE)
            inspect = Panel(
                chips,
                LineBlock(index.inspect_lines),
                title="INSPECT",
                hint=_INSPECT_HINT,
                classes="analyse-inspect",
            )
        right = Vertical(detail, inspect, classes="analyse-right")
        return Horizontal(list_side, right, classes="stack")

    def _register(self, index: AnalyseIndexData) -> str:
        """Record the index data and its initial cursor key; return that key."""
        initial_key = next(
            (item.key for item in index.items if not item.header and not item.dimmed),
            index.items[0].key,
        )
        self._data[index.key] = index
        self._cursor_keys[index.key] = initial_key
        self._facets[index.key] = index.inspect_facet
        return initial_key

    def _build_list(self, index: AnalyseIndexData) -> Panel:
        """Build the left list panel around a CursorList of the index rows."""
        cursor_list = CursorList(index.items)
        self._lists[index.key] = cursor_list
        return Panel(
            cursor_list,
            title=index.list_panel.title,
            hint=index.list_panel.hint,
            classes="analyse-list",
        )

    def _build_detail(self, index: AnalyseIndexData, initial_key: str) -> Panel:
        """Build the detail panel sized for its deepest variant."""
        depth = max([len(index.detail_panel.lines), *map(len, index.detail_for.values())])
        block = LineBlock(_pad(index.detail_for.get(initial_key, index.detail_panel.lines), depth))
        panel = Panel(
            block,
            title=index.detail_titles.get(initial_key, index.detail_panel.title),
            hint=index.detail_panel.hint,
            classes="analyse-detail",
        )
        self._detail_blocks[index.key] = block
        self._detail_panels[index.key] = panel
        return panel

    def _build_inspect(self, index: AnalyseIndexData, initial_key: str) -> Panel:
        """Build the inspect panel sized for its deepest facet variant."""
        variants = chain(
            index.inspect_for.values(),
            (lines for facet in index.facet_for.values() for lines in facet.values()),
        )
        depth = max([len(index.inspect_lines), *map(len, variants)])
        block = LineBlock(_pad(self._variant_lines(index, initial_key, index.inspect_facet), depth))
        chips = FacetChips(FACETS, index.inspect_facet, note=_FACET_NOTE)
        panel = Panel(
            chips,
            block,
            title="INSPECT",
            hint=_INSPECT_HINT,
            classes="analyse-inspect",
        )
        self._inspect_blocks[index.key] = block
        self._inspect_panels[index.key] = panel
        self._chips[index.key] = chips
        return panel

    # ------------------------------------------------------------- selection

    @staticmethod
    def _variant_lines(data: AnalyseIndexData, item_key: str, facet: str) -> tuple[StyledLine, ...]:
        """Resolve the inspect lines for one row under one facet."""
        variants = data.facet_for.get(facet)
        if variants is not None:
            lines = variants.get(item_key, variants.get(""))
            if lines is not None:
                return lines
        return data.inspect_for.get(item_key, data.inspect_lines)

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """Re-bind detail and inspect panels to the newly highlighted row."""
        for index_key, cursor_list in self._lists.items():
            if event.source is cursor_list:
                self._show_selection(index_key, event.key)
                return

    def _show_selection(self, index_key: str, item_key: str) -> None:
        """Update the detail panel, its title, and the inspect body in place."""
        data = self._data[index_key]
        self._cursor_keys[index_key] = item_key
        detail = data.detail_for.get(item_key, data.detail_panel.lines)
        self._detail_blocks[index_key].update_lines(detail)
        title = data.detail_titles.get(item_key, data.detail_panel.title)
        self._detail_panels[index_key].border_title = title
        self._refresh_inspect(index_key)

    def _refresh_inspect(self, index_key: str) -> None:
        """Re-render the inspect body for the current row and facet."""
        data = self._data[index_key]
        lines = self._variant_lines(data, self._cursor_keys[index_key], self._facets[index_key])
        self._inspect_blocks[index_key].update_lines(lines)

    # ----------------------------------------------------------------- facets

    def cycle_facet(self) -> None:
        """Advance the INSPECT facet chip for the active index (s)."""
        if self._diving or self._active not in self._data:
            return
        position = FACETS.index(self._facets[self._active])
        facet = FACETS[(position + 1) % len(FACETS)]
        self._facets[self._active] = facet
        previous = self._chips[self._active]
        chips = FacetChips(FACETS, facet, note=_FACET_NOTE)
        self._chips[self._active] = chips
        self._inspect_panels[self._active].mount(chips, before=previous)
        previous.remove()
        self._refresh_inspect(self._active)

    def action_cycle_facet(self) -> None:
        """Key binding entry point for facet cycling."""
        self.cycle_facet()

    # ------------------------------------------------------------------ focus

    def on_show(self) -> None:
        """Focus the active index's list when the view becomes visible."""
        self._focus_active_list()

    def _focus_active_list(self) -> None:
        """Give keyboard focus to the visible CursorList, if any."""
        cursor_list = self._lists.get(self._active)
        if cursor_list is not None and cursor_list.is_mounted:
            cursor_list.focus()

    # ------------------------------------------------------------- navigation

    def cycle_index(self) -> None:
        """Move to the next index (i)."""
        if self._diving:
            return
        position = self._order.index(self._active)
        self._active = self._order[(position + 1) % len(self._order)]
        self._index_strip.set_active(self._active)
        self._host.show(self._active)
        self._focus_active_list()

    def dive(self, key: str) -> bool:
        """Open a deep dive if the fixture defines it."""
        if key not in self._vm.deep_dives:
            return False
        self._diving = True
        self._host.show(f"dive:{key}")
        return True

    def close_dive(self) -> bool:
        """Close an open deep dive; returns whether one was open."""
        if not self._diving:
            return False
        self._diving = False
        self._host.show(self._active)
        self._focus_active_list()
        return True


_LABELS = {
    "requests": "1 REQUESTS 12",
    "issues": "2 ISSUES 3",
    "rules": "3 RULES 17",
    "fields": "4 FIELDS 38",
    "timeline": "5 TIMELINE 21",
}
