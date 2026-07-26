"""Native Compare Target, Plan, and Run lifecycle surfaces."""

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
from textual.widgets import Collapsible
from textual.widgets import Label
from textual.widgets import Select
from textual.widgets import SelectionList
from textual.widgets import Static
from textual.widgets import Tab
from textual.widgets import Tabs

from comparo.application.project_workspace import ProjectResourceSummary
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareMode
from comparo.tui.presentation import safe_terminal_text

_MODES = (
    (CompareMode.DIFFERENCES, "Differences"),
    (CompareMode.ASSERTIONS, "Assertions"),
    (CompareMode.EXECUTION_PROFILE, "Execution profile"),
)


@final
class CompareLifecycleBar(Horizontal):
    """Shared visible Target → Plan → Run → Analyze lifecycle orientation."""

    def __init__(
        self,
        active: str,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Retain the active lifecycle stage."""
        super().__init__(id=id, classes=classes)
        self._active = active

    @override
    def compose(self) -> ComposeResult:
        """Compose compact non-interactive stage markers."""
        stages = ("Target", "Plan", "Run", "Analyze")
        active_index = stages.index(self._active)
        for index, stage in enumerate(stages):
            classes = "compare-lifecycle-stage"
            if index == active_index:
                classes += " compare-lifecycle-active"
            elif index < active_index:
                classes += " compare-lifecycle-complete"
            yield Static(f"{index + 1}  {stage}", classes=classes, markup=False)


@final
class CompareTargetPanel(Vertical):
    """Project-aware native selectors for one comparison target."""

    @final
    class DraftChanged(Message):
        """The complete typed target changed."""

        def __init__(self, draft: CompareDraft) -> None:
            """Retain the updated typed draft."""
            super().__init__()
            self.draft = draft

    @final
    class Submitted(Message):
        """The current target should be compiled into a Plan."""

        def __init__(self, draft: CompareDraft) -> None:
            """Retain the submitted typed draft."""
            super().__init__()
            self.draft = draft

    def __init__(
        self,
        draft: CompareDraft,
        resources: tuple[ProjectResourceSummary, ...],
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize from one controller-owned draft and safe project catalog."""
        if type(draft) is not CompareDraft:
            raise TypeError("Compare target requires a typed draft")
        if type(resources) is not tuple or any(
            type(resource) is not ProjectResourceSummary for resource in resources
        ):
            raise TypeError("Compare target resources must be immutable safe summaries")
        super().__init__(id=id, classes=classes)
        self._resources = resources
        self._draft = seed_compare_draft(draft, resources)

    @override
    def compose(self) -> ComposeResult:
        """Compose mode, target topology, scope preview, and advanced context."""
        mode = self._draft.mode
        yield Tabs(
            *(Tab(label, id=f"compare-target-mode-{item.value}") for item, label in _MODES),
            active=f"compare-target-mode-{mode.value}",
            id="compare-target-modes",
        )
        with Horizontal(classes="compare-target-layout"):
            with Vertical(classes="compare-target-selectors"):
                if mode is CompareMode.EXECUTION_PROFILE:
                    yield _label("Execution profile", "A reusable authored execution recipe")
                    yield _select(
                        self._resources,
                        "ExecutionProfile",
                        self._draft.execution_profile,
                        "compare-target-profile",
                    )
                else:
                    yield _label("Requests", "Select one or more Requests or future Scenarios")
                    yield _selection_list(
                        self._resources,
                        {"Request", "Scenario"},
                        self._draft.subjects,
                        "compare-target-subjects",
                    )
                if mode is CompareMode.ASSERTIONS:
                    yield _label("Environments", "Each target is validated independently")
                    yield _selection_list(
                        self._resources,
                        {"Environment"},
                        self._draft.environments,
                        "compare-target-environments",
                    )
                elif mode is CompareMode.DIFFERENCES:
                    yield _label("Baseline", "Reference Environment")
                    yield _select(
                        self._resources,
                        "Environment",
                        self._draft.baseline,
                        "compare-target-baseline",
                    )
                    yield _label("Candidates", "One or more Environments compared to baseline")
                    yield _selection_list(
                        self._resources,
                        {"Environment"},
                        self._draft.candidates,
                        "compare-target-candidates",
                    )
                yield Collapsible(
                    Static(_advanced_context(self._draft, self._resources), markup=False),
                    title="Advanced overrides and safety context",
                    collapsed=True,
                    id="compare-target-advanced",
                )
            with Vertical(classes="compare-target-summary"):
                yield Label("SCOPE", classes="compare-panel-kicker")
                yield Static(_scope_summary(self._draft, self._resources), markup=False)
                yield Label("VALIDATION", classes="compare-panel-kicker")
                yield Static(_validation_summary(self._draft), id="compare-target-validation")
                yield Button("Review plan", id="compare-review-plan", variant="primary")

    def current_draft(self) -> CompareDraft:
        """Read the current native selector values into one immutable target."""
        if not self.is_mounted:
            return self._draft
        mode = self._draft.mode
        draft = self._draft
        if mode is CompareMode.EXECUTION_PROFILE:
            draft = msgspec.structs.replace(
                draft,
                execution_profile=_select_value(self, "#compare-target-profile"),
            )
        else:
            subjects = self.query_one("#compare-target-subjects", SelectionList).selected
            draft = msgspec.structs.replace(draft, subjects=tuple(subjects))
        if mode is CompareMode.ASSERTIONS:
            environments = self.query_one("#compare-target-environments", SelectionList).selected
            draft = msgspec.structs.replace(draft, environments=tuple(environments))
        elif mode is CompareMode.DIFFERENCES:
            candidates = self.query_one("#compare-target-candidates", SelectionList).selected
            draft = msgspec.structs.replace(
                draft,
                baseline=_select_value(self, "#compare-target-baseline"),
                candidates=tuple(candidates),
            )
        self._draft = draft
        return draft

    def show_draft(self, draft: CompareDraft) -> None:
        """Update the native Target surface from a controller-owned draft."""
        if type(draft) is not CompareDraft:
            raise TypeError("Compare target requires a typed draft")
        seeded = seed_compare_draft(draft, self._resources)
        if seeded == self._draft:
            return
        self._draft = seeded
        if self.is_mounted:
            self.refresh(recompose=True)

    @on(Tabs.TabActivated, "#compare-target-modes")
    def change_mode(self, event: Tabs.TabActivated) -> None:
        """Switch target mode while retaining compatible prior selections."""
        prefix = "compare-target-mode-"
        tab_id = event.tab.id or ""
        if not tab_id.startswith(prefix):
            return
        mode = CompareMode(tab_id.removeprefix(prefix))
        if mode is self._draft.mode:
            return
        self._draft = switch_compare_draft_mode(self._draft, mode, self._resources)
        self.post_message(self.DraftChanged(self._draft))
        self.refresh(recompose=True)

    @on(SelectionList.SelectedChanged)
    @on(Select.Changed)
    def change_selection(self) -> None:
        """Publish one complete typed draft after native selector changes."""
        self._draft = self.current_draft()
        self.query_one(".compare-target-summary", Vertical).query_one(Static).update(
            _scope_summary(self._draft, self._resources)
        )
        self.post_message(self.DraftChanged(self._draft))

    @on(Button.Pressed, "#compare-review-plan")
    def submit(self) -> None:
        """Compile the current target through the application coordinator."""
        self._draft = self.current_draft()
        self.post_message(self.Submitted(self._draft))


def _label(title: str, description: str) -> Vertical:
    return Vertical(
        Label(title, classes="compare-field-label"),
        Static(description, classes="compare-field-description", markup=False),
        classes="compare-field-heading",
    )


def _selection_list(
    resources: tuple[ProjectResourceSummary, ...],
    kinds: set[str],
    selected: tuple[str, ...],
    identifier: str,
) -> SelectionList[str]:
    options = tuple(
        (
            safe_terminal_text(resource.name or resource.coordinate.resource_id),
            resource.coordinate.resource_id,
            resource.coordinate.resource_id in selected,
        )
        for resource in resources
        if resource.coordinate.kind in kinds
    )
    return SelectionList(*options, id=identifier, classes="compare-target-selection")


def _select(
    resources: tuple[ProjectResourceSummary, ...],
    kind: str,
    selected: str | None,
    identifier: str,
) -> Select[str]:
    options = tuple(
        (
            safe_terminal_text(resource.name or resource.coordinate.resource_id),
            resource.coordinate.resource_id,
        )
        for resource in resources
        if resource.coordinate.kind == kind
    )
    value = Select.NULL if selected is None else selected
    return Select(options, value=value, allow_blank=not options, id=identifier)


def _select_value(panel: CompareTargetPanel, selector: str) -> str | None:
    value = panel.query_one(selector, Select).value
    return None if value is Select.NULL else str(value)


def seed_compare_draft(
    draft: CompareDraft,
    resources: tuple[ProjectResourceSummary, ...],
) -> CompareDraft:
    """Apply deterministic project-aware defaults without mutating authored resources."""
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
    if draft.mode is CompareMode.ASSERTIONS:
        seeded = msgspec.structs.replace(
            draft,
            subjects=draft.subjects or subjects[:1],
            environments=draft.environments or environments[:1],
        )
    elif draft.mode is CompareMode.DIFFERENCES:
        seeded = msgspec.structs.replace(
            draft,
            subjects=draft.subjects or subjects[:1],
            baseline=draft.baseline or (environments[0] if environments else None),
            candidates=draft.candidates or environments[1:2] or environments[:1],
        )
    else:
        seeded = msgspec.structs.replace(
            draft,
            execution_profile=draft.execution_profile or (profiles[0] if profiles else None),
        )
    return draft if seeded == draft else seeded


def switch_compare_draft_mode(
    draft: CompareDraft,
    mode: CompareMode,
    resources: tuple[ProjectResourceSummary, ...],
) -> CompareDraft:
    """Change mode without constructing an invalid intermediate target topology."""
    return seed_compare_draft(_draft_for_mode(draft, mode), resources)


def _draft_for_mode(draft: CompareDraft, mode: CompareMode) -> CompareDraft:
    """Clear mode-exclusive topology atomically before applying project defaults."""
    if mode is CompareMode.ASSERTIONS:
        return msgspec.structs.replace(
            draft,
            mode=mode,
            baseline=None,
            candidates=(),
            execution_profile=None,
        )
    if mode is CompareMode.DIFFERENCES:
        return msgspec.structs.replace(
            draft,
            mode=mode,
            environments=(),
            execution_profile=None,
        )
    return msgspec.structs.replace(
        draft,
        mode=mode,
        subjects=(),
        environments=(),
        baseline=None,
        candidates=(),
    )


def _scope_summary(
    draft: CompareDraft,
    resources: tuple[ProjectResourceSummary, ...],
) -> str:
    matrices = sum(resource.coordinate.kind == "Matrix" for resource in resources)
    if draft.mode is CompareMode.EXECUTION_PROFILE:
        topology = f"Profile\n  {draft.execution_profile or 'not selected'}"
    elif draft.mode is CompareMode.ASSERTIONS:
        topology = (
            f"Subjects\n  {', '.join(draft.subjects) or 'none'}\n\n"
            f"Environments\n  {', '.join(draft.environments) or 'none'}"
        )
    else:
        topology = (
            f"Subjects\n  {', '.join(draft.subjects) or 'none'}\n\n"
            f"Topology\n  {draft.baseline or 'none'} → "
            f"{', '.join(draft.candidates) or 'none'}"
        )
    return (
        f"{topology}\n\nMatrix resources\n  {matrices} available\n\nOverrides\n  session values win"
    )


def _validation_summary(draft: CompareDraft) -> str:
    return (
        "Ready to compile a bounded, value-unresolved plan."
        if draft.validation_code is None
        else f"Target requires attention: {draft.validation_code.value}"
    )


def _advanced_context(
    draft: CompareDraft,
    resources: tuple[ProjectResourceSummary, ...],
) -> str:
    counts = {
        kind: sum(item.coordinate.kind == kind for item in resources)
        for kind in ("Matrix", "SafetyProfile", "AssertionProfile", "DiffProfile")
    }
    trials = "inherit" if draft.trials is None else str(draft.trials)
    attempts = "inherit" if draft.maximum_attempts is None else str(draft.maximum_attempts)
    return (
        f"Trials                 {trials}\n"
        f"Maximum attempts       {attempts}\n"
        f"Fail fast              {draft.fail_fast or 'inherit'}\n"
        f"Matrix resources       {counts['Matrix']}\n"
        f"Assertion profiles     {counts['AssertionProfile']}\n"
        f"Diff profiles          {counts['DiffProfile']}\n"
        f"Safety profiles        {counts['SafetyProfile']}\n"
        f"Mutation approval      {'explicit' if draft.approve_mutations else 'not granted'}"
    )


__all__ = (
    "CompareLifecycleBar",
    "CompareTargetPanel",
    "seed_compare_draft",
    "switch_compare_draft_mode",
)
