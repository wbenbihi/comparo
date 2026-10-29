"""Launch workspace — scope → plan → run → analyse, one lifecycle."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.containers import Vertical

from comparo.tuibis import fixtures_analyse
from comparo.tuibis import fixtures_launch
from comparo.tuibis.viewmodels import KeyHint
from comparo.tuibis.viewmodels import StageVM
from comparo.tuibis.widgets.analyse_view import AnalyseView
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.stage_view import RunStageView
from comparo.tuibis.widgets.stage_view import ScopeStageView
from comparo.tuibis.widgets.stage_view import StageView
from comparo.tuibis.widgets.strips import StageBreadcrumb

_ORDER = ("scope", "plan", "run", "analyse")

ANALYSE_HINTS: tuple[KeyHint, ...] = (
    KeyHint("i", "next index"),
    KeyHint("z", "deep dive"),
    KeyHint("d", "sent diff"),
    KeyHint("esc", "back"),
)


class LaunchWorkspace(Vertical):
    """Composition only: breadcrumb + the active stage view."""

    BINDINGS: ClassVar = [
        Binding("a", "mark_all_or_cancel", "All / cancel", show=False),
        Binding("n", "mark_none", "None", show=False),
        Binding("c", "toggle_candidate", "Candidate", show=False),
        Binding("f", "fails_only", "Fails only", show=False),
        Binding("slash", "focus_filter", "Filter", show=False),
    ]

    def __init__(self) -> None:
        """Build all stage views up front; scope starts active."""
        self._stage = "scope"
        self._analyse = AnalyseView(fixtures_analyse.analyse())
        self._scope = ScopeStageView(
            items=fixtures_launch.scope_items(fixtures_launch.INITIAL_SELECTED),
            profiles={
                key: fixtures_launch.scope_items(keys)
                for key, keys in fixtures_launch.PROFILES.items()
            },
            chips=fixtures_launch.SCOPE_CHIPS,
            topology=fixtures_launch.topology_lines,
            math=fixtures_launch.scope_math_lines,
            side_panels=(fixtures_launch.variants_panel(), fixtures_launch.overrides_panel()),
        )
        self._run = RunStageView(
            header=fixtures_launch.run_header_lines,
            boards={
                "all": fixtures_launch.run_call_items(),
                "fails": fixtures_launch.run_fail_items(),
                "cancel": fixtures_launch.run_cancelling_items(),
            },
            board_title=fixtures_launch.BOARD_TITLE,
            details=fixtures_launch.call_detail_lines,
            events=fixtures_launch.events_panel(),
        )
        stages: dict[str, Vertical] = {
            "scope": self._scope,
            "plan": StageView(fixtures_launch.STAGES["plan"]),
            "run": self._run,
            "analyse": self._analyse,
        }
        self._host = InspectorHost(stages, initial="scope")
        self._breadcrumb = StageBreadcrumb(self._stage_vms())
        super().__init__(self._breadcrumb, self._host, id="launch-workspace")

    def _stage_vms(self) -> tuple[StageVM, ...]:
        current = _ORDER.index(self._stage)
        states = []
        for position, name in enumerate(_ORDER):
            if position < current:
                states.append(StageVM(name, "done"))
            elif position == current:
                states.append(StageVM(name, "run" if name == "run" else "active"))
            else:
                states.append(StageVM(name, "todo"))
        return tuple(states)

    def _go(self, stage: str) -> None:
        self._stage = stage
        self._host.show(stage)
        self._breadcrumb.remove()
        self._breadcrumb = StageBreadcrumb(self._stage_vms())
        self.mount(self._breadcrumb, before=self._host)
        self._focus_stage()

    def _focus_stage(self) -> None:
        if self._stage == "scope":
            self._scope.focus_default()
        elif self._stage == "run":
            self._run.focus_default()

    def on_show(self) -> None:
        """Focus the active stage's interactive surface when the workspace appears."""
        host = getattr(self.app, "_host", None)
        if host is not None and host.current == "LAUNCH":
            self._focus_stage()

    @property
    def hints(self) -> tuple[KeyHint, ...]:
        """Contextual footer hints for the active stage."""
        if self._stage == "analyse":
            return ANALYSE_HINTS
        return fixtures_launch.STAGES[self._stage].hints

    def handle_command(self, command: str) -> bool:
        """Route workspace keys: stage flow, index cycling, deep dives."""
        position = _ORDER.index(self._stage)
        if command == "advance" and position < len(_ORDER) - 1:
            self._go(_ORDER[position + 1])
            return True
        if command == "back":
            if self._stage == "analyse" and self._analyse.close_dive():
                return True
            if position > 0:
                self._go(_ORDER[position - 1])
                return True
            return False
        if command == "admit" and self._stage == "scope":
            if self._scope.admit_allowed:
                self._go("plan")
            return True
        if command == "execute" and self._stage == "plan":
            self._go("run")
            return True
        if self._stage == "analyse":
            return self._handle_analyse(command)
        return False

    def _handle_analyse(self, command: str) -> bool:
        if command == "index":
            self._analyse.cycle_index()
            return True
        if command == "dive-response":
            return self._analyse.dive("response")
        if command == "dive-sent":
            return self._analyse.dive("sent")
        return False

    def action_mark_all_or_cancel(self) -> None:
        """`a` — select all requests in scope; toggle cancelling in run."""
        if self._stage == "scope":
            self._scope.set_all(True)
        elif self._stage == "run":
            self._run.toggle_cancel()

    def action_mark_none(self) -> None:
        """`n` — clear the request selection in scope."""
        if self._stage == "scope":
            self._scope.set_all(False)

    def action_toggle_candidate(self) -> None:
        """`c` — toggle the candidate environment in scope."""
        if self._stage == "scope":
            self._scope.toggle_candidate()

    def action_fails_only(self) -> None:
        """`f` — swap the run board between all calls and fails only."""
        if self._stage == "run":
            self._run.toggle_fails()

    def action_focus_filter(self) -> None:
        """`/` — focus the scope filter input."""
        if self._stage == "scope":
            self._scope.focus_filter()
