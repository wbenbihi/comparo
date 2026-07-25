"""Reusable result summary, index, detail, and workspace components."""

from __future__ import annotations

from enum import StrEnum
from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Button
from textual.widgets import Label
from textual.widgets import ListItem
from textual.widgets import ListView
from textual.widgets import Static

from comparo.application.results import ComparisonResultRow
from comparo.application.results import ExecutionResultRow
from comparo.application.results import ResultEntity
from comparo.application.results import ResultFact
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.application.results import ResultSummary as ResultSummaryModel
from comparo.tui.components.base import ResponsiveWidget
from comparo.tui.components.status import StatusBadge
from comparo.tui.components.status import status_for_outcome
from comparo.tui.contracts import ResultAccess
from comparo.tui.presentation import safe_terminal_text


class ResultIndexKind(StrEnum):
    """The four artifact-owned result collections available for navigation."""

    EXECUTIONS = "executions"
    ASSERTIONS = "assertions"
    COMPARISONS = "comparisons"
    DIFFS = "diffs"

    @classmethod
    def for_reference(cls, reference: ResultReference) -> ResultIndexKind:
        """Return the collection containing one typed result reference."""
        return {
            ResultEntity.EXECUTION: cls.EXECUTIONS,
            ResultEntity.ASSERTION: cls.ASSERTIONS,
            ResultEntity.COMPARISON: cls.COMPARISONS,
            ResultEntity.DIFF: cls.DIFFS,
        }[reference.entity]


@final
class ResultIndexSelector(Horizontal):
    """Shared control exposing every result collection in live and stored runs."""

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
        super().__init__(id=id, classes=classes)
        self._kind = kind

    @property
    def kind(self) -> ResultIndexKind:
        """Return the currently active result collection."""
        return self._kind

    @override
    def compose(self) -> ComposeResult:
        """Compose all result collections from the closed kind vocabulary."""
        for kind in ResultIndexKind:
            classes = "result-index-option"
            if kind is self._kind:
                classes += " result-index-active"
            yield Button(
                kind.value.title(),
                id=f"result-index-{kind.value}",
                classes=classes,
            )

    @on(Button.Pressed)
    def select_index(self, event: Button.Pressed) -> None:
        """Translate one shared control press into a typed index message."""
        prefix = "result-index-"
        button_id = event.button.id or ""
        if not button_id.startswith(prefix):
            return
        kind = ResultIndexKind(button_id.removeprefix(prefix))
        self._kind = kind
        for button in self.query(Button):
            button.set_class(button.id == button_id, "result-index-active")
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
            yield Static(
                safe_terminal_text(
                    f"Run {self._summary.run_id} · {self._access.value}",
                    maximum_characters=256,
                ),
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
        kind: ResultIndexKind = ResultIndexKind.EXECUTIONS,
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
        if self.kind is ResultIndexKind.EXECUTIONS:
            return tuple(_execution_item(item) for item in self.projection.executions)
        if self.kind is ResultIndexKind.ASSERTIONS:
            return tuple(_fact_item(item) for item in self.projection.assertions)
        if self.kind is ResultIndexKind.COMPARISONS:
            return tuple(_comparison_item(item) for item in self.projection.comparisons)
        return tuple(_fact_item(item) for item in self.projection.diffs)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Expose framework selection as one typed result reference."""
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


def _fact_item(fact: ResultFact) -> ResultIndexItem:
    return ResultIndexItem(
        fact.reference,
        fact.outcome,
        f"{fact.effective_rule_id} · {fact.severity} · {fact.outcome}",
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
        evidence = ", ".join(item.location for item in fact.evidence) or "none"
        return (
            f"{fact.engine} rule {fact.effective_rule_id}",
            f"Severity: {fact.severity} · outcome: {fact.outcome}",
            f"Evidence: {evidence}",
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
        index: ResultIndexKind = ResultIndexKind.EXECUTIONS,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the shared live or read-only result composition."""
        super().__init__(id=id, classes=classes)
        self._projection = projection
        self._access = access
        self._index = index
        self._selected_reference: ResultReference | None = None

    @override
    def compose(self) -> ComposeResult:
        """Compose one summary/index/detail hierarchy for every result origin."""
        yield ResultSummary(self._projection.summary, access=self._access)
        yield ResultIndexSelector(self._index, classes="result-index-selector")
        with Horizontal(classes="result-layout"):
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
