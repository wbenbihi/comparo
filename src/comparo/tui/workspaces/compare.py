"""Compare's Target → Plan → Run → Analyze workspace composition."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar
from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label
from textual.widgets import Static

from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.results import ResultProjection
from comparo.tui.bindings import textual_bindings
from comparo.tui.commands import CommandScope
from comparo.tui.components import CompareLifecycleBar
from comparo.tui.components import ComparePlanPanel
from comparo.tui.components import CompareRunPanel
from comparo.tui.components import CompareTargetPanel
from comparo.tui.components import DiagnosticsList
from comparo.tui.components import ResultsWorkspaceView
from comparo.tui.components import StatePanel
from comparo.tui.components import StatePanelMode
from comparo.tui.components import seed_compare_draft
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareMode
from comparo.tui.contracts import ResultAccess
from comparo.tui.contracts import TuiInteractionLimits
from comparo.tui.contracts import tui_interaction_limits_defaults_v1


class CompareWorkspaceState(StrEnum):
    """The three controller-owned lifecycle surfaces contained in Compare."""

    PREPARE = "prepare"
    RUNNING = "running"
    RESULTS = "results"


@final
class CompareWorkspace(Vertical):
    """Keep every execution mode and lifecycle phase in one persistent workspace."""

    BINDINGS: ClassVar[list[BindingType]] = textual_bindings(CommandScope.COMPARE)

    @final
    class DraftChanged(Message):
        """The complete target draft changed."""

        def __init__(self, draft: CompareDraft) -> None:
            """Retain the typed draft."""
            super().__init__()
            self.draft = draft

    @final
    class PlanRequested(Message):
        """The current target should be prepared."""

        def __init__(self, draft: CompareDraft) -> None:
            """Retain the exact submitted draft."""
            super().__init__()
            self.draft = draft

    @final
    class ExecuteRequested(Message):
        """The admitted plan should start."""

    @final
    class CancelRequested(Message):
        """The running execution should be cancelled."""

    @final
    class SurfaceChanged(Message):
        """The local Target/Plan presentation changed without a backend effect."""

    def __init__(
        self,
        *,
        mode: CompareMode = CompareMode.DIFFERENCES,
        draft: CompareDraft | None = None,
        resources: tuple[ProjectResourceSummary, ...] = (),
        state: CompareWorkspaceState = CompareWorkspaceState.PREPARE,
        plan: PlanInspection | None = None,
        progress: ExecutionProgressSnapshot | None = None,
        results: ResultProjection | None = None,
        diagnostics: tuple[ApplicationDiagnostic, ...] = (),
        interaction_limits: TuiInteractionLimits | None = None,
        id: str | None = None,
        classes: str | None = "workspace",
    ) -> None:
        """Initialize the controller-owned Compare state and local Plan orientation."""
        super().__init__(id=id, classes=classes)
        self.can_focus = True
        if type(mode) is not CompareMode:
            raise TypeError("Compare workspace mode must be typed")
        if type(state) is not CompareWorkspaceState:
            raise TypeError("Compare workspace state must be typed")
        if draft is not None and type(draft) is not CompareDraft:
            raise TypeError("Compare workspace draft must be typed")
        limits = (
            tui_interaction_limits_defaults_v1()
            if interaction_limits is None
            else interaction_limits
        )
        if type(limits) is not TuiInteractionLimits:
            raise TypeError("Compare workspace interaction limits must be typed")
        authored_draft = CompareDraft(mode=mode) if draft is None else draft
        self.draft = seed_compare_draft(authored_draft, resources)
        self.resources = resources
        self.state = state
        self.plan = plan
        self.progress = progress
        self.results = results
        self.diagnostics = diagnostics
        self.interaction_limits = limits
        self.reviewing_plan = plan is not None

    @override
    def compose(self) -> ComposeResult:
        """Compose one screenshot-led lifecycle surface under shared orientation."""
        stage = self._stage()
        with Vertical(classes="compare-page"):
            with (
                Horizontal(classes="workspace-heading"),
                Vertical(classes="workspace-heading-copy"),
            ):
                yield Label("Compare", classes="workspace-title")
                yield Static(
                    "Define targets, review the admitted plan, run, and analyze outcomes.",
                    classes="workspace-subtitle",
                    markup=False,
                )
            yield CompareLifecycleBar(stage)
            yield self._surface(stage)

    def _stage(self) -> str:
        if self.state is CompareWorkspaceState.RUNNING:
            return "Run"
        if self.state is CompareWorkspaceState.RESULTS:
            return "Analyze"
        if self.plan is not None and self.reviewing_plan:
            return "Plan"
        return "Target"

    @property
    def lifecycle_stage(self) -> str:
        """Return the product-facing lifecycle stage currently presented."""
        return self._stage()

    def _surface(self, stage: str) -> StatePanel:
        if stage == "Target":
            children: list[Widget] = [CompareTargetPanel(self.draft, self.resources)]
            children.extend((DiagnosticsList(self.diagnostics),) if self.diagnostics else ())
            return StatePanel(
                *children,
                mode=StatePanelMode.ERROR if self.diagnostics else StatePanelMode.CONTENT,
                message="The target could not be compiled." if self.diagnostics else "",
                classes="compare-surface",
            )
        if stage == "Plan":
            if self.plan is None:  # pragma: no cover - closed stage invariant
                return StatePanel(mode=StatePanelMode.ERROR, message="Plan is unavailable.")
            return StatePanel(
                ComparePlanPanel(self.plan),
                mode=StatePanelMode.CONTENT,
                classes="compare-surface",
            )
        if stage == "Run":
            if self.progress is None:
                return StatePanel(
                    mode=StatePanelMode.LOADING,
                    message="Starting the admitted execution…",
                    classes="compare-surface",
                )
            return StatePanel(
                CompareRunPanel(self.progress, self.plan),
                mode=StatePanelMode.CONTENT,
                classes="compare-surface",
            )
        if self.results is None:
            return StatePanel(
                mode=StatePanelMode.ERROR,
                message="The execution did not produce a safe result projection.",
                classes="compare-surface",
            )
        return StatePanel(
            ResultsWorkspaceView(self.results, access=ResultAccess.LIVE),
            mode=StatePanelMode.CONTENT,
            classes="compare-surface",
        )

    def current_draft(self) -> CompareDraft:
        """Return the current typed Target selection."""
        if self.is_mounted:
            targets = self.query(CompareTargetPanel)
            if len(targets):
                self.draft = targets.first().current_draft()
        return self.draft

    @on(CompareTargetPanel.DraftChanged)
    def target_changed(self, message: CompareTargetPanel.DraftChanged) -> None:
        """Forward native selector updates through a workspace-level contract."""
        self.draft = message.draft
        self.post_message(self.DraftChanged(message.draft))

    @on(CompareTargetPanel.Submitted)
    def target_submitted(self, message: CompareTargetPanel.Submitted) -> None:
        """Forward the exact Target for application preparation."""
        self.draft = message.draft
        self.post_message(self.PlanRequested(message.draft))

    @on(ComparePlanPanel.EditRequested)
    def edit_target(self) -> None:
        """Return locally to Target while retaining the admitted plan until edited."""
        self.reviewing_plan = False
        self.refresh(recompose=True)
        self.post_message(self.SurfaceChanged())

    @on(ComparePlanPanel.ExecuteRequested)
    def execute_plan(self) -> None:
        """Forward pointer execution through the application command path."""
        self.post_message(self.ExecuteRequested())

    @on(CompareRunPanel.CancelRequested)
    def cancel_run(self) -> None:
        """Forward pointer cancellation through the application command path."""
        self.post_message(self.CancelRequested())

    def show_prepare(
        self,
        draft: CompareDraft,
        *,
        plan: PlanInspection | None = None,
        diagnostics: tuple[ApplicationDiagnostic, ...] = (),
        resources: tuple[ProjectResourceSummary, ...] | None = None,
    ) -> None:
        """Return to Target or advance to a newly admitted Plan."""
        if type(draft) is not CompareDraft:
            raise TypeError("Compare workspace draft must be typed")
        self.state = CompareWorkspaceState.PREPARE
        self.draft = draft
        self.plan = plan
        self.progress = None
        self.results = None
        self.diagnostics = diagnostics
        if resources is not None:
            self.resources = resources
        self.reviewing_plan = plan is not None
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_running(self, snapshot: ExecutionProgressSnapshot) -> None:
        """Keep Run mounted and update its coalesced progress in place."""
        if type(snapshot) is not ExecutionProgressSnapshot:
            raise TypeError("Compare running state requires ExecutionProgressSnapshot")
        self.progress = snapshot
        self.results = None
        self.diagnostics = ()
        if self.is_mounted and self.state is CompareWorkspaceState.RUNNING:
            panels = self.query(CompareRunPanel)
            if len(panels):
                panels.first().show_snapshot(snapshot)
                return
        self.state = CompareWorkspaceState.RUNNING
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_starting(self) -> None:
        """Replace Plan before the execution emits its first progress snapshot."""
        if self.state is CompareWorkspaceState.RUNNING and self.progress is None:
            return
        self.state = CompareWorkspaceState.RUNNING
        self.progress = None
        self.results = None
        self.diagnostics = ()
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_results(self, results: ResultProjection) -> None:
        """Advance to shared Analyze after a terminal sanitized projection."""
        if type(results) is not ResultProjection:
            raise TypeError("Compare results require ResultProjection")
        if self.state is CompareWorkspaceState.RESULTS and self.results is results:
            return
        self.state = CompareWorkspaceState.RESULTS
        self.progress = None
        self.results = results
        self.diagnostics = ()
        if self.is_mounted:
            self.refresh(recompose=True)


__all__ = ("CompareWorkspace", "CompareWorkspaceState")
