"""Thin Compare composition for every execution mode and lifecycle state."""

from __future__ import annotations

from enum import StrEnum
from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical

from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.results import ResultProjection
from comparo.tui.components import CompareDraftForm
from comparo.tui.components import DiagnosticsList
from comparo.tui.components import ExecutionProgress
from comparo.tui.components import PlanPreview
from comparo.tui.components import ResultsWorkspaceView
from comparo.tui.components import StatePanel
from comparo.tui.components import StatePanelMode
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareMode
from comparo.tui.contracts import ResultAccess
from comparo.tui.contracts import TuiInteractionLimits
from comparo.tui.contracts import tui_interaction_limits_defaults_v1


class CompareWorkspaceState(StrEnum):
    """The three stable lifecycle surfaces contained in Compare."""

    PREPARE = "prepare"
    RUNNING = "running"
    RESULTS = "results"


@final
class CompareWorkspace(Vertical):
    """Compose assertions, differences, and ExecutionProfile runs in one workspace."""

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
        """Initialize the selected Compare mode and lifecycle surface."""
        super().__init__(id=id, classes=classes)
        if type(mode) is not CompareMode:
            raise TypeError("Compare workspace mode must be typed")
        if type(state) is not CompareWorkspaceState:
            raise TypeError("Compare workspace state must be typed")
        if draft is not None and type(draft) is not CompareDraft:
            raise TypeError("Compare workspace draft must be typed")
        selected_limits = (
            tui_interaction_limits_defaults_v1()
            if interaction_limits is None
            else interaction_limits
        )
        if type(selected_limits) is not TuiInteractionLimits:
            raise TypeError("Compare workspace interaction limits must be typed")
        self.draft = CompareDraft(mode=mode) if draft is None else draft
        self.resources = resources
        self.state = state
        self.plan = plan
        self.progress = progress
        self.results = results
        self.diagnostics = diagnostics
        self.interaction_limits = selected_limits

    @override
    def compose(self) -> ComposeResult:
        """Compose the active lifecycle surface from shared widgets only."""
        if self.state is CompareWorkspaceState.PREPARE:
            yield self._prepare_panel()
            return
        if self.state is CompareWorkspaceState.RUNNING:
            if self.progress is None:
                yield StatePanel(
                    mode=StatePanelMode.LOADING,
                    message="Starting the admitted execution…",
                )
            else:
                yield StatePanel(
                    ExecutionProgress(self.progress),
                    mode=StatePanelMode.CONTENT,
                )
            return
        if self.results is None:
            yield StatePanel(
                mode=StatePanelMode.ERROR,
                message="The execution did not produce a safe result projection.",
            )
            return
        yield StatePanel(
            ResultsWorkspaceView(self.results, access=ResultAccess.LIVE),
            mode=StatePanelMode.CONTENT,
        )

    def _prepare_panel(self) -> StatePanel:
        children = [
            CompareDraftForm(
                self.draft,
                limits=self.interaction_limits,
                resources=self.resources,
            ),
            PlanPreview(self.plan),
        ]
        children.extend((DiagnosticsList(self.diagnostics),) if self.diagnostics else ())
        mode = StatePanelMode.ERROR if self.diagnostics else StatePanelMode.CONTENT
        message = "The comparison could not be prepared." if self.diagnostics else ""
        return StatePanel(*children, mode=mode, message=message)

    def current_draft(self) -> CompareDraft:
        """Return the typed shared form draft without interpreting it."""
        if self.is_mounted:
            forms = self.query(CompareDraftForm)
            if len(forms):
                self.draft = forms.first().current_draft()
        return self.draft

    def show_prepare(
        self,
        draft: CompareDraft,
        *,
        plan: PlanInspection | None = None,
        diagnostics: tuple[ApplicationDiagnostic, ...] = (),
        resources: tuple[ProjectResourceSummary, ...] | None = None,
    ) -> None:
        """Keep Compare mounted while returning to its shared preparation form."""
        if type(draft) is not CompareDraft:
            raise TypeError("Compare workspace draft must be typed")
        resources_changed = resources is not None and resources != self.resources
        was_preparing = self.state is CompareWorkspaceState.PREPARE
        self.state = CompareWorkspaceState.PREPARE
        self.draft = draft
        self.plan = plan
        self.progress = None
        self.results = None
        self.diagnostics = diagnostics
        if resources is not None:
            self.resources = resources
        if not self.is_mounted:
            return
        if was_preparing and not resources_changed and not diagnostics:
            forms = self.query(CompareDraftForm)
            if len(forms):
                form = forms.first()
                if form.current_draft() != draft:
                    form.show_draft(draft)
                self.query_one(PlanPreview).show_plan(plan)
                for diagnostic_list in self.query(DiagnosticsList):
                    diagnostic_list.display = False
                self.query_one(StatePanel).show_state(StatePanelMode.CONTENT)
                return
        self.refresh(recompose=True)

    def show_running(self, snapshot: ExecutionProgressSnapshot) -> None:
        """Keep Compare mounted and update progress in place when already running."""
        if type(snapshot) is not ExecutionProgressSnapshot:
            raise TypeError("Compare running state requires ExecutionProgressSnapshot")
        self.progress = snapshot
        self.results = None
        self.diagnostics = ()
        if self.is_mounted and self.state is CompareWorkspaceState.RUNNING:
            progress = self.query(ExecutionProgress)
            if len(progress):
                progress.first().show_snapshot(snapshot)
                return
        self.state = CompareWorkspaceState.RUNNING
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_starting(self) -> None:
        """Replace preparation controls before a session emits its first snapshot."""
        if self.state is CompareWorkspaceState.RUNNING and self.progress is None:
            return
        self.state = CompareWorkspaceState.RUNNING
        self.progress = None
        self.results = None
        self.diagnostics = ()
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_results(self, results: ResultProjection) -> None:
        """Keep Compare mounted while switching to the shared live results component."""
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
