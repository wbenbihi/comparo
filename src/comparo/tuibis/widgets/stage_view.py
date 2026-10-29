"""Launch stage compositions — static plan stage plus interactive scope and run views."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping

from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widget import Widget

from comparo.tuibis.viewmodels import ListItemVM
from comparo.tuibis.viewmodels import PanelVM
from comparo.tuibis.viewmodels import StageViewVM
from comparo.tuibis.viewmodels import StyledLine
from comparo.tuibis.widgets.cursor_list import CursorList
from comparo.tuibis.widgets.filter_bar import FilterBar
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.lines import LineBlock
from comparo.tuibis.widgets.lines import line_panel
from comparo.tuibis.widgets.lines import render_line
from comparo.tuibis.widgets.panel import Panel
from comparo.tuibis.widgets.sections import TextRow

LinesBuilder = Callable[[bool], tuple[StyledLine, ...]]
MathBuilder = Callable[[frozenset[str], bool, str], tuple[StyledLine, ...]]
DetailBuilder = Callable[[str], tuple[StyledLine, ...]]


def _columns(vm: StageViewVM, left_id: str | None = None) -> Horizontal:
    left = Vertical(
        *(line_panel(panel) for panel in vm.left),
        classes="stack",
        id=left_id,
    )
    right = Vertical(
        *(line_panel(panel) for panel in vm.right),
        classes="right-col",
    )
    return Horizontal(left, right, classes="stack")


class StageView(Vertical):
    """One static launch stage: full-width panels, columns, and an action line."""

    def __init__(self, vm: StageViewVM) -> None:
        """Compose the stage per its VM shape."""
        children: list[Widget] = []
        if vm.full:
            children.append(line_panel(vm.full[0]))
        children.append(_columns(vm))
        children.extend(line_panel(panel) for panel in vm.full[1:])
        if vm.action_line:
            children.append(
                TextRow(
                    render_line(StyledLine(vm.action_line)),
                    classes="stack-auto",
                )
            )
        super().__init__(*children, classes="stack")


class ScopeStageView(Vertical):
    """Interactive scope stage: a filterable request list driving live scope math."""

    def __init__(
        self,
        *,
        items: tuple[ListItemVM, ...],
        profiles: Mapping[str, tuple[ListItemVM, ...]],
        chips: tuple[str, ...],
        topology: LinesBuilder,
        math: MathBuilder,
        side_panels: tuple[PanelVM, ...],
    ) -> None:
        """Compose the request list, topology, side panels, and live math."""
        self._profiles = dict(profiles)
        self._topology_builder = topology
        self._math_builder = math
        self._candidate_on = True
        self._prefilled_by = ""
        self._query = ""
        self._scope_list = CursorList(items, id="scope-list")
        self._filter = FilterBar("filter requests…", chips, chips[0] if chips else "")
        self._list_panel = Panel(
            self._scope_list,
            self._filter,
            title="» WHAT RUNS",
            hint="space select · a/n all/none · / filter",
        )
        self._topology_block = LineBlock(topology(self._candidate_on))
        self._math_lines = math(self._scope_list.selected_keys, self._candidate_on, "")
        self._math_block = LineBlock(self._math_lines)
        left = Vertical(self._list_panel, classes="stack", id="scope-left")
        right = Vertical(
            Panel(self._topology_block, title="TOPOLOGY  ⇄  directed", hint="c candidate on/off"),
            *(line_panel(panel) for panel in side_panels),
            Panel(self._math_block, title="SCOPE MATH — recomputes live"),
            classes="right-col",
        )
        super().__init__(Horizontal(left, right, classes="stack"), classes="stack")

    # ------------------------------------------------------------- public API

    def focus_default(self) -> None:
        """Give the request list keyboard focus."""
        self._scope_list.focus()

    def focus_filter(self) -> None:
        """Jump to the filter query input."""
        self._filter.focus_query()

    @property
    def candidate_on(self) -> bool:
        """Whether the candidate environment is part of the run."""
        return self._candidate_on

    @property
    def selected_keys(self) -> frozenset[str]:
        """Keys of the currently selected requests."""
        return self._scope_list.selected_keys

    @property
    def scope_selected_count(self) -> int:
        """Number of selected requests."""
        return len(self._scope_list.selected_keys)

    @property
    def admit_allowed(self) -> bool:
        """Whether the plan may be admitted — at least one request selected."""
        return self.scope_selected_count > 0

    def math_text(self) -> tuple[str, ...]:
        """Plain text of the current SCOPE MATH rows, one string per row."""
        return tuple("".join(cell.text for cell in line.cells) for line in self._math_lines)

    def set_all(self, selected: bool) -> None:
        """Select or clear every request row."""
        self._scope_list.select_all(selected)

    def toggle_candidate(self) -> None:
        """Flip the candidate environment on or off; topology and math follow."""
        self._candidate_on = not self._candidate_on
        self._topology_block.update_lines(self._topology_builder(self._candidate_on))
        self._refresh_math()

    # --------------------------------------------------------------- wiring

    def _refresh_math(self) -> None:
        self._math_lines = self._math_builder(
            self._scope_list.selected_keys, self._candidate_on, self._prefilled_by
        )
        self._math_block.update_lines(self._math_lines)

    def on_filter_bar_changed(self, event: FilterBar.Changed) -> None:
        """Narrow the request list to rows matching the query."""
        event.stop()
        self._query = event.query
        self._scope_list.set_filter(event.query)

    def on_cursor_list_toggled(self, event: CursorList.Toggled) -> None:
        """Recompute scope math whenever the request selection changes."""
        if event.source is self._scope_list:
            event.stop()
            self._refresh_math()

    async def on_cursor_list_activated(self, event: CursorList.Activated) -> None:
        """Apply a quick-start profile when its row is activated."""
        if event.source is self._scope_list and event.key in self._profiles:
            event.stop()
            await self._apply_profile(event.key)

    async def _apply_profile(self, key: str) -> None:
        self._prefilled_by = key.removeprefix("profile:")
        replacement = CursorList(self._profiles[key], id="scope-list")
        await self._scope_list.remove()
        self._scope_list = replacement
        await self._list_panel.mount(replacement, before=self._filter)
        if self._query:
            replacement.set_filter(self._query)
        replacement.focus()
        self._refresh_math()


class RunStageView(Vertical):
    """Interactive run stage: live board, fails-only swap, and a cancel state."""

    def __init__(
        self,
        *,
        header: LinesBuilder,
        boards: Mapping[str, tuple[ListItemVM, ...]],
        board_title: str,
        details: DetailBuilder,
        events: PanelVM,
    ) -> None:
        """Compose the run header, the call-board host, and the live side panels.

        Args:
            header: Builds the header rows; the flag selects the cancelling variant.
            boards: Board rows per key — must define ``all``, ``fails``, ``cancel``.
            board_title: Border title of the board panel.
            details: Builds the SELECTED CALL rows for one board key.
            events: The live events side panel.
        """
        self._header_builder = header
        self._details = details
        self._cancelling = False
        self._mode = "all"
        self._header_block = LineBlock(header(False))
        self._header_panel = Panel(self._header_block, title="RUN", hint="a cancel run")
        self._lists = {key: CursorList(rows, id=f"run-board-{key}") for key, rows in boards.items()}
        self._board_host = InspectorHost(dict(self._lists), initial="all")
        board_panel = Panel(self._board_host, title=board_title, hint="f fails only · ↑↓ select")
        board_panel.add_class("stack")
        self._selected_key = self._lists["all"].cursor_key
        self._detail_block = LineBlock(details(self._selected_key))
        selected_panel = Panel(self._detail_block, title="SELECTED CALL", hint="enter inspect")
        columns = Horizontal(
            Vertical(selected_panel, classes="stack"),
            Vertical(line_panel(events), classes="right-col"),
            classes="stack",
        )
        super().__init__(self._header_panel, board_panel, columns, classes="stack")

    # ------------------------------------------------------------- public API

    def focus_default(self) -> None:
        """Focus the visible call board."""
        self._lists[self._board_host.current].focus()

    @property
    def board_mode(self) -> str:
        """Key of the visible board: ``all``, ``fails``, or ``cancel``."""
        return self._board_host.current

    @property
    def cancelling(self) -> bool:
        """Whether the run is in its cancelling state."""
        return self._cancelling

    @property
    def selected_call_key(self) -> str:
        """Board key of the call shown in the SELECTED CALL panel."""
        return self._selected_key

    def detail_text(self) -> tuple[str, ...]:
        """Plain text of the current SELECTED CALL rows, one string per row."""
        return tuple(
            "".join(cell.text for cell in line.cells) for line in self._details(self._selected_key)
        )

    def toggle_fails(self) -> None:
        """Swap between the full board and the fails-only board."""
        if self._cancelling:
            return
        self._mode = "fails" if self._mode == "all" else "all"
        self._show_board(self._mode)

    def toggle_cancel(self) -> None:
        """Flip the cancelling state — amber header and drained board rows."""
        self._cancelling = not self._cancelling
        self._header_block.update_lines(self._header_builder(self._cancelling))
        self._header_panel.set_class(self._cancelling, "tone-warning")
        self._header_panel.border_title = "RUN — CANCELLING" if self._cancelling else "RUN"
        self._show_board("cancel" if self._cancelling else self._mode)

    # --------------------------------------------------------------- wiring

    def _show_board(self, key: str) -> None:
        self._board_host.show(key)
        shown = self._lists[key]
        shown.focus()
        self._set_detail(shown.cursor_key)

    def _set_detail(self, key: str) -> None:
        self._selected_key = key
        self._detail_block.update_lines(self._details(key))

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """Refresh the SELECTED CALL panel as the board cursor moves."""
        if event.source in self._lists.values():
            event.stop()
            self._set_detail(event.key)
