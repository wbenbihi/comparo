"""Native immutable Plan checkpoint and live Run monitoring surfaces."""

from __future__ import annotations

from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Button
from textual.widgets import DataTable
from textual.widgets import Label
from textual.widgets import ProgressBar
from textual.widgets import Static

from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.inspection import PlanInspection
from comparo.tui.components.status import StatusBadge
from comparo.tui.components.status import status_for_outcome


@final
class ComparePlanPanel(Vertical):
    """Immutable admitted-plan checkpoint before network execution."""

    @final
    class EditRequested(Message):
        """Return to Target without starting HTTP work."""

    @final
    class ExecuteRequested(Message):
        """Start the exact admitted plan."""

    def __init__(
        self,
        plan: PlanInspection,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Retain one immutable, secret-free plan inspection."""
        if type(plan) is not PlanInspection:
            raise TypeError("Compare plan surface requires PlanInspection")
        super().__init__(id=id, classes=classes)
        self._plan = plan

    @override
    def compose(self) -> ComposeResult:
        """Expose work expansion, topology, budgets, provenance, and actions."""
        plan = self._plan
        cardinality = plan.cardinality
        with Horizontal(classes="compare-plan-summary"):
            yield Static(f"{cardinality.cells}\nCELLS", classes="compare-plan-metric")
            yield Static(
                f"{cardinality.environment_executions}\nEXECUTIONS",
                classes="compare-plan-metric",
            )
            yield Static(
                f"{cardinality.comparisons}\nCOMPARISONS",
                classes="compare-plan-metric",
            )
            yield Static(
                f"{cardinality.maximum_attempts}\nMAX ATTEMPTS",
                classes="compare-plan-metric",
            )
        with Horizontal(classes="compare-plan-layout"):
            yield _PlanWorkTable(plan, classes="compare-plan-work")
            with Vertical(classes="compare-plan-authority"):
                yield Label("AUTHORITY & LIMITS", classes="compare-panel-kicker")
                yield Static(_authority_text(plan), markup=False)
                yield Label("OVERRIDE PROVENANCE", classes="compare-panel-kicker")
                yield Static(_plan_provenance(plan), markup=False)
        with Horizontal(classes="compare-plan-actions"):
            yield Button("Back to target", id="compare-plan-edit")
            yield Button("Execute plan", id="compare-plan-execute", variant="success")

    @on(Button.Pressed, "#compare-plan-edit")
    def edit(self) -> None:
        """Request local return to Target."""
        self.post_message(self.EditRequested())

    @on(Button.Pressed, "#compare-plan-execute")
    def execute(self) -> None:
        """Route pointer activation through the semantic Execute command."""
        self.post_message(self.ExecuteRequested())


@final
class _PlanWorkTable(DataTable[str]):
    """Virtualized table for admitted subject/environment work."""

    def __init__(self, plan: PlanInspection, *, classes: str | None = None) -> None:
        """Retain one compact plan inspection."""
        super().__init__(classes=classes, zebra_stripes=True, cursor_type="row")
        self._plan = plan

    @override
    def on_mount(self) -> None:
        """Populate one row per compact plan slice/environment topology."""
        self.add_columns("SUBJECT", "ENVIRONMENTS", "MATRICES", "CELLS", "TRIALS")
        for item in self._plan.slices:
            environments = " → ".join(value.resource_id for value in item.environments)
            matrices = ", ".join(
                f"{matrix.binding}[{len(matrix.cases)}]" for matrix in item.matrices
            )
            self.add_row(
                item.subject.resource_id,
                environments or "—",
                matrices or "—",
                str(item.cardinality.cells),
                str(item.trials),
            )


@final
class _RunScopeTable(DataTable[str]):
    """Planned work slices beside truthful aggregate runtime progress."""

    def __init__(
        self,
        plan: PlanInspection,
        snapshot: ExecutionProgressSnapshot,
        *,
        classes: str | None = None,
    ) -> None:
        super().__init__(classes=classes, zebra_stripes=True, cursor_type="row")
        self._plan = plan
        self._snapshot = snapshot

    @override
    def on_mount(self) -> None:
        """Populate admitted slices without inventing per-occurrence runtime state."""
        self.add_columns("SUBJECT", "TOPOLOGY", "CELLS", "TRIALS", "RUN STATE")
        progress = f"{self._snapshot.finished_executions}/{self._snapshot.total_executions} exec"
        for item in self._plan.slices:
            self.add_row(
                item.subject.resource_id,
                " → ".join(value.resource_id for value in item.environments),
                str(item.cardinality.cells),
                str(item.trials),
                progress,
            )


@final
class CompareRunPanel(Vertical):
    """Live coalesced execution monitor that never retains event history."""

    @final
    class CancelRequested(Message):
        """The running comparison should receive user cancellation."""

    def __init__(
        self,
        snapshot: ExecutionProgressSnapshot,
        plan: PlanInspection | None = None,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize from one bounded application-owned progress snapshot."""
        if type(snapshot) is not ExecutionProgressSnapshot:
            raise TypeError("Compare Run requires ExecutionProgressSnapshot")
        if plan is not None and type(plan) is not PlanInspection:
            raise TypeError("Compare Run plan must be PlanInspection or absent")
        super().__init__(id=id, classes=classes)
        self._snapshot = snapshot
        self._plan = plan

    @override
    def compose(self) -> ComposeResult:
        """Compose progress, counters, active occurrence, and cancellation affordance."""
        snapshot = self._snapshot
        progress = ProgressBar(
            total=max(1, snapshot.total_executions + snapshot.total_comparisons),
            show_eta=False,
            id="compare-run-progress",
        )
        progress.update(progress=snapshot.finished_executions + snapshot.finished_comparisons)
        yield progress
        with Horizontal(classes="compare-run-metrics"):
            yield _run_metric("QUEUED", snapshot.queued_executions)
            yield _run_metric("ACTIVE", snapshot.active_executions)
            yield _run_metric("FINISHED", snapshot.finished_executions)
            yield _run_metric("DIFFS", snapshot.finished_comparisons)
        with Horizontal(classes="compare-run-layout"):
            with Vertical(classes="compare-run-activity"):
                yield Label("EXECUTION SCOPE", classes="compare-panel-kicker")
                if self._plan is not None:
                    yield _RunScopeTable(
                        self._plan,
                        snapshot,
                        classes="compare-run-scope-table",
                    )
                yield Label("LATEST ACTIVITY", classes="compare-panel-kicker")
                yield StatusBadge(
                    status_for_outcome(snapshot.phase.value), detail=snapshot.phase.value
                )
                yield Static(_run_activity(snapshot), markup=False)
            with Vertical(classes="compare-run-detail"):
                yield Label("SELECTED OCCURRENCE", classes="compare-panel-kicker")
                yield Static(_run_occurrence(snapshot), markup=False)
                yield Button("Cancel run", id="compare-run-cancel", variant="error")

    def show_snapshot(self, snapshot: ExecutionProgressSnapshot) -> None:
        """Replace coalesced counters in place without retaining prior events."""
        if type(snapshot) is not ExecutionProgressSnapshot:
            raise TypeError("Compare Run requires ExecutionProgressSnapshot")
        self._snapshot = snapshot
        if self.is_mounted:
            self.refresh(recompose=True)

    @on(Button.Pressed, "#compare-run-cancel")
    def cancel(self) -> None:
        """Route pointer cancellation through the semantic command dispatcher."""
        self.post_message(self.CancelRequested())


def _authority_text(plan: PlanInspection) -> str:
    budgets = (
        "\n".join(
            f"  {item.resource:<18} {item.effective} / cap {item.cap}" for item in plan.budgets
        )
        or "  No narrowed budgets"
    )
    return (
        f"Policy             {plan.policy.resource_id}\n"
        f"Ordering           {plan.ordering}\n"
        f"Plan fingerprint   {plan.fingerprint[:18]}…\n"
        f"Run                {plan.run_id[:18]}…\n\n"
        f"Budgets\n{budgets}"
    )


def _plan_provenance(plan: PlanInspection) -> str:
    return (
        "\n".join(
            f"{item.field:<24} {' → '.join(source.kind for source in item.sources)}"
            for item in plan.provenance
        )
        or "No invocation-level overrides changed versioned defaults."
    )


def _run_metric(label: str, value: int) -> Static:
    return Static(f"{value}\n{label}", classes="compare-run-metric", markup=False)


def _run_activity(snapshot: ExecutionProgressSnapshot) -> str:
    return (
        f"Run                 {snapshot.run_id}\n"
        f"Runtime sequence    {snapshot.runtime_sequence or 0}\n"
        f"Executions          {snapshot.finished_executions} / {snapshot.total_executions}\n"
        f"Comparisons         {snapshot.finished_comparisons} / {snapshot.total_comparisons}"
    )


def _run_occurrence(snapshot: ExecutionProgressSnapshot) -> str:
    return (
        f"Latest execution\n  {snapshot.last_execution_id or 'Waiting for work…'}\n\n"
        f"Latest comparison\n  {snapshot.last_comparison_id or 'Waiting for comparison…'}"
    )


__all__ = ("ComparePlanPanel", "CompareRunPanel")
