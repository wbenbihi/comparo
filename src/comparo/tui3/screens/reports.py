"""Reports — the saved-run archive; opening a run replays its analyze read-only."""

from __future__ import annotations

from typing import override

from rich.text import Text
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widget import Widget

from comparo.tui3 import fixtures as fx
from comparo.tui3.screens.base import WorkspaceScreen
from comparo.tui3.widgets import CursorList
from comparo.tui3.widgets import FilterInput
from comparo.tui3.widgets import Panel
from comparo.tui3.widgets import PanelHeader
from comparo.tui3.widgets import Row
from comparo.tui3.widgets import TextRow
from comparo.tui3.widgets import fragments as fr

REPORT_BY_KEY = {r.key: r for r in fx.REPORTS}
REPLAY_KEYS = ("t", "v", "o", "left_square_bracket", "right_square_bracket")
REPLAY_HINTS = (
    ("↑↓", "row"),
    ("t", "index"),
    ("[ ]", "facet"),
    ("v", "diff view"),
    ("o", "outbound"),
    ("esc", "back"),
    ("q", "quit"),
)


def _report_row(report: fx.ReportFx) -> Text:
    """One archive table row: status · run · when · scope · drift · calls · dur."""
    drift = (
        fr.t(f"{report.drift:>5}", "drift.b") if report.drift else fr.t(f"{report.drift:>5}", "dim")
    )
    return fr.join(
        fr.t(f"{report.state:<9}", f"{report.sc}.b"),
        fr.t(f"{report.key:<9}", "b"),
        fr.t(f"{report.when:<17}", "dim"),
        fr.t(f"{report.scope:<29}", "fg"),
        drift,
        fr.t(f"{report.calls:>7}", "dim"),
        fr.t(f"{report.dur:>6}", "dim"),
    )


class ReportsScreen(WorkspaceScreen):
    """The Reports workspace: browse the archive, replay a run's analyze."""

    def __init__(self) -> None:
        """Start browsing on the newest run."""
        super().__init__(id="ws-reports")
        self._sel = fx.REPORTS[0].key
        self._mode = "browse"
        self._replay: Widget | None = None
        self._filter = FilterInput("filter… status · env · tag", id="reports-filter")
        self._list = CursorList(self._make_rows(), id="reports-list")
        self._summary_head = PanelHeader(f"Summary — {self._sel}")
        self._summary = Vertical(id="reports-summary")
        self._browse = Horizontal(
            Panel(
                PanelHeader(
                    "Saved runs — read-only, opening never sends HTTP", "enter opens analyze"
                ),
                self._filter,
                self._list,
                TextRow(
                    fr.join(
                        fr.t("✗ fail", "drift"),
                        fr.sep(),
                        fr.t("✓ pass", "same"),
                        fr.sep(),
                        fr.t("✗ error", "warn"),
                        fr.sep(),
                        fr.t(f"{len(fx.REPORTS)} runs · .runs/", "dim"),
                    )
                ),
                id="reports-list-panel",
                classes="col-left-wide",
            ),
            Panel(
                self._summary_head,
                self._summary,
                hi=True,
                id="reports-summary-panel",
                classes="col-right-narrow",
            ),
            classes="cols",
            id="reports-browse",
        )

    # ----------------------------------------------------------- composition

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Archive list beside the summary panel."""
        yield self._browse

    def on_mount(self) -> None:
        """Render the initial summary."""
        self._render_summary()

    def _make_rows(self) -> tuple[Row, ...]:
        """Header row + one row per saved run."""
        head = fr.t(
            f"{'STATUS':<9}{'RUN':<9}{'WHEN':<17}{'SCOPE':<29}{'DRIFT':>5}{'CALLS':>7}{'DUR':>6}",
            "ph",
        )
        rows = [Row("hdr", head, header=True)]
        rows.extend(
            Row(
                report.key,
                _report_row(report),
                search=f"{report.key} {report.state} {report.when} {report.scope}",
            )
            for report in fx.REPORTS
        )
        return tuple(rows)

    # -------------------------------------------------------------- summary

    def _kv(self, label: str, *value: Text) -> TextRow:
        """One dim-label key/value line."""
        return TextRow(fr.join(fr.t(f"  {label:<10}", "dim"), *value))

    def _summary_widgets(self) -> list[Widget]:
        """The summary panel body for the selected run."""
        report = REPORT_BY_KEY[self._sel]
        out: list[Widget] = [
            TextRow(
                fr.join(
                    fr.t(report.state, f"{report.sc}.b"),
                    fr.t(f" · exit {report.exit_code}", "dim"),
                )
            ),
            TextRow(fr.t(report.note, "dim"), classes="mb1"),
            self._kv("scope", fr.t(report.scope, "hi")),
            self._kv("calls", fr.t(f"{report.calls} · {report.dur}", "hi")),
            self._kv("artifact", fr.t(f".runs/{report.key}", "hi")),
        ]
        if report.top:
            out.append(TextRow(fr.t("TOP FINDINGS", "ph")))
            out.extend(
                TextRow(fr.join(fr.t("✗ ", "drift"), fr.t(finding, "fg"))) for finding in report.top
            )
        else:
            out.append(TextRow(fr.t("✓ clean run", "same")))
        out.append(TextRow(Text(" ")))
        out.append(TextRow(fr.t(" enter open analyze ", "btn.primary")))
        return out

    def _render_summary(self) -> None:
        """Repaint the summary panel for the selected run."""
        self._summary_head.set_left(fr.t(f"SUMMARY — {self._sel}", "ph"))
        self._summary.remove_children()
        self._summary.mount_all(self._summary_widgets())

    # --------------------------------------------------------------- replay

    def _analyze_view(self) -> Widget | None:
        """Lazily build AnalyzeView(replay=True) — the shared analyze contract."""
        from importlib import import_module

        try:
            module = import_module("comparo.tui3.screens.analyze")
        except ImportError:
            return None
        factory = getattr(module, "AnalyzeView", None)
        if factory is None:
            return None
        view = factory(replay=True)
        return view if isinstance(view, Widget) else None

    def _open_replay(self) -> None:
        """Swap the browse body for the read-only analyze view."""
        view = self._analyze_view()
        if view is None:
            self.notify("analyze view is not available yet")
            return
        self._replay = view
        self._mode = "replay"
        self._browse.display = False
        self.mount(view)
        self.screen.set_focus(None)
        focus = getattr(view, "focus_default", None)
        if callable(focus):
            focus()
        self._refresh_chrome()

    def _close_replay(self) -> None:
        """Drop the analyze view and return to the archive."""
        if self._replay is not None:
            self._replay.remove()
            self._replay = None
        self._mode = "browse"
        self._browse.display = True
        self._list.focus()
        self._refresh_chrome()

    def _refresh_chrome(self) -> None:
        """Ask the app to repaint crumb and footer (guarded, no circular import)."""
        refresh = getattr(self.app, "refresh_chrome", None)
        if callable(refresh):
            refresh()

    # --------------------------------------------------------------- events

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """The summary follows the archive cursor."""
        if event.source is not self._list or event.key == "hdr":
            return
        self._sel = event.key
        self._render_summary()
        self._refresh_chrome()

    def on_cursor_list_activated(self, event: CursorList.Activated) -> None:
        """Enter (or click) on a run opens its analyze in replay."""
        if event.source is not self._list or event.key == "hdr":
            return
        self._sel = event.key
        self._render_summary()
        self._open_replay()

    def on_filter_input_filter_changed(self, event: FilterInput.FilterChanged) -> None:
        """Filter the archive live."""
        self._list.set_filter(event.query)

    # ----------------------------------------------------------- app wiring

    @override
    def crumb(self) -> str:
        """Navrow crumb."""
        if self._mode == "replay":
            return "reports · replay 8c3e11"
        return f"reports · {self._sel}"

    @override
    def tier(self) -> str:
        """Navrow tier badge."""
        return "REPORTS"

    @override
    def hints(self) -> list[tuple[str, str]]:
        """Footer keys."""
        if self._mode == "replay":
            if self._replay is not None:
                hints = getattr(self._replay, "hints", None)
                if callable(hints):
                    got = hints()
                    if isinstance(got, list) and got:
                        return got
            return list(REPLAY_HINTS)
        return [("↑↓", "select"), ("enter", "open analyze"), ("e", "export"), ("q", "quit")]

    @override
    def focus_default(self) -> None:
        """Focus the archive list (or the replay view)."""
        if self._mode == "replay" and self._replay is not None:
            focus = getattr(self._replay, "focus_default", None)
            if callable(focus):
                focus()
            return
        self._list.focus()

    @override
    def on_ws_key(self, key: str) -> bool:
        """Workspace keys: export, filter, replay routing."""
        if self._mode == "replay":
            if key == "escape":
                self._close_replay()
                return True
            if key in REPLAY_KEYS and self._replay is not None:
                route = getattr(self._replay, "route_key", None)
                if callable(route):
                    return bool(route(key))
            return False
        if key == "e":
            self.notify("exported json · junit · sarif · md (mock)")
            return True
        if key == "slash":
            self._filter.focus()
            return True
        return False
