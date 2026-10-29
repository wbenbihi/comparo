"""Launch lifecycle coordinator over shared TUI4 components and application services."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import ClassVar
from typing import override

from rich.text import Text
from textual import on
from textual import work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.events import Resize

from comparo.application import PlanReviewInspection
from comparo.application import RunHandle
from comparo.application import present_diagnostic
from comparo.tui4.bindings import launch_bindings
from comparo.tui4.components.actionbar import Action
from comparo.tui4.components.actionbar import ActionBar
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.launchconfig import LaunchConfiguration
from comparo.tui4.components.launchsummary import LaunchReview
from comparo.tui4.components.launchsummary import LaunchSummary
from comparo.tui4.components.launchsummary import SummarySection
from comparo.tui4.components.launchsummary import draft_sections
from comparo.tui4.components.launchsummary import preview_sections
from comparo.tui4.components.results import ResultsView
from comparo.tui4.components.runview import LiveRunView
from comparo.tui4.components.typography import Caption
from comparo.tui4.models.diff import build_diff_document
from comparo.tui4.models.diff import build_source_document
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.launch import LaunchDraft
from comparo.tui4.theme import style
from comparo.tui4.types import ComparoScreen
from comparo.tui4.workspaces.base import Workspace


class LaunchWorkspace(Workspace):
    """Coordinate effects; shared components own selection, formatting and interaction."""

    SCREEN = ComparoScreen.LAUNCH
    BINDINGS: ClassVar[list[BindingType]] = launch_bindings()

    def __init__(self) -> None:
        """Initialize one session-local draft and one reviewed-plan slot."""
        super().__init__()
        self.catalog: ExplorerCatalog | None = None
        self.review: PlanReviewInspection | None = None
        self.run_handle: RunHandle | None = None
        self.phase = "loading"
        self.analysis_open = False
        self.draft = LaunchDraft()

    @override
    def compose(self) -> ComposeResult:
        """Compose the component library without reaching into native widget internals."""
        yield Caption("", id="launch-steps")
        yield Caption("", id="launch-title", markup=False)
        yield Caption("", id="launch-subtitle", markup=False)
        with Horizontal(id="launch-layout"):
            yield LaunchConfiguration(id="launch-config")
            with Vertical(id="launch-target-rail"):
                yield LaunchSummary(id="launch-summary")
                yield ActionBar(id="launch-target-actions")
            yield LaunchReview(id="launch-review")
            yield LiveRunView(id="launch-live")
            yield ResultsView(id="launch-results")
        yield Caption("", id="launch-status", markup=False)
        yield ActionBar(id="launch-actions")

    def on_mount(self) -> None:
        """Load the catalog and observe backend-owned runs without stealing focus."""
        self._update_controls()
        self._load_catalog()
        self.set_interval(0.2, self._poll_run)

    @override
    def focus_default(self) -> None:
        """Focus a visible major region without implicitly activating its controls."""
        if self.phase in {"loading", "draft"}:
            self.query_one(LaunchConfiguration).focus_default()
        elif self.phase == "ready":
            self.query_one(LaunchReview).focus()
        elif self.query_one(LiveRunView).display:
            self.query_one(LiveRunView).focus()
        elif self.query_one(ResultsView).display:
            self.query_one(ResultsView).focus()
        else:
            self.query_one(LaunchSummary).focus()

    def _focus_if_visible(self) -> None:
        if self.display:
            self.focus_default()

    def _status(self, text: str) -> None:
        self.query_one("#launch-status", Caption).update(text)

    @work(exclusive=True, group="launch-catalog")
    async def _load_catalog(self) -> None:
        """Load safe catalog choices on a worker thread."""
        self.phase = "loading"
        self._update_controls()
        try:
            result = await asyncio.to_thread(self.backend.load_workspace)
            self.catalog = build_catalog(result)
        except Exception:  # noqa: BLE001 - arbitrary exception text can contain private values
            self.catalog = None
            self._status("Could not load the project. Correct the configuration and reload.")
        else:
            if self.catalog is None:
                self._status(
                    "\n".join(
                        f"{present_diagnostic(item).title}: "
                        f"{present_diagnostic(item).action} ({item.code})"
                        for item in result.diagnostics
                    )
                )
        self.phase = "draft"
        if self.catalog is not None:
            self.query_one(LaunchConfiguration).load(self.catalog)
        else:
            self.query_one(LaunchSummary).sections = (
                SummarySection("Unavailable", note="No executable project loaded."),
            )
        self._update_controls()
        self.call_after_refresh(self._focus_if_visible)

    @on(LaunchConfiguration.Changed)
    def _draft_changed(self, event: LaunchConfiguration.Changed) -> None:
        event.stop()
        if self.phase != "draft":
            return
        self.backend.discard_handle()
        self.review = None
        self.draft = event.draft
        self.query_one(LaunchSummary).sections = draft_sections(self.draft, self.catalog)
        if self.catalog is not None:
            self._status(" ".join(self.draft.problems))
        self._update_controls()
        if not self.draft.problems and self.catalog is not None:
            self._preview(self.draft)

    @work(exclusive=True, group="launch-preview")
    async def _preview(self, draft: LaunchDraft) -> None:
        await asyncio.sleep(0.15)
        if self.phase != "draft" or self.draft != draft:
            return
        try:
            result = await self.backend.preview_launch(
                draft.execution_intent,
                draft.options(),
                approve_mutations=draft.approve_mutations,
            )
        except Exception:  # noqa: BLE001 - keep arbitrary exception values outside the UI
            return
        if self.phase != "draft" or self.draft != draft:
            return
        # A cancelled thread-backed preview can finish during child teardown.
        if not list(self.query(LaunchSummary)) or not list(self.query(LaunchConfiguration)):
            return
        if result.review is not None:
            self.query_one(LaunchSummary).sections = preview_sections(draft, result.review)
            self.query_one(LaunchConfiguration).load_preview(result.review)
        elif result.diagnostics:
            self._status(" ".join(present_diagnostic(item).title for item in result.diagnostics))

    def _update_controls(self) -> None:
        phase = self.phase
        self.query_one(LaunchConfiguration).display = phase in {"loading", "draft", "preparing"}
        self.query_one(LaunchConfiguration).disabled = phase != "draft"
        self.query_one("#launch-target-rail").display = phase in {"loading", "draft", "preparing"}
        self.query_one(LaunchSummary).display = phase in {"loading", "draft", "preparing"}
        self.query_one(LiveRunView).display = phase in {"running", "cancelling"} or (
            phase == "finished" and not self.analysis_open
        )
        self.query_one(ResultsView).display = phase == "finished" and self.analysis_open
        self.query_one(LaunchReview).display = phase == "ready"
        self.query_one(LaunchReview).catalog = self.catalog
        self.query_one(LaunchReview).draft = self.draft
        self.query_one(LaunchReview).review = self.review
        actions: tuple[Action, ...]
        if phase in {"loading", "draft", "preparing"}:
            actions = (
                Action(
                    "prepare",
                    "Prepare plan",
                    "^r",
                    enabled=bool(self.check_action("prepare", ())),
                    primary=True,
                ),
                Action("reload", "Reload project", enabled=phase == "draft"),
            )
        elif phase == "ready":
            count = self.review.plan.cardinality.environment_executions if self.review else 0
            actions = (
                Action(
                    "run",
                    f"Run {count} calls",
                    "^x",
                    enabled=bool(self.check_action("run", ())),
                    primary=True,
                ),
                Action("edit", "Edit configuration", "^e"),
            )
        elif phase in {"running", "cancelling"}:
            actions = (
                Action("cancel", "Cancel run", "^k", enabled=phase == "running", primary=True),
            )
        else:
            actions = (
                Action(
                    "run_summary" if self.analysis_open else "analyze",
                    "Run summary" if self.analysis_open else "Analyze results",
                    primary=True,
                ),
                Action("edit", "Configure another run", "^e"),
            )
        target = phase in {"loading", "draft", "preparing"}
        self.query_one("#launch-actions", ActionBar).display = not target
        self.query_one("#launch-target-actions", ActionBar).actions = actions if target else ()
        self.query_one("#launch-actions", ActionBar).actions = actions
        active = (
            0
            if target
            else 1
            if phase == "ready"
            else 2
            if phase in {"running", "cancelling"} or not self.analysis_open
            else 3
        )
        steps = Text()
        for index, label in enumerate(("Target", "Plan", "Run", "Analyze")):
            if index:
                steps.append("   →   ", style("dim"))
            steps.append(
                ("✓ " if index < active else "● " if index == active else "· ") + label,
                style(
                    "status.passed"
                    if index < active
                    else "status.running"
                    if index == active
                    else "dim"
                ),
            )
        self.query_one("#launch-steps", Caption).update(steps)
        count = self.review.plan.cardinality.environment_executions if self.review else 0
        title = (
            "What do you want to validate?"
            if target
            else (
                f"Ready to run {count} HTTP calls."
                if phase == "ready"
                else "Running "
                + (
                    self.draft.profile or "execution profile"
                    if self.draft.execution_intent.value == "execution-profile"
                    else "environment comparison"
                    if self.draft.execution_intent.value == "comparison"
                    else "assertion checks"
                )
                if active == 2
                else ""
            )
        )
        subtitle = (
            "Choose requests, an execution profile, or assertions. Configure the scope below."
            if target
            else "Review the admitted plan. Editing the target will invalidate this plan."
            if phase == "ready"
            else ""
        )
        if phase == "finished" and not self.analysis_open:
            projection = self.query_one(ResultsView).projection
            title = "Run finished"
            if projection is not None:
                title += (
                    f" · Gate {projection.summary.gate_verdict} · {projection.summary.completion}"
                )
            subtitle = "Inspect completed calls below. Open Analyze results when you are ready."
        self.query_one("#launch-title", Caption).update(title)
        self.query_one("#launch-title").display = bool(title)
        self.query_one("#launch-subtitle", Caption).update(subtitle)
        self.query_one("#launch-subtitle").display = bool(subtitle)
        self.query_one(LaunchSummary).border_title = "SCOPE SUMMARY"
        self.refresh_bindings()

    @override
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Expose only actions that the current lifecycle state can honor."""
        if action.startswith("target_"):
            return self.phase == "draft"
        if action == "prepare":
            return self.phase == "draft" and self.catalog is not None and not self.draft.problems
        if action == "run":
            return (
                self.phase == "ready" and self.review is not None and self.review.execution_allowed
            )
        if action == "edit":
            return self.phase in {"ready", "finished"}
        if action == "cancel":
            return self.phase == "running"
        if action in {"options", "focus_filter"}:
            return self.phase == "draft"
        return super().check_action(action, parameters)

    @on(ActionBar.Requested, "#launch-target-actions")
    @on(ActionBar.Requested, "#launch-actions")
    def _action_requested(self, event: ActionBar.Requested) -> None:
        event.stop()
        actions = {
            "prepare": self.action_prepare,
            "run": self.action_run,
            "edit": self.action_edit,
            "cancel": self.action_cancel,
            "reload": self.action_reload,
            "analyze": self.action_analyze,
            "run_summary": self.action_run_summary,
        }
        action = actions.get(event.action)
        if action is not None:
            action()

    def _move_target(self, direction: int) -> None:
        configuration = self.query_one(LaunchConfiguration)
        bar = self.query_one("#launch-target-actions", ActionBar)
        focused = self.app.focused
        if focused is not None and bar in focused.ancestors:
            if not bar.move_focus(direction):
                if direction < 0:
                    configuration.focus_section(4)
                else:
                    configuration.focus_default()
        elif not configuration.move_target_focus(direction):
            bar.focus_action(last=direction < 0)

    def action_target_next(self) -> None:
        """Tab skips editor internals until a section is explicitly entered."""
        self._move_target(1)

    def action_target_previous(self) -> None:
        """Reverse the same semantic focus route."""
        self._move_target(-1)

    def action_target_back(self) -> None:
        """Leave section editing with one key."""
        self.query_one(LaunchConfiguration).return_to_sections()

    def action_target_section(self, index: int) -> None:
        """Jump directly to one of the five configuration sections."""
        self.query_one(LaunchConfiguration).focus_section(index, edit=True)

    def action_prepare(self) -> None:
        """Freeze selection before asynchronous preparation."""
        if not self.check_action("prepare", ()):
            return
        self.phase = "preparing"
        self._status("Preparing the exact plan… No requests have been sent.")
        self._update_controls()
        self.focus_default()
        self._prepare(self.draft)

    @work(exclusive=True, group="launch-prepare")
    async def _prepare(self, draft: LaunchDraft) -> None:
        try:
            result = await self.backend.prepare_launch(
                draft.execution_intent, draft.options(), approve_mutations=draft.approve_mutations
            )
        except Exception:  # noqa: BLE001 - diagnostics must never expose arbitrary exception text
            self.phase = "draft"
            self._status("Preparation failed. Check the project and retry; no requests were sent.")
        else:
            self.review = result.review
            self.query_one(LaunchReview).destinations = None
            if self.review is not None:
                with suppress(Exception):
                    self.query_one(
                        LaunchReview
                    ).destinations = await self.backend.launch_destinations()
            if self.review is None:
                self.phase = "draft"
                self._status(
                    "\n".join(
                        f"{present_diagnostic(item).title}: "
                        f"{present_diagnostic(item).action} ({item.code})"
                        for item in result.diagnostics
                    )
                )
            else:
                self.phase = "ready"
                self._status(
                    "Edit configuration to address blockers."
                    if not self.review.execution_allowed
                    else "Plan ready. Run executes this reviewed selection."
                )
        self._update_controls()
        self.call_after_refresh(self._focus_if_visible)

    def action_edit(self) -> None:
        """Invalidate admission before returning to the preserved draft."""
        if not self.check_action("edit", ()):
            return
        self.backend.discard_handle()
        self.review = None
        if self.run_handle is not None:
            self.backend.release_launch(self.run_handle)
        self.run_handle = None
        self.phase = "draft"
        configuration = self.query_one(LaunchConfiguration)
        configuration.choices("mutations").selected = ()
        self.draft = configuration.draft()
        self.query_one(LaunchSummary).sections = draft_sections(self.draft, self.catalog)
        self._status(" ".join(self.draft.problems))
        self._update_controls()
        self.focus_default()
        if not self.draft.problems:
            self._preview(self.draft)

    def action_reload(self) -> None:
        """Refresh available choices after an external edit."""
        if self.phase == "draft":
            self.backend.discard_handle()
            self._load_catalog()

    def action_options(self) -> None:
        """Toggle the shared run-options section."""
        if self.phase == "draft":
            self.query_one(LaunchConfiguration).focus_section(4, edit=True)

    def action_focus_filter(self) -> None:
        """Focus the shared filter only in a mode that exposes Request selection."""
        if self.phase == "draft" and self.draft.execution_intent.value != "execution-profile":
            self.query_one(FilterInput).focus()

    def action_run(self) -> None:
        """Execute the retained checkpoint without rebuilding or changing its controls."""
        if not self.check_action("run", ()):
            return
        try:
            self.run_handle = self.backend.start_launch()
        except Exception:  # noqa: BLE001 - backend exception payloads stay private
            self._status("Could not start this plan. Edit configuration and prepare again.")
            return
        self.phase = "running"
        self.analysis_open = False
        self.query_one(ResultsView).reset()
        self.query_one(LiveRunView).begin(self.catalog, self.review)
        self._status("Execution continues when you visit another workspace.")
        self._update_controls()
        self.focus_default()
        self._poll_run()

    def action_cancel(self) -> None:
        """Request cancellation once and keep observing cleanup."""
        if self.phase != "running" or self.run_handle is None:
            return
        self.backend.cancel_launch(self.run_handle)
        self.phase = "cancelling"
        self._status("Cancellation requested. Waiting for execution cleanup…")
        self._update_controls()

    def _poll_run(self) -> None:
        if self.phase not in {"running", "cancelling"} or self.run_handle is None:
            return
        inspection = self.backend.inspect_launch(self.run_handle)
        snapshot = inspection.snapshot
        self.query_one(LiveRunView).update_snapshot(snapshot)
        if inspection.terminal:
            self.phase = "finished"
            self._status("Loading sanitized results…")
            self._update_controls()
            self._finish_run(self.run_handle)

    @work(group="launch-outcome")
    async def _finish_run(self, run: RunHandle) -> None:
        try:
            projection = await self.backend.launch_results(run)
            messages = await self.backend.launch_outcome(run)
        except Exception:  # noqa: BLE001 - exception payloads stay outside the UI
            projection = None
            messages = ("Could not load sanitized results.",)
        if self.run_handle == run and self.phase == "finished":
            if projection is not None:
                self.query_one(ResultsView).load(projection)
            else:
                self.query_one(ResultsView).query_one("#result-headline", Caption).update(
                    "Results unavailable. " + " ".join(messages)
                )
            self._status(" ".join(messages))
            self._update_controls()

    def action_analyze(self) -> None:
        """Enter analysis explicitly, keeping fast runs inspectable until requested."""
        if self.phase == "finished":
            self.analysis_open = True
            self._update_controls()
            self.focus_default()

    def action_run_summary(self) -> None:
        """Return to the frozen execution table without discarding analysis selection."""
        if self.phase == "finished":
            self.analysis_open = False
            self._update_controls()
            self.focus_default()

    @on(ResultsView.BodyRequested)
    def _body_requested(self, event: ResultsView.BodyRequested) -> None:
        event.stop()
        if self.phase == "finished":
            self._read_body(event)

    @work(exclusive=True, group="launch-body")
    async def _read_body(self, event: ResultsView.BodyRequested) -> None:
        try:
            windows = [
                await self.backend.read_result_body(reference) for reference in event.references
            ]
        except Exception:  # noqa: BLE001 - authenticated read failures remain value-free
            event.view.show_body(
                event.references, None, "Capture unavailable or report verification failed."
            )
            return
        texts = [window.content.decode("utf-8", errors="replace") for window in windows]
        # Body data can include terminal controls even after value redaction.
        texts = [
            "".join(char if char.isprintable() or char in "\n\t" else "�" for char in text)
            for text in texts
        ]
        document = (
            build_diff_document(*texts) if len(texts) == 2 else build_source_document(texts[0])
        )
        message = " · ".join(
            f"{window.reference.message.value}: {window.source_state}/{window.extent}"
            f" · {len(window.content)} of "
            f"{window.total_bytes if window.total_bytes is not None else '?'} bytes"
            f" · {window.redaction_count} redactions"
            + (f" · {window.reason}" if window.reason else "")
            + (" · preview truncated at 64 KiB" if window.next_offset is not None else "")
            for window in windows
        )
        event.view.show_body(event.references, document, message)

    def on_resize(self, event: Resize) -> None:
        """Allocate a readable rail or move its content below the configuration."""
        self.set_class(event.size.width < 88, "narrow")
        self.query_one(LaunchConfiguration).set_compact(event.size.width < 88)
        self.set_class(event.size.width < 128, "stacked-scope")
        self.query_one(LaunchSummary).compact = event.size.width < 128
        self.set_class(event.size.height < 42, "short")
