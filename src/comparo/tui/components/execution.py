"""Shared plan-preview and live-execution progress components."""

from __future__ import annotations

from typing import final
from typing import override

import msgspec
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Button
from textual.widgets import Input
from textual.widgets import Label
from textual.widgets import ProgressBar
from textual.widgets import Static

from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.inspection import PlanInspection
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.tui.components.status import StatusBadge
from comparo.tui.components.status import status_for_outcome
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareMode
from comparo.tui.presentation import safe_terminal_text


@final
class CompareModeSelector(Horizontal):
    """The sole reusable control for the three Compare preparation modes."""

    @final
    class ModeSelected(Message):
        """A typed Compare mode was selected."""

        def __init__(self, mode: CompareMode) -> None:
            """Retain the selected closed mode."""
            super().__init__()
            self.mode = mode

    def __init__(
        self,
        mode: CompareMode,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the selector with one active mode."""
        super().__init__(id=id, classes=classes)
        if type(mode) is not CompareMode:
            raise TypeError("Compare mode selector requires CompareMode")
        self._mode = mode

    @property
    def mode(self) -> CompareMode:
        """Return the currently highlighted preparation mode."""
        return self._mode

    @override
    def compose(self) -> ComposeResult:
        """Compose modes from the one closed CompareMode vocabulary."""
        for mode, label in (
            (CompareMode.ASSERTIONS, "Assertions"),
            (CompareMode.DIFFERENCES, "Differences"),
            (CompareMode.EXECUTION_PROFILE, "Execution profile"),
        ):
            classes = "compare-mode-option"
            if mode is self._mode:
                classes += " compare-mode-active"
            yield Button(label, id=f"compare-mode-{mode.value}", classes=classes)

    @on(Button.Pressed)
    def select_mode(self, event: Button.Pressed) -> None:
        """Translate the pressed shared control into a typed mode message."""
        prefix = "compare-mode-"
        button_id = event.button.id or ""
        if not button_id.startswith(prefix):
            return
        mode = CompareMode(button_id.removeprefix(prefix))
        self._mode = mode
        for button in self.query(Button):
            button.set_class(button.id == button_id, "compare-mode-active")
        self.post_message(self.ModeSelected(mode))

    def show_mode(self, mode: CompareMode) -> None:
        """Update the highlighted mode without replacing the selector widget."""
        if type(mode) is not CompareMode:
            raise TypeError("Compare mode selector requires CompareMode")
        self._mode = mode
        if not self.is_mounted:
            return
        selected_id = f"compare-mode-{mode.value}"
        for button in self.query(Button):
            button.set_class(button.id == selected_id, "compare-mode-active")


@final
class CompareDraftForm(Vertical):
    """Shared bounded selectors for every Compare preparation mode."""

    @final
    class CompareDraftChanged(Message):
        """The current typed preparation draft changed."""

        def __init__(self, draft: CompareDraft) -> None:
            """Retain one immutable draft."""
            super().__init__()
            self.draft = draft

    @final
    class Submitted(Message):
        """The current typed draft was submitted for preview by the user."""

        def __init__(self, draft: CompareDraft) -> None:
            """Retain one immutable submitted draft."""
            super().__init__()
            self.draft = draft

    def __init__(
        self,
        draft: CompareDraft,
        *,
        resources: tuple[ProjectResourceSummary, ...] = (),
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize bounded fields from a draft and optional safe resource summaries."""
        super().__init__(id=id, classes=classes)
        if type(draft) is not CompareDraft:
            raise TypeError("Compare draft form requires CompareDraft")
        if type(resources) is not tuple or any(
            type(item) is not ProjectResourceSummary for item in resources
        ):
            raise TypeError("Compare draft resources must be immutable safe summaries")
        self._draft = _initialize_draft(draft, resources)
        self._resources = resources

    @override
    def compose(self) -> ComposeResult:
        """Compose every bounded selector once and vary only mode visibility."""
        yield CompareModeSelector(self._draft.mode, classes="compare-draft-modes")
        yield _draft_input(
            "Subject",
            "compare-draft-subject",
            self._draft.subject,
            _resource_hint(self._resources, {"Request", "Scenario"}),
            "compare-field-subject",
        )
        yield _draft_input(
            "Environment",
            "compare-draft-environment",
            self._draft.environment,
            _resource_hint(self._resources, {"Environment"}),
            "compare-field-environment",
        )
        yield _draft_input(
            "Baseline",
            "compare-draft-baseline",
            self._draft.baseline,
            _resource_hint(self._resources, {"Environment"}),
            "compare-field-baseline",
        )
        yield _draft_input(
            "Candidate",
            "compare-draft-candidate",
            self._draft.candidate,
            _resource_hint(self._resources, {"Environment"}),
            "compare-field-candidate",
        )
        yield _draft_input(
            "Execution profile",
            "compare-draft-profile",
            self._draft.execution_profile,
            _resource_hint(self._resources, {"ExecutionProfile"}),
            "compare-field-profile",
        )

    def on_mount(self) -> None:
        """Apply initial mode-dependent field visibility."""
        self._apply_mode_visibility()

    def current_draft(self) -> CompareDraft:
        """Return the exact bounded values currently present in the shared form."""
        if not self.is_mounted:
            return self._draft
        return CompareDraft(
            mode=self.query_one(CompareModeSelector).mode,
            subject=_optional_input(self, "#compare-draft-subject"),
            environment=_optional_input(self, "#compare-draft-environment"),
            baseline=_optional_input(self, "#compare-draft-baseline"),
            candidate=_optional_input(self, "#compare-draft-candidate"),
            execution_profile=_optional_input(self, "#compare-draft-profile"),
        )

    def show_draft(self, draft: CompareDraft) -> None:
        """Update existing controls from one immutable controller-owned draft."""
        if type(draft) is not CompareDraft:
            raise TypeError("Compare draft form requires CompareDraft")
        self._draft = draft
        if not self.is_mounted:
            return
        self.query_one(CompareModeSelector).show_mode(draft.mode)
        values = (
            ("#compare-draft-subject", draft.subject),
            ("#compare-draft-environment", draft.environment),
            ("#compare-draft-baseline", draft.baseline),
            ("#compare-draft-candidate", draft.candidate),
            ("#compare-draft-profile", draft.execution_profile),
        )
        for selector, value in values:
            self.query_one(selector, Input).value = value or ""
        self._apply_mode_visibility()

    @on(CompareModeSelector.ModeSelected)
    def change_mode(self, message: CompareModeSelector.ModeSelected) -> None:
        """Preserve typed field values while switching the active topology."""
        self._draft = msgspec.structs.replace(self.current_draft(), mode=message.mode)
        self._apply_mode_visibility()
        self.post_message(self.CompareDraftChanged(self._draft))

    @on(Input.Changed)
    def change_value(self) -> None:
        """Publish one coalescible typed draft after an input edit."""
        self._draft = self.current_draft()
        self.post_message(self.CompareDraftChanged(self._draft))

    @on(Input.Submitted)
    def submit(self) -> None:
        """Submit through a semantic message without declaring an application key binding."""
        self._draft = self.current_draft()
        self.post_message(self.Submitted(self._draft))

    def _apply_mode_visibility(self) -> None:
        mode = self.query_one(CompareModeSelector).mode
        visibility = {
            "compare-field-subject": mode is not CompareMode.EXECUTION_PROFILE,
            "compare-field-environment": mode is CompareMode.ASSERTIONS,
            "compare-field-baseline": mode is CompareMode.DIFFERENCES,
            "compare-field-candidate": mode is CompareMode.DIFFERENCES,
            "compare-field-profile": mode is CompareMode.EXECUTION_PROFILE,
        }
        for class_name, visible in visibility.items():
            self.query_one(f".{class_name}").display = visible


def _draft_input(
    label: str,
    input_id: str,
    value: str | None,
    hint: str,
    field_class: str,
) -> Horizontal:
    return Horizontal(
        Label(label, classes="compare-draft-label", markup=False),
        Input(
            value=value or "",
            placeholder=hint,
            max_length=63,
            id=input_id,
            classes="compare-draft-input",
        ),
        classes=f"compare-draft-field {field_class}",
    )


def _optional_input(form: CompareDraftForm, selector: str) -> str | None:
    value = form.query_one(selector, Input).value.strip()
    return value or None


def _resource_hint(
    resources: tuple[ProjectResourceSummary, ...],
    kinds: set[str],
) -> str:
    values = tuple(
        item.coordinate.resource_id for item in resources if item.coordinate.kind in kinds
    )
    if not values:
        return "Type a resource id"
    return safe_terminal_text(
        "Available: " + ", ".join(values),
        maximum_characters=128,
    )


def _initialize_draft(
    draft: CompareDraft,
    resources: tuple[ProjectResourceSummary, ...],
) -> CompareDraft:
    subjects = tuple(
        item.coordinate.resource_id
        for item in resources
        if item.coordinate.kind in {"Request", "Scenario"}
    )
    environments = tuple(
        item.coordinate.resource_id for item in resources if item.coordinate.kind == "Environment"
    )
    profiles = tuple(
        item.coordinate.resource_id
        for item in resources
        if item.coordinate.kind == "ExecutionProfile"
    )
    return msgspec.structs.replace(
        draft,
        subject=draft.subject or _first(subjects),
        environment=draft.environment or _first(environments),
        baseline=draft.baseline or _first(environments),
        candidate=draft.candidate or _second_or_first(environments),
        execution_profile=draft.execution_profile or _first(profiles),
    )


def _first(values: tuple[str, ...]) -> str | None:
    return None if not values else values[0]


def _second_or_first(values: tuple[str, ...]) -> str | None:
    if len(values) > 1:
        return values[1]
    return _first(values)


@final
class PlanPreview(Static):
    """Compact admitted-plan summary shared by every Compare mode."""

    def __init__(
        self,
        plan: PlanInspection | None,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a preview from an admitted plan or its absence."""
        super().__init__("", id=id, classes=classes, markup=False)
        self.show_plan(plan)

    def show_plan(self, plan: PlanInspection | None) -> None:
        """Display application-owned topology and cardinality without re-planning."""
        if plan is None:
            self.update("Preview the current selections to inspect the admitted plan.")
            return
        cardinality = plan.cardinality
        lines = [
            f"Run {plan.run_id}",
            (
                f"{cardinality.cells} cells · {cardinality.trials} trials · "
                f"{cardinality.environment_executions} executions · "
                f"{cardinality.comparisons} comparisons"
            ),
        ]
        lines.extend(
            (
                f"{item.subject.kind}/{item.subject.resource_id} · "
                f"{len(item.environments)} environments · {item.cardinality.cells} cells"
            )
            for item in plan.slices
        )
        self.update(
            "\n".join(
                safe_terminal_text(item, maximum_characters=512, multiline=False) for item in lines
            )
        )


@final
class ExecutionProgress(Vertical):
    """One bounded coalesced progress snapshot; it never retains runtime events."""

    def __init__(
        self,
        snapshot: ExecutionProgressSnapshot,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize progress from one immutable application snapshot."""
        super().__init__(id=id, classes=classes)
        self._snapshot = snapshot

    @override
    def compose(self) -> ComposeResult:
        """Compose shared progress status, counts, and progress bar."""
        with Horizontal(classes="execution-progress-heading"):
            yield StatusBadge(
                status_for_outcome(self._snapshot.phase.value),
                classes="execution-progress-status",
            )
            yield Static("", classes="execution-progress-label", markup=False)
        yield ProgressBar(classes="execution-progress-bar", show_eta=False)
        yield Static("", classes="execution-progress-counts", markup=False)

    def on_mount(self) -> None:
        """Render the initial immutable snapshot."""
        self._apply_snapshot()

    def show_snapshot(self, snapshot: ExecutionProgressSnapshot) -> None:
        """Replace progress from the application session's latest coalesced snapshot."""
        self._snapshot = snapshot
        if self.is_mounted:
            self._apply_snapshot()

    def _apply_snapshot(self) -> None:
        snapshot = self._snapshot
        total = snapshot.total_executions + snapshot.total_comparisons
        finished = snapshot.finished_executions + snapshot.finished_comparisons
        self.query_one(".execution-progress-status", StatusBadge).set_status(
            status_for_outcome(snapshot.phase.value)
        )
        self.query_one(".execution-progress-label", Static).update(
            safe_terminal_text(f"Run {snapshot.run_id}", maximum_characters=128)
        )
        self.query_one(".execution-progress-bar", ProgressBar).update(
            total=total,
            progress=finished,
        )
        self.query_one(".execution-progress-counts", Static).update(
            f"{snapshot.finished_executions}/{snapshot.total_executions} executions · "
            f"{snapshot.finished_comparisons}/{snapshot.total_comparisons} comparisons · "
            f"{snapshot.active_executions} active"
        )


__all__ = (
    "CompareDraftForm",
    "CompareModeSelector",
    "ExecutionProgress",
    "PlanPreview",
)
