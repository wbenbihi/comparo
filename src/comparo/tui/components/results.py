"""Reusable result summary, index, detail, and workspace components."""

from __future__ import annotations

from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label
from textual.widgets import ListItem
from textual.widgets import ListView
from textual.widgets import Static
from textual.widgets import Tab
from textual.widgets import Tabs

from comparo.application.results import ComparisonResultRow
from comparo.application.results import ExecutionResultRow
from comparo.application.results import ResultFact
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.application.results import ResultSummary as ResultSummaryModel
from comparo.tui.components.base import ResponsiveWidget
from comparo.tui.components.result_lenses import ResultIndexKind
from comparo.tui.components.result_lenses import analysis_lenses
from comparo.tui.components.result_lenses import is_issue
from comparo.tui.components.result_lenses import lens_heading
from comparo.tui.components.result_lenses import lens_summary
from comparo.tui.components.status import StatusBadge
from comparo.tui.components.status import status_for_outcome
from comparo.tui.contracts import ResultAccess
from comparo.tui.presentation import safe_terminal_text


@final
class ResultIndexSelector(Tabs):
    """Native analysis-lens tabs shared by live and stored runs."""

    @final
    class IndexSelected(Message):
        """A typed result collection was selected."""

        def __init__(self, kind: ResultIndexKind) -> None:
            """Retain the selected closed index kind."""
            super().__init__()
            self.kind = kind

    def __init__(
        self,
        kind: ResultIndexKind,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the selector with one active result collection."""
        if type(kind) is not ResultIndexKind:
            raise TypeError("result index selector requires ResultIndexKind")
        visible = analysis_lenses()
        selected = kind if kind in visible else ResultIndexKind.ISSUES
        super().__init__(
            *(Tab(item.value.title(), id=f"result-index-{item.value}") for item in visible),
            active=f"result-index-{selected.value}",
            id=id,
            classes=classes,
        )
        self._kind = selected

    @property
    def kind(self) -> ResultIndexKind:
        """Return the currently active result collection."""
        return self._kind

    @on(Tabs.TabActivated)
    def activate_index(self, event: Tabs.TabActivated) -> None:
        """Translate one native tab activation into a typed lens message."""
        prefix = "result-index-"
        tab_id = event.tab.id or ""
        if not tab_id.startswith(prefix):
            return
        kind = ResultIndexKind(tab_id.removeprefix(prefix))
        if kind is self._kind:
            return
        self._kind = kind
        self.post_message(self.IndexSelected(kind))


@final
class ResultSummary(Vertical):
    """Shared artifact header and exact gate/outcome metrics."""

    def __init__(
        self,
        summary: ResultSummaryModel,
        *,
        access: ResultAccess,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize one exact artifact summary and access mode."""
        super().__init__(id=id, classes=classes)
        self._summary = summary
        self._access = access

    @override
    def compose(self) -> ComposeResult:
        """Render the artifact-owned verdict without recomputing it."""
        with Horizontal(classes="result-summary-heading"):
            yield StatusBadge(
                status_for_outcome(self._summary.gate_verdict),
                detail=self._summary.gate_verdict,
            )
            with Vertical(classes="result-summary-copy"):
                yield Label(
                    safe_terminal_text(f"Run {self._summary.run_id}", maximum_characters=256),
                    classes="result-summary-title",
                    markup=False,
                )
                yield Static(
                    f"{self._summary.completion} · {self._access.value} · "
                    f"{self._summary.coverage_issues} coverage issues",
                    classes="result-summary-subtitle",
                    markup=False,
                )
        yield Static(
            _summary_metrics(self._summary),
            classes="result-summary-metrics",
            markup=False,
        )


def _summary_metrics(summary: ResultSummaryModel) -> str:
    dimensions = (
        ("executions", summary.executions),
        ("comparisons", summary.comparisons),
        ("assertions", summary.assertions),
        ("diffs", summary.diffs),
    )
    parts = []
    for name, metrics in dimensions:
        values = ", ".join(f"{item.name} {item.value}" for item in metrics) or "none"
        parts.append(f"{name}: {values}")
    return safe_terminal_text(" · ".join(parts), maximum_characters=2048)


@final
class ResultIndexItem(ListItem):
    """One result row retaining a typed cross-navigation reference."""

    def __init__(self, reference: ResultReference, outcome: str, label: str) -> None:
        """Initialize a row from one typed reference and exact outcome."""
        self.reference = reference
        with_status = Horizontal(
            StatusBadge(status_for_outcome(outcome)),
            Label(safe_terminal_text(label, maximum_characters=512), markup=False),
            classes="result-index-row",
        )
        super().__init__(with_status)


@final
class ResultIndex(ListView):
    """Shared outcome index used identically by live and persisted results."""

    @final
    class ResultSelected(Message):
        """A result row was selected for shared detail inspection."""

        def __init__(self, reference: ResultReference) -> None:
            """Retain the selected typed result reference."""
            super().__init__()
            self.reference = reference

    def __init__(
        self,
        projection: ResultProjection,
        *,
        kind: ResultIndexKind = ResultIndexKind.REQUESTS,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize one of the four result indexes."""
        if type(kind) is not ResultIndexKind:
            raise TypeError("result index kind must be typed")
        self.projection = projection
        self.kind = kind
        super().__init__(*self._items(), id=id, classes=classes)

    def _items(self) -> tuple[ResultIndexItem, ...]:
        if self.kind in {ResultIndexKind.REQUESTS, ResultIndexKind.TIMELINE}:
            return tuple(_execution_item(item) for item in self.projection.executions)
        if self.kind is ResultIndexKind.ISSUES:
            facts = tuple(
                item
                for item in (*self.projection.assertions, *self.projection.diffs)
                if is_issue(item)
            )
            if facts:
                return tuple(_fact_item(item) for item in facts)
            return tuple(_comparison_item(item) for item in self.projection.comparisons)
        return tuple(
            _fact_item(item, include_location=self.kind is ResultIndexKind.FIELDS)
            for item in (*self.projection.assertions, *self.projection.diffs)
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Expose framework selection as one typed result reference."""
        if type(event.item) is ResultIndexItem:
            self.post_message(self.ResultSelected(event.item.reference))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Update analysis detail immediately as the native cursor moves."""
        if type(event.item) is ResultIndexItem:
            self.post_message(self.ResultSelected(event.item.reference))


def _execution_item(row: ExecutionResultRow) -> ResultIndexItem:
    return ResultIndexItem(
        row.reference,
        row.outcome,
        (
            f"{row.subject.resource_id} · {row.environment.resource_id} · "
            f"{row.cell_id} · {row.outcome}"
        ),
    )


def _fact_item(fact: ResultFact, *, include_location: bool = False) -> ResultIndexItem:
    location = ""
    if include_location and fact.evidence:
        location = f" · {fact.evidence[0].location}"
    return ResultIndexItem(
        fact.reference,
        fact.outcome,
        f"{fact.effective_rule_id} · {fact.severity} · {fact.outcome}{location}",
    )


def _comparison_item(row: ComparisonResultRow) -> ResultIndexItem:
    return ResultIndexItem(
        row.reference,
        row.outcome,
        f"{row.subject.resource_id} · {row.cell_id} · {row.outcome}",
    )


@final
class ResultDetail(Static):
    """One evidence-light manifest detail that never loads detached bodies."""

    def __init__(
        self,
        projection: ResultProjection,
        reference: ResultReference | None = None,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize detail over one immutable result projection."""
        super().__init__("", id=id, classes=classes, markup=False)
        self._projection = projection
        self.show_reference(reference)

    def show_reference(self, reference: ResultReference | None) -> None:
        """Inspect one exact projected entity through its stable reference."""
        if reference is None:
            self.update("Select a result row to inspect it.")
            return
        lines = _detail_lines(self._projection, reference)
        relationships = tuple(
            (f"Related {link.relation.value}: {link.target.entity.value}/{link.target.result_id}")
            for link in self._projection.links
            if link.source == reference
        )
        self.update(
            "\n".join(
                safe_terminal_text(item, maximum_characters=2048, multiline=False)
                for item in (*lines, *relationships)
            )
        )


def _detail_lines(
    projection: ResultProjection,
    reference: ResultReference,
) -> tuple[str, ...]:
    execution = next((item for item in projection.executions if item.reference == reference), None)
    if execution is not None:
        timings = ", ".join(
            (
                f"{item.name} {item.nanoseconds} ns"
                if item.nanoseconds is not None
                else f"{item.name} unavailable ({item.reason})"
            )
            for item in execution.timing.measurements
        )
        return (
            f"{execution.subject.kind}/{execution.subject.resource_id}",
            f"Environment: {execution.environment.resource_id}",
            f"Cell: {execution.cell_id} · trial {execution.trial_id}",
            f"Outcome: {execution.outcome}",
            f"Timings: {timings or 'none'}",
            (f"Attempts: {execution.timing.attempts} · exchanges: {execution.timing.exchanges}"),
        )
    fact = next(
        (
            item
            for item in (*projection.assertions, *projection.diffs)
            if item.reference == reference
        ),
        None,
    )
    if fact is not None:
        evidence = tuple(f"  {item.location:<34} {item.outcome}" for item in fact.evidence) or (
            "  No manifest-resident evidence",
        )
        counts = ", ".join(f"{item.name}={item.value}" for item in fact.counts) or "none"
        coverage = ", ".join(f"{item.name}={item.value}" for item in fact.coverage) or "none"
        return (
            f"{fact.engine} rule {fact.effective_rule_id}",
            f"Severity: {fact.severity} · outcome: {fact.outcome}",
            f"Rule source: {fact.rule.kind}/{fact.rule.resource_id}",
            f"Counts: {counts}",
            f"Coverage: {coverage}",
            "",
            "EVIDENCE",
            *evidence,
            f"Evidence omitted: {fact.evidence_omitted}",
        )
    comparison = next(
        (item for item in projection.comparisons if item.reference == reference),
        None,
    )
    if comparison is not None:
        return (
            f"Comparison {comparison.reference.result_id}",
            f"Subject: {comparison.subject.resource_id} · cell {comparison.cell_id}",
            f"Outcome: {comparison.outcome}",
            (
                f"Baseline: {comparison.baseline.result_id} · "
                f"candidate: {comparison.candidate.result_id}"
            ),
        )
    return ("The selected result is unavailable in this projection.",)


@final
class ResultsWorkspaceView(ResponsiveWidget):
    """The exact shared result composition mounted by Compare and Report."""

    def __init__(
        self,
        projection: ResultProjection,
        *,
        access: ResultAccess,
        index: ResultIndexKind = ResultIndexKind.ISSUES,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the shared live or read-only result composition."""
        super().__init__(id=id, classes=classes)
        self._projection = projection
        self._access = access
        self._index = index if index in analysis_lenses() else ResultIndexKind.ISSUES
        self._selected_reference: ResultReference | None = None

    @override
    def compose(self) -> ComposeResult:
        """Compose outcome → issue → occurrence → evidence for every result origin."""
        yield ResultSummary(self._projection.summary, access=self._access)
        yield ResultIndexSelector(self._index, classes="result-index-selector")
        with Horizontal(classes="result-layout"):
            with Vertical(classes="result-index-region"):
                yield Label(lens_heading(self._index), classes="result-list-heading")
                yield Static(lens_summary(self._projection, self._index), markup=False)
                yield ResultIndex(self._projection, kind=self._index, classes="result-index")
            yield ResultDetail(
                self._projection,
                self._selected_reference,
                classes="result-detail",
            )

    @property
    def current_index(self) -> ResultIndexKind:
        """Return the result collection currently visible in this workspace."""
        return self._index

    @property
    def selected_reference(self) -> ResultReference | None:
        """Return the exact result currently represented by the detail surface."""
        return self._selected_reference

    @on(ResultIndexSelector.IndexSelected)
    def show_index(self, message: ResultIndexSelector.IndexSelected) -> None:
        """Switch collections without discarding the inspected result context."""
        self._index = message.kind
        self.refresh(recompose=True)

    @on(ResultIndex.ResultSelected)
    def show_selected_result(self, message: ResultIndex.ResultSelected) -> None:
        """Route index selection to the one shared detail component."""
        self._selected_reference = message.reference
        self.query_one(".result-detail", ResultDetail).show_reference(message.reference)

    def follow_reference(self, reference: ResultReference) -> None:
        """Follow a typed cross-reference to its owning collection and detail."""
        if type(reference) is not ResultReference:
            raise TypeError("result navigation requires ResultReference")
        self._index = ResultIndexKind.for_reference(reference)
        self._selected_reference = reference
        if self.is_mounted:
            self.refresh(recompose=True)

    def clear_reference(self) -> None:
        """Return to the collection index without retaining a stale detail selection."""
        self._selected_reference = None
        if self.is_mounted:
            self.query_one(".result-detail", ResultDetail).show_reference(None)


__all__ = (
    "ResultDetail",
    "ResultIndex",
    "ResultIndexItem",
    "ResultIndexKind",
    "ResultIndexSelector",
    "ResultSummary",
    "ResultsWorkspaceView",
)
