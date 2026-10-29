"""Launch workspace — scope, plan, run, and results, ported from the clickable v2."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any
from typing import ClassVar
from typing import cast
from typing import override

from rich.text import Text
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from comparo.tui3 import fixtures as fx
from comparo.tui3.screens.base import WorkspaceScreen
from comparo.tui3.theme import style
from comparo.tui3.widgets import ChipRow
from comparo.tui3.widgets import CursorList
from comparo.tui3.widgets import FilterInput
from comparo.tui3.widgets import Panel
from comparo.tui3.widgets import PanelHeader
from comparo.tui3.widgets import Row
from comparo.tui3.widgets import SplitRow
from comparo.tui3.widgets import TextRow
from comparo.tui3.widgets import fragments as fr

OVERRIDES: tuple[tuple[str, str, str], ...] = (
    ("trials", "trials", "1"),
    ("attempts", "attempts", "2"),
    ("failfast", "fail-fast", "off"),
    ("rate", "rate/min", "60"),
    ("timeout", "timeout", "5s"),
    ("writes", "writes", "ask"),
)
PROFILE_SCOPE = frozenset({"quote", "feed", "checkout", "orderstat", "product"})
RESULTS_KEYS = ("t", "v", "o", "left_square_bracket", "right_square_bracket")


def _pad_to(text: Text, col: int) -> None:
    """Pad a fragment run with spaces up to an absolute column."""
    text.append(" " * max(1, col - text.cell_len))


@dataclass(frozen=True)
class _RunSpec:
    """One row of the watch-only run table."""

    name: str
    cell: str
    stream: bool
    drift: bool
    slow: bool


class ClickRow(Static):
    """A clickable line that reports presses with a key."""

    DEFAULT_CSS = """
    ClickRow { width: auto; height: auto; margin-right: 2; }
    ClickRow:hover { background: #1a2334; }
    """

    class Pressed(Message):
        """A click landed on the row."""

        def __init__(self, key: str) -> None:
            """Carry the row key."""
            super().__init__()
            self.key = key

    def __init__(self, press_key: str, content: Text) -> None:
        """Store the key and render the content."""
        super().__init__(content)
        self.press_key = press_key

    def on_click(self) -> None:
        """Report the press."""
        self.post_message(self.Pressed(self.press_key))


class _AnalyzePending(Vertical):
    """Placeholder results view mounted when the analyze module is absent."""

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield the pending note."""
        yield Panel(
            PanelHeader("Results"),
            TextRow(fr.t("analyze view not available yet — esc back to scope", "dim")),
            hi=True,
            classes="-auto",
        )

    def route_key(self, key: str) -> bool:
        """No keys handled."""
        return False

    def hints(self) -> list[tuple[str, str]]:
        """No extra footer keys."""
        return []

    def focus_default(self) -> None:
        """Nothing to focus."""

    def crumb_suffix(self) -> str:
        """No crumb suffix."""
        return ""


class LaunchScreen(WorkspaceScreen):
    """The Launch workspace: scope → plan → run → results."""

    RUN_TICK: ClassVar[float] = 0.7
    RESULTS_DELAY: ClassVar[float] = 0.9

    def __init__(self) -> None:
        """Start on the scope stage with the showcase scope state."""
        super().__init__(id="ws-launch")
        self._scope = fx.ScopeState()
        self._stage = "scope"
        self._analyze: Any = None
        self._run_specs: tuple[_RunSpec, ...] = ()
        self._run_pos = 0
        self._run_done = False
        self._run_cancelled = False
        self._run_timer: Timer | None = None

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield the initial scope stage."""
        yield self._make_scope()

    # ---------------------------------------------------------------- stages

    def _set_stage(self, stage: str) -> None:
        """Swap the visible stage, remount its widgets, refresh the chrome."""
        if stage != "run" and self._run_timer is not None:
            self._run_timer.stop()
            self._run_timer = None
        self._stage = stage
        self.remove_children()
        if stage == "scope":
            self.mount(self._make_scope())
        elif stage == "plan":
            self.mount(self._make_plan())
        elif stage == "run":
            self.mount(self._make_run())
        else:
            self.mount(self._make_results())
        self.call_after_refresh(self.focus_default)
        self._refresh_chrome_guarded()

    def _refresh_chrome_guarded(self) -> None:
        """Refresh the app chrome without importing the app class."""
        app = self.app
        refresh = getattr(app, "refresh_chrome", None)
        if callable(refresh):
            refresh()

    # ----------------------------------------------------------------- scope

    def _make_scope(self) -> Horizontal:
        """Build the catalog panel and the live composed-run panel."""
        self._filter = FilterInput("filter requests…")
        self._tag_chips = ChipRow(self._tag_chip_labels())
        self._catalog = CursorList(self._catalog_rows(), click_toggles=True)
        self._topo_row = ClickRow("cand", self._topology_text())
        labels = self._ovr_chip_labels()
        self._ovr_chips = ChipRow(labels[:3])
        self._ovr_chips_b = ChipRow(labels[3:])
        self._warn_row = TextRow(Text(""))
        self._math_row = TextRow(Text(""))
        self._cmd_row = TextRow(Text(""))
        self._plan_btn = ClickRow("plan", fr.t(" p review plan ", "btn"))
        self._run_btn = ClickRow("run", Text(""))
        self._sync_scope_panel()
        left = Panel(
            PanelHeader("Catalog — what can run", "space/click select · click cells"),
            self._filter,
            self._tag_chips,
            self._catalog,
            classes="col-left-wide",
        )
        right = Panel(
            PanelHeader("Your run — composed live", "c candidate · w writes · p plan · x run"),
            TextRow(fr.t("TOPOLOGY", "ph")),
            self._topo_row,
            TextRow(fr.t("CHECKS", "ph")),
            TextRow(
                fr.join(
                    fr.t("⊨ assert.order", "k.assert"),
                    fr.t(" 3 rules · ", "dim"),
                    fr.t("± diff.showcase", "k.diff"),
                    fr.t(" 4 rules · gate: complete run, 0 errors", "dim"),
                )
            ),
            TextRow(fr.t("OVERRIDES — SESSION ONLY", "ph")),
            self._ovr_chips,
            self._ovr_chips_b,
            self._warn_row,
            TextRow(fr.t("SCOPE MATH", "ph")),
            self._math_row,
            TextRow(fr.t("EQUIVALENT COMMAND", "ph")),
            self._cmd_row,
            TextRow(Text(" ")),
            Horizontal(self._plan_btn, self._run_btn, classes="stack-auto"),
            hi=True,
            classes="col-right",
        )
        if self._scope.query:
            self._filter.value = self._scope.query
            self.call_after_refresh(self._catalog.set_filter, self._scope.query)
        return Horizontal(left, right, classes="cols")

    def _catalog_rows(self) -> tuple[Row, ...]:
        """Catalog rows grouped by tag, with cell rows under the matrix request."""
        rows: list[Row] = []
        for tag in fx.TAGS[1:]:
            reqs = [
                r
                for r in fx.REQS
                if tag in r.tags and (self._scope.tag == "all" or self._scope.tag in r.tags)
            ]
            if not reqs:
                continue
            if self._scope.tag == "all":
                rows.append(Row(f"grp:{tag}", fr.t(f"▾ {tag.upper()}", "dim"), header=True))
            for req in reqs:
                on = self._scope.on[req.id]
                text = fr.join(fr.entity("req", req.name), fr.t("  "), fr.method(req.method))
                if req.write:
                    text.append_text(fr.t("  ~w", "badge.w"))
                if req.stream:
                    text.append_text(fr.t("  sse", "badge.s"))
                if req.matrix:
                    text.append_text(fr.t("  x plans", "axis"))
                rows.append(
                    Row(f"req:{req.id}", text, selectable=True, selected=on, search=req.name)
                )
                if req.matrix and on:
                    rows.extend(
                        Row(
                            f"cell:{cell}",
                            fr.t(cell, "axis"),
                            selectable=True,
                            selected=self._scope.cells[cell],
                            search=f"{req.name} {cell}",
                            indent=4,
                        )
                        for cell in fx.PLAN_CELLS
                    )
        rows.append(
            Row(
                "profile:release",
                fr.join(
                    fr.t("start from a profile instead: ", "dim"),
                    fr.t("▶ Release gate", "k.exec"),
                    fr.t(" prefills scope + checks + limits (stays editable)", "dim"),
                ),
                search="release gate profile",
            )
        )
        return tuple(rows)

    def _rebuild_catalog(self) -> None:
        """Rebuild the catalog list from ScopeState (cell rows come and go)."""
        old = self._catalog
        previous = old.cursor_key
        container = old.parent
        self._catalog = CursorList(self._catalog_rows(), click_toggles=True)
        old.remove()
        if isinstance(container, Widget):
            container.mount(self._catalog, after=self._tag_chips)
        if self._scope.query:
            self.call_after_refresh(self._catalog.set_filter, self._scope.query)
        self._catalog.move_cursor_to(previous)
        self._catalog.focus()

    def _tag_chip_labels(self) -> tuple[tuple[str, Text], ...]:
        """Tag chips — the active tag is highlighted."""
        return tuple(
            (tag, fr.t(tag, "acc.b" if tag == self._scope.tag else "dim")) for tag in fx.TAGS
        )

    def _ovr_value(self, key: str) -> str:
        """Current override value as text."""
        return str(getattr(self._scope, key))

    def _ovr_chip_labels(self) -> tuple[tuple[str, Text], ...]:
        """Override chips — amber when touched, defaults shown inline."""
        out: list[tuple[str, Text]] = []
        for key, label, default in OVERRIDES:
            value = self._ovr_value(key)
            touched = value != default
            text = fr.join(fr.t(f"{label} ", "dim"), fr.t(value, "warn.b" if touched else "b"))
            if touched:
                text.append_text(fr.t(f" ←{default}", "dim"))
            out.append((key, text))
        return tuple(out)

    def _cycle_override(self, key: str) -> None:
        """Advance one override exactly like the mockup chips."""
        scope = self._scope
        if key == "trials":
            scope.trials = scope.trials + 1 if scope.trials < 3 else 1
        elif key == "attempts":
            scope.attempts = scope.attempts + 1 if scope.attempts < 3 else 2
        elif key == "failfast":
            scope.failfast = "on" if scope.failfast == "off" else "off"
        elif key == "rate":
            scope.rate = {60: 100, 100: 250}.get(scope.rate, 60)
        elif key == "timeout":
            order = ("5s", "15s", "60s")
            scope.timeout = order[(order.index(scope.timeout) + 1) % len(order)]
        elif key == "writes":
            scope.writes = "approved" if scope.writes == "ask" else "ask"

    def _topology_text(self) -> Text:
        """The baseline ⇄ candidate line; a click toggles the candidate."""
        base = fr.join(
            fr.t("baseline ", "dim"),
            fr.t("stable", "b"),
            fr.t(" ● ", "same"),
            fr.pill("default", "pill.default"),
            fr.t("  ⇄  ", "axis"),
        )
        if self._scope.cand:
            return fr.join(
                base,
                fr.t("candidate ", "dim"),
                fr.t("canary", "b"),
                fr.t(" "),
                fr.pill("live", "pill.live"),
                fr.t("  remove", "dim"),
            )
        return fr.join(base, fr.t("+ add candidate", "acc"), fr.t(" · assertions-only run", "warn"))

    def _warn_text(self, writes: int) -> Text:
        """The write-approval warning line."""
        return fr.t(
            f"~ {writes} write-capable selected — approve writes (w or the chip) before x can run.",
            "warn",
        )

    def _math_text(self) -> Text:
        """The live scope-math line."""
        reqs, cells, writes, envs, calls = self._scope.counts()
        est = max(1, round(calls * 0.4))
        verdict = (
            fr.t("⇄ DIFF", "acc.b") if self._scope.cand else fr.t("RUN assertions-only", "same.b")
        )
        return fr.join(
            fr.t(str(reqs), "b"),
            fr.t(" requests · ", "dim"),
            fr.t(str(cells), "b"),
            fr.t(" cells x ", "dim"),
            fr.t(str(envs), "b"),
            fr.t(" env " if envs == 1 else " envs ", "dim"),
            fr.t(f"= {calls} calls", "acc.b"),
            fr.t(f" · est ~{est}s · {writes} write{'' if writes == 1 else 's'} · ", "dim"),
            verdict,
        )

    def _cmd_text(self) -> Text:
        """The equivalent-command line."""
        scope = self._scope
        head = f"comparo {'diff' if scope.cand else 'run'}"
        rest = " --baseline stable"
        if scope.cand:
            rest += " --candidate canary"
        if scope.tag != "all":
            rest += f" --tag {scope.tag}"
        if scope.rate != 60:
            rest += f" --rate {scope.rate}"
        if scope.writes == "approved":
            rest += " --approve-writes"
        return fr.join(fr.t(head, "cmd.b"), fr.t(rest, "cmd"))

    def _run_btn_text(self, calls: int, blocked: bool) -> Text:
        """The x-run button label — blocked style while writes wait."""
        return fr.t(f" x run {calls} calls ", "btn.blocked" if blocked else "btn.primary")

    def _sync_scope_panel(self) -> None:
        """Recompute every live element of the composed-run panel."""
        _, _, writes, _, calls = self._scope.counts()
        blocked = bool(writes) and self._scope.writes != "approved"
        self._topo_row.update(self._topology_text())
        labels = self._ovr_chip_labels()
        self._ovr_chips.set_chips(labels[:3])
        self._ovr_chips_b.set_chips(labels[3:])
        self._warn_row.display = blocked
        if blocked:
            self._warn_row.update(self._warn_text(writes))
        self._math_row.update(self._math_text())
        self._cmd_row.update(self._cmd_text())
        self._run_btn.update(self._run_btn_text(calls, blocked))

    def _selected_catalog_keys(self) -> set[str]:
        """Selection keys implied by the current ScopeState."""
        keys = {f"req:{rid}" for rid, on in self._scope.on.items() if on}
        keys.update(f"cell:{cell}" for cell, on in self._scope.cells.items() if on)
        return keys

    def _try_run(self) -> bool:
        """Start the run unless unapproved writes block it."""
        _, _, writes, _, _ = self._scope.counts()
        if writes and self._scope.writes != "approved":
            where = "on scope (w)" if self._stage == "plan" else "first (w)"
            self.notify(f"✗ blocked — approve writes {where}")
            return False
        self._start_run()
        return True

    # ------------------------------------------------------------------ plan

    def _plan_cells(self) -> list[tuple[fx.RequestFx, str]]:
        """Every admitted (request, cell) pair, in run order."""
        out: list[tuple[fx.RequestFx, str]] = []
        for rid, on in self._scope.on.items():
            if not on:
                continue
            req = fx.REQ_BY_ID[rid]
            if req.matrix:
                out.extend((req, cell) for cell in fx.PLAN_CELLS if self._scope.cells[cell])
            else:
                out.append((req, "stream" if req.stream else "—"))
        return out

    def _make_plan(self) -> Horizontal:
        """Build the admitted-plan stage."""
        return Horizontal(
            Panel(*self._plan_left(), hi=True, classes="col-left-wide"),
            Panel(*self._plan_right(), classes="col-right"),
            classes="cols",
        )

    def _plan_left(self) -> list[Widget]:
        """Destinations, cells, and checks."""
        per_cell = 2 if self._scope.cand else 1
        children: list[Widget] = [
            PanelHeader("Plan 9f4b2e — admitted, nothing sent", "x run · esc edit"),
            TextRow(fr.t("1 · DESTINATIONS", "ph")),
            TextRow(fr.t("  ROLE        ENV      HOST                  TLS", "ph")),
            TextRow(
                fr.join(
                    fr.t("  baseline    ", "dim"),
                    fr.t("stable   ", "b"),
                    fr.t("127.0.0.1:8091        ", "fg"),
                    fr.t("—         ", "same"),
                    fr.pill("default", "pill.default"),
                )
            ),
        ]
        if self._scope.cand:
            children.append(
                TextRow(
                    fr.join(
                        fr.t("  candidate   ", "dim"),
                        fr.t("canary   ", "b"),
                        fr.t("canary.internal:443   ", "fg"),
                        fr.t("✓ pinned  ", "same"),
                        fr.pill("live", "pill.live"),
                    )
                )
            )
        else:
            children.append(TextRow(fr.t("  no candidate — assertions-only", "dim")))
        children.append(TextRow(fr.t("2 · CELLS — EVERY CALL, IN ORDER", "ph")))
        children.append(TextRow(fr.t("  #    REQUEST          CELL      CALLS   WRITES", "ph")))
        children.extend(
            TextRow(
                fr.join(
                    fr.t(f"  {index:<5}", "dim"),
                    fr.t(f"{req.name:<17}", "hi"),
                    fr.t(f"{cell:<10}", "axis"),
                    fr.t(f"{per_cell:<8}", "fg"),
                    fr.t("~ POST", "warn") if req.write else fr.t("—", "dim"),
                )
            )
            for index, (req, cell) in enumerate(self._plan_cells(), start=1)
        )
        children.extend(
            [
                TextRow(fr.t("3 · CHECKS", "ph")),
                TextRow(
                    fr.join(
                        fr.t("⊨ assert.order ", "k.assert"),
                        fr.t(
                            "$status 2xx (error) · $.price>0 (error) · latency≤150ms (warn)", "dim"
                        ),
                    )
                ),
                TextRow(
                    fr.join(
                        fr.t("± diff.showcase ", "k.diff"),
                        fr.t(
                            "$.headers.* ignore · $.total ±0.5 · $.price exact · $.* exact", "dim"
                        ),
                    )
                ),
                TextRow(fr.t("gate: complete run · 0 errors allowed · exit 1 on drift", "dim")),
            ]
        )
        return children

    def _plan_right(self) -> list[Widget]:
        """Limits, writes, secrets, kept overrides, and the ready row."""
        scope = self._scope
        _, cells, writes, _, calls = scope.counts()
        blocked = bool(writes) and scope.writes != "approved"
        rate_extra = fr.t(" ← session (60)", "warn") if scope.rate != 60 else fr.t("")
        children: list[Widget] = [
            PanelHeader("Safety & limits"),
            TextRow(fr.t("4 · LIMITS & BUDGETS", "ph")),
            TextRow(
                fr.join(
                    fr.t("  rate               ", "dim"),
                    fr.t(f"{scope.rate}/min", "hi"),
                    rate_extra,
                )
            ),
            TextRow(
                fr.join(
                    fr.t("  timeout · retries  ", "dim"),
                    fr.t(f"{scope.timeout} · {scope.attempts} attempts", "hi"),
                )
            ),
            TextRow(
                fr.join(
                    fr.t("  concurrency        ", "dim"),
                    fr.t("4", "hi"),
                    fr.t(f" · capture 50 MB/call · fail-fast {scope.failfast}", "dim"),
                )
            ),
            TextRow(
                fr.join(
                    fr.t("  budgets            ", "dim"),
                    fr.t(f"{calls} calls ≤ 100 ", "hi"),
                    fr.t("✓", "same"),
                    fr.t(f" · {cells} cells ≤ 10 000 ", "hi"),
                    fr.t("✓", "same"),
                )
            ),
            TextRow(fr.t("5 · WRITES", "ph")),
        ]
        if not writes:
            children.append(TextRow(fr.t("none in scope", "dim")))
        elif blocked:
            children.append(
                ClickRow(
                    "approve",
                    fr.join(
                        fr.t(f"✗ {writes} write-capable blocked", "drift.b"),
                        fr.t(" — Checkout POST needs approval  ", "dim"),
                        fr.t("approve now (w)", "acc"),
                    ),
                )
            )
        else:
            children.append(
                TextRow(
                    fr.join(
                        fr.t(f"✓ {writes} write approved", "same.b"),
                        fr.t(" — Checkout POST, candidate is ", "dim"),
                        fr.pill("live", "pill.live"),
                    )
                )
            )
        children.extend(
            [
                TextRow(fr.t("6 · SECRETS EGRESS", "ph")),
                TextRow(
                    fr.join(
                        fr.t("  SHOWCASE_TOKEN  ", "drift"),
                        fr.t("→ canary.internal:443 only", "hi"),
                        fr.t("  · masked everywhere", "dim"),
                    )
                ),
                TextRow(
                    fr.join(
                        fr.t("  CHECKOUT_TOKEN  ", "drift"),
                        fr.t("→ canary.internal:443 only", "hi"),
                        fr.t("  · never persisted", "dim"),
                    )
                ),
                TextRow(fr.t("7 · OVERRIDES KEPT IN THE REPORT", "ph")),
            ]
        )
        touched = [
            (label, default, self._ovr_value(key))
            for key, label, default in OVERRIDES
            if self._ovr_value(key) != default
        ]
        if touched:
            children.extend(
                TextRow(
                    fr.join(
                        fr.t(f"  {label:<12}", "dim"),
                        fr.t(f"{default} → ", "dim"),
                        fr.t(value, "warn.b"),
                        fr.t("  session", "dim"),
                    )
                )
                for label, default, value in touched
            )
        else:
            children.append(TextRow(fr.t("  none — everything from project + profile", "dim")))
        children.append(TextRow(Text(" ")))
        if blocked:
            children.append(TextRow(fr.t("✗ x blocked by writes", "drift.b")))
        else:
            children.append(
                ClickRow(
                    "run",
                    fr.join(
                        fr.t("✓ ready", "same.b"),
                        fr.t(" — ", "dim"),
                        fr.t(f" x run {calls} calls ", "btn.primary"),
                    ),
                )
            )
        return children

    # ------------------------------------------------------------------- run

    def _make_run_specs(self) -> tuple[_RunSpec, ...]:
        """One spec per admitted cell — quote drifts, checkout lands slow."""
        out: list[_RunSpec] = []
        for rid, on in self._scope.on.items():
            if not on:
                continue
            req = fx.REQ_BY_ID[rid]
            if req.matrix:
                out.extend(
                    _RunSpec(req.name, cell, False, True, False)
                    for cell in fx.PLAN_CELLS
                    if self._scope.cells[cell]
                )
            else:
                out.append(_RunSpec(req.name, "", req.stream, False, rid == "checkout"))
        return tuple(out)

    def _start_run(self) -> None:
        """Enter the run stage and start the tick timer."""
        self._run_specs = self._make_run_specs()
        self._run_pos = 0
        self._run_done = False
        self._run_cancelled = False
        self._set_stage("run")
        self._run_timer = self.set_interval(self.RUN_TICK, self._advance_run)

    def _make_run(self) -> Vertical:
        """Build the watch-only run stage."""
        self._run_progress = SplitRow(Text(""), Text(""))
        self._run_row_widgets = [TextRow(Text("")) for _ in self._run_specs]
        self._run_legend = TextRow(Text(""))
        header = Text("    CELL", style=style("ph"))
        _pad_to(header, 30)
        header.append("BASELINE", style=style("ph"))
        _pad_to(header, 46)
        header.append("CANDIDATE", style=style("ph"))
        _pad_to(header, 62)
        header.append("STATE", style=style("ph"))
        self._update_run_view()
        return Vertical(
            Panel(self._run_progress, classes="-auto"),
            Panel(
                PanelHeader("Run — watch only, the plan fills in", "no interaction until it lands"),
                TextRow(header),
                *self._run_row_widgets,
                self._run_legend,
            ),
        )

    def _advance_run(self) -> None:
        """One tick: land one cell; when everything landed, schedule results."""
        if self._stage != "run" or self._run_cancelled or self._run_done:
            return
        self._run_pos += 1
        if self._run_pos >= len(self._run_specs):
            self._run_done = True
            if self._run_timer is not None:
                self._run_timer.stop()
                self._run_timer = None
            self._update_run_view()
            self.set_timer(self.RESULTS_DELAY, self._open_results)
            return
        self._update_run_view()

    def _cancel_run(self) -> None:
        """Esc during the run: stop the timer, mark the rest skipped."""
        self._run_cancelled = True
        if self._run_timer is not None:
            self._run_timer.stop()
            self._run_timer = None
        self._update_run_view()

    def _open_results(self) -> None:
        """Auto-advance from a fully landed run into results."""
        if self._stage == "run" and self._run_done and not self._run_cancelled:
            self._set_stage("results")

    def _run_mark(self, done: bool, current: bool) -> Text:
        """The ✓ / ◐ / ○ state mark."""
        if done:
            return fr.t("✓", "same")
        if current:
            return fr.t("◐", "warn")
        return fr.t("○", "dim")

    def _run_candidate_text(self, spec: _RunSpec, done: bool, current: bool) -> Text:
        """The candidate column of one run row."""
        if done:
            return fr.join(
                fr.t("200", "drift" if spec.slow else "same"),
                fr.t(" 280ms" if spec.slow else " 96ms", "dim"),
            )
        if current:
            return fr.t("…in flight", "dim")
        return fr.t("—", "dim")

    def _run_state_text(self, spec: _RunSpec, done: bool, current: bool) -> Text:
        """The state column of one run row."""
        if done:
            return fr.t("drift", "drift") if spec.drift else fr.t("same", "same")
        if current:
            return fr.t("running", "warn")
        if self._run_cancelled:
            return fr.t("skipped", "skip")
        return fr.t("queued", "dim")

    def _run_row_text(self, index: int) -> Text:
        """One plan-fill row at the current tick."""
        spec = self._run_specs[index]
        done = index < self._run_pos
        current = index == self._run_pos and not self._run_done and not self._run_cancelled
        out = Text("  ")
        out.append_text(self._run_mark(done, current))
        out.append(" ")
        out.append(spec.name, style=style("b"))
        if spec.cell:
            out.append(" · ", style=style("dim"))
            out.append(spec.cell, style=style("axis"))
        if spec.stream:
            out.append("  sse", style=style("badge.s"))
        _pad_to(out, 30)
        if done or current:
            out.append("200", style=style("same"))
            out.append(" 41ms", style=style("dim"))
        else:
            out.append("—", style=style("dim"))
        _pad_to(out, 46)
        out.append_text(self._run_candidate_text(spec, done, current))
        _pad_to(out, 62)
        out.append_text(self._run_state_text(spec, done, current))
        return out

    def _update_run_view(self) -> None:
        """Repaint progress, rows, and legend for the current tick."""
        total = len(self._run_specs)
        per = 2 if self._scope.cand else 1
        landed = self._run_specs[: self._run_pos]
        drift = sum(1 for spec in landed if spec.drift)
        same = len(landed) - drift
        pct = round(100 * self._run_pos / total) if total else 100
        self._run_progress.set_left(
            fr.join(
                fr.t("stable ", "dim"),
                fr.t("⇄", "axis"),
                fr.t(" canary  ", "dim"),
                fr.progress(pct, 40),
            )
        )
        right = fr.join(
            fr.t(f"{self._run_pos * per}/{total * per}", "b"),
            fr.t(" calls · ", "dim"),
            fr.t(f"{same} same", "same"),
            fr.t(" · ", "dim"),
            fr.t(f"{drift} drift", "drift"),
        )
        if self._run_cancelled:
            right.append_text(fr.t(" · cancelled", "warn"))
        self._run_progress.set_right(right)
        for index, widget in enumerate(self._run_row_widgets):
            widget.update(self._run_row_text(index))
            current = index == self._run_pos and not self._run_done and not self._run_cancelled
            widget.set_class(current, "row-cursor")
        legend = fr.join(
            fr.t("✓", "same"),
            fr.t(" materialised · ", "dim"),
            fr.t("◐", "warn"),
            fr.t(" in flight · ", "dim"),
            fr.t("○ queued", "dim"),
        )
        if self._run_done:
            legend.append_text(fr.t(" · opening results…", "acc.b"))
        self._run_legend.update(legend)

    # --------------------------------------------------------------- results

    def _make_results(self) -> Widget:
        """Mount the analyze view — imported lazily so module order never matters."""
        try:
            module = importlib.import_module("comparo.tui3.screens.analyze")
        except ImportError:
            view: Any = _AnalyzePending()
        else:
            view = module.AnalyzeView(replay=False)
        self._analyze = view
        return cast("Widget", view)

    # ---------------------------------------------------------------- events

    def on_cursor_list_toggled(self, event: CursorList.Toggled) -> None:
        """Catalog toggles write straight into ScopeState."""
        if self._stage != "scope" or event.source is not self._catalog:
            return
        key = event.key
        if key.startswith("req:"):
            rid = key.removeprefix("req:")
            self._scope.on[rid] = event.selected
            if fx.REQ_BY_ID[rid].matrix:
                self._rebuild_catalog()
        elif key.startswith("cell:"):
            self._scope.cells[key.removeprefix("cell:")] = event.selected
        else:
            return
        self._sync_scope_panel()

    def on_cursor_list_activated(self, event: CursorList.Activated) -> None:
        """The profile legend row prefills the whole scope."""
        if self._stage != "scope" or event.source is not self._catalog:
            return
        if event.key != "profile:release":
            return
        quote_was_on = self._scope.on["quote"]
        for rid in self._scope.on:
            self._scope.on[rid] = rid in PROFILE_SCOPE
        for cell in self._scope.cells:
            self._scope.cells[cell] = True
        self._scope.cand = True
        if quote_was_on:
            self._catalog.set_selected(self._selected_catalog_keys())
        else:
            self._rebuild_catalog()
        self.notify("▶ Release gate prefilled the scope — still editable")
        self._sync_scope_panel()
        self._refresh_chrome_guarded()

    def on_chip_row_pressed(self, event: ChipRow.Pressed) -> None:
        """Tag chips filter the catalog; override chips cycle their value."""
        if self._stage != "scope":
            return
        if event.source is self._tag_chips:
            self._scope.tag = event.key
            self._tag_chips.set_chips(self._tag_chip_labels())
            self._rebuild_catalog()
            return
        if event.source is self._ovr_chips or event.source is self._ovr_chips_b:
            self._cycle_override(event.key)
            self._sync_scope_panel()

    def on_filter_input_filter_changed(self, event: FilterInput.FilterChanged) -> None:
        """Filter the catalog live."""
        if self._stage != "scope" or event.source is not self._filter:
            return
        self._scope.query = event.query
        self._catalog.set_filter(event.query)

    def on_click_row_pressed(self, event: ClickRow.Pressed) -> None:
        """Clickable rows: candidate toggle, plan, approve, run."""
        key = event.key
        if key == "cand":
            self._scope.cand = not self._scope.cand
            self._sync_scope_panel()
            self._refresh_chrome_guarded()
        elif key == "plan":
            self._set_stage("plan")
        elif key == "approve":
            self._scope.writes = "approved"
            self._set_stage("plan")
        elif key == "run":
            self._try_run()

    # ------------------------------------------------------------ app wiring

    @override
    def crumb(self) -> str:
        """Navrow crumb per stage."""
        if self._stage == "scope":
            return "launch · scope"
        if self._stage == "plan":
            return "launch · plan"
        if self._stage == "run":
            return "launch · running"
        suffix = str(self._analyze.crumb_suffix()).strip() if self._analyze is not None else ""
        return f"launch · {suffix}" if suffix else "launch · results · 8c3e11"

    @override
    def tier(self) -> str:
        """DIFF when a candidate is on, RUN otherwise."""
        return "DIFF" if self._scope.cand else "RUN"

    @override
    def hints(self) -> list[tuple[str, str]]:
        """Footer keys per stage, mirroring the mockup footers."""
        if self._stage == "scope":
            return [
                ("space", "select"),
                ("click", "cells · chips"),
                ("c", "candidate"),
                ("w", "writes"),
                ("p", "plan"),
                ("x", "run"),
                ("q", "quit"),
            ]
        if self._stage == "plan":
            return [("x", "run"), ("esc", "edit scope"), ("q", "quit")]
        if self._stage == "run":
            return [("esc", "cancel"), ("q", "quit")]
        hints: list[tuple[str, str]] = []
        if self._analyze is not None:
            hints = cast("list[tuple[str, str]]", list(self._analyze.hints()))
        for extra in (("esc", "back"), ("q", "quit")):
            if all(key != extra[0] for key, _ in hints):
                hints.append(extra)
        return hints

    @override
    def focus_default(self) -> None:
        """Focus the natural widget of the current stage."""
        if self._stage == "scope":
            self._catalog.focus()
        elif self._stage == "results" and self._analyze is not None:
            self._analyze.focus_default()

    @override
    def on_ws_key(self, key: str) -> bool:
        """Route stage keys exactly like the mockup keydown handler."""
        if self._stage == "scope":
            return self._scope_key(key)
        if self._stage == "plan":
            return self._plan_key(key)
        if self._stage == "run":
            return self._run_key(key)
        return self._results_key(key)

    def _scope_key(self, key: str) -> bool:
        """Scope-stage keys."""
        if key == "c":
            self._scope.cand = not self._scope.cand
            self._sync_scope_panel()
            return True
        if key == "w":
            self._scope.writes = "ask" if self._scope.writes == "approved" else "approved"
            self._sync_scope_panel()
            return True
        if key == "p":
            self._set_stage("plan")
            return True
        if key == "x":
            self._try_run()
            return True
        if key == "slash":
            self._filter.focus()
            return True
        return False

    def _plan_key(self, key: str) -> bool:
        """Plan-stage keys."""
        if key == "x":
            self._try_run()
            return True
        if key == "escape":
            self._set_stage("scope")
            return True
        return False

    def _run_key(self, key: str) -> bool:
        """Run-stage keys — esc cancels; esc again leaves."""
        if key != "escape":
            return False
        if self._run_cancelled or self._run_done:
            self._set_stage("scope")
        else:
            self._cancel_run()
        return True

    def _results_key(self, key: str) -> bool:
        """Results-stage keys — esc back, the rest goes to the analyze view."""
        if key == "escape":
            self._set_stage("scope")
            return True
        if key in RESULTS_KEYS and self._analyze is not None:
            return bool(self._analyze.route_key(key))
        return False
