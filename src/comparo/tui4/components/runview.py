"""Live execution table and inspectors over safe application progress snapshots."""

from time import monotonic_ns
from typing import override

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.content import Content
from textual.events import Resize

from comparo.application import PlanReviewInspection
from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import LiveOccurrenceProgress
from comparo.tui4.components.groupedtable import GroupedTable
from comparo.tui4.components.groupedtable import RecordGroup
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.resourcelabel import resource_label
from comparo.tui4.components.status import compact_facts
from comparo.tui4.components.status import duration
from comparo.tui4.components.status import fact_counts
from comparo.tui4.components.status import status_text
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Caption
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.theme import style


class LiveRunView(Vertical):
    """Keep evolving execution rows, recent events and selection independently stable."""

    DEFAULT_CSS = """
    LiveRunView { height: 1fr; width: 1fr; }
    LiveRunView > Caption { height: auto; padding: 0 1; }
    #run-calls { height: 2fr; min-height: 5; }
    #run-inspectors { height: 1fr; min-height: 5; }
    #run-inspectors Panel { width: 1fr; }
    #run-inspectors Caption { height: auto; }
    """

    def __init__(self, *, id: str | None = None) -> None:
        """Initialize without a run; no fixture state is displayed."""
        super().__init__(id=id)
        self.snapshot: ExecutionProgressSnapshot | None = None
        self.catalog: ExplorerCatalog | None = None
        self.review: PlanReviewInspection | None = None
        self.groups: dict[str, tuple[LiveOccurrenceProgress | None, ...]] = {}
        self.started = monotonic_ns()

    @override
    def compose(self) -> ComposeResult:
        """Follow the reference's statistics, execution table and lower inspectors."""
        yield Caption("", id="run-scope", markup=False)
        yield Caption("", id="run-progress", markup=False)
        yield Caption("", id="run-statistics", markup=False)
        yield Caption("", id="run-validation", markup=False)
        yield Caption(
            "Requests & matrix cases  ·  Space / Enter expand · ← collapse", id="run-table-title"
        )
        yield GroupedTable(
            columns=(
                Column("request", "Request / case", 32),
                Column("state", "State", 12),
                Column("baseline", "Baseline", 18),
                Column("candidate", "Candidate", 18),
                Column("assertions", "Assertions", 20),
                Column("diff", "Diff", 18),
                Column("elapsed", "Duration", 16),
            ),
            id="run-calls",
        )
        yield Caption("", id="run-coverage", markup=False)
        with Horizontal(id="run-inspectors"):
            with Panel(title="Selected execution"):
                yield Caption(
                    "Select a request to inspect its progress.", id="run-detail", markup=False
                )
            with Panel(title="Recent activity", id="run-activity-panel"):
                yield Caption("", id="run-events", markup=False)

    def begin(
        self, catalog: ExplorerCatalog | None, review: PlanReviewInspection | None = None
    ) -> None:
        """Reset the retained table only when starting a different run."""
        self.catalog = catalog
        self.review = review
        self.groups.clear()
        self.started = monotonic_ns()
        self.snapshot = None
        if review is not None:
            plan = review.plan
            self.query_one("#run-scope", Caption).update(
                f"{len(plan.slices)} requests · {plan.cardinality.cells} cells · "
                f"{plan.cardinality.environment_executions} planned calls · "
                f"{plan.cardinality.comparisons} comparisons"
            )
        self.query_one(GroupedTable).set_groups((), reset=True)

    def update_snapshot(self, snapshot: ExecutionProgressSnapshot) -> None:
        """Project bounded facts without retaining runtime observations or stealing focus."""
        self.snapshot = snapshot
        calls = tuple(item for item in snapshot.occurrences if item.execution is not None)
        table = self.query_one(GroupedTable)
        table.set_groups(self._request_groups(self._group_records(calls)))
        done, total = snapshot.finished_executions, snapshot.total_executions
        queued = max(0, snapshot.queued_executions - snapshot.started_executions)
        self.query_one("#run-statistics", Caption).update(
            f"Elapsed {duration(monotonic_ns() - self.started)}"
            f"  ·  {snapshot.active_executions} active  ·  {queued} queued"
            f"  ·  {snapshot.finished_comparisons}/{snapshot.total_comparisons} comparisons"
        )
        width = max(12, self.size.width - 24)
        filled = int(width * done / total) if total else 0
        self.query_one("#run-progress", Caption).update(
            Text(
                "━" * filled,
                style("status.running" if snapshot.active_executions else "status.passed"),
            )
            + Text("─" * (width - filled), style("dim"))
            + Text(f"  {done}/{total} · {int(100 * done / total) if total else 0}%")
        )
        self.query_one("#run-validation", Caption).update(
            Content("Assertions: ")
            + fact_counts(
                fact.outcome.value for fact in snapshot.facts if fact.engine == "assertion"
            )
            + Content(" · Diffs: ")
            + fact_counts(fact.outcome.value for fact in snapshot.facts if fact.engine == "diff")
            + Content(
                f" · Transport errors: {sum(item.transport_failed is True for item in calls)}"
            )
        )
        self.query_one("#run-coverage", Caption).update(
            f"{len(calls)} calls retained"
            f" · {snapshot.omitted_occurrences} occurrences outside live window"
            f" · {snapshot.omitted_facts} omitted facts"
            f" · {snapshot.incomplete_fact_scopes} incomplete evaluations"
        )
        identities = {
            item.occurrence_id: item.execution.subject.resource_id
            for item in calls
            if item.execution is not None
        }
        self.query_one("#run-events", Caption).update(
            "\n".join(
                f"{event.kind.value.replace('-', ' ')}  "
                f"{identities.get(event.occurrence_id or '', '')} "
                f"{event.outcome.value if event.outcome else ''}"
                for event in snapshot.recent_events[-6:]
            )
        )
        self._detail(table.cursor_row_id)

    def _group_records(self, calls: tuple[LiveOccurrenceProgress, ...]) -> tuple[Record[str], ...]:
        cells: dict[tuple[str, str, str], dict[str, LiveOccurrenceProgress]] = {}
        for item in calls:
            execution = item.execution
            if execution is not None:
                key = (execution.subject.resource_id, execution.cell_id, execution.trial_id)
                cells.setdefault(key, {})[execution.environment.resource_id] = item
        records: list[Record[str]] = []
        self.groups = {}
        for (subject, cell, trial), environments in cells.items():
            slice_ = (
                next(
                    (
                        item
                        for item in self.review.plan.slices
                        if item.subject.resource_id == subject
                    ),
                    None,
                )
                if self.review
                else None
            )
            pairs = (
                tuple(
                    (pair.baseline.resource_id, pair.candidate.resource_id)
                    for pair in slice_.comparisons
                )
                if slice_
                else ()
            )
            lanes: tuple[tuple[str, ...], ...] = pairs or tuple(
                (identity,) for identity in environments
            )
            for names in lanes:
                row_key = f"{cell}/{trial}/{'/'.join(names)}"
                group = tuple(environments.get(identity) for identity in names)
                if not any(item is not None for item in group):
                    continue
                self.groups[row_key] = group
                first = next(item for item in group if item is not None)
                records.append(self._group_record(row_key, first, group, names))
        return tuple(records)

    def _request_groups(self, records: tuple[Record[str], ...]) -> tuple[RecordGroup, ...]:
        requests: dict[str, list[Record[str]]] = {}
        for row in records:
            first = next(item for item in self.groups[row.id] if item is not None)
            if first.execution is not None:
                requests.setdefault(first.execution.subject.resource_id, []).append(row)
        groups = []
        for subject, children in requests.items():
            if len(children) == 1:
                groups.append(RecordGroup(children[0]))
                continue
            key = f"request:{subject}"
            calls = tuple(
                item for row in children for item in self.groups[row.id] if item is not None
            )
            self.groups[key] = calls
            first = calls[0]
            owners = {item.occurrence_id for item in calls}
            baseline = tuple(self.groups[row.id][0] for row in children)
            candidate = tuple(
                self.groups[row.id][1] for row in children if len(self.groups[row.id]) > 1
            )
            groups.append(
                RecordGroup(
                    Record(
                        key,
                        (
                            self._request_label(first) + Content(f" · {len(children)} cases"),
                            self._state(calls),
                            self._lane_summary(baseline),
                            self._lane_summary(candidate),
                            self._facts(owners, "assertion"),
                            self._diffs(owners),
                            self._times(calls),
                        ),
                    ),
                    tuple(
                        Record(
                            row.id,
                            (
                                row.cells[0].split("\n")[-1]
                                if isinstance(row.cells[0], Content)
                                else row.cells[0],
                                *row.cells[1:],
                            ),
                        )
                        for row in children
                    ),
                )
            )
        return tuple(groups)

    def _request_label(self, item: LiveOccurrenceProgress) -> Content:
        if item.execution is None:
            return Content("Request")
        if self.catalog is not None:
            entry = next(
                (
                    entry
                    for entry in self.catalog.entries.values()
                    if entry.summary.coordinate == item.execution.subject
                ),
                None,
            )
            if entry is not None:
                return Content.from_rich_text(resource_label(entry)).split("[")[0]
        return Content(item.execution.subject.resource_id)

    @staticmethod
    def _state(calls: tuple[LiveOccurrenceProgress | None, ...]) -> Content:
        retained = tuple(item for item in calls if item is not None)
        if any(item.state.value == "running" for item in retained):
            return Content.from_rich_text(status_text("running"))
        if any(item.transport_failed for item in retained):
            return Content.from_rich_text(status_text("error"))
        if any(item.state.value == "queued" for item in retained) or len(retained) < len(calls):
            return Content.from_rich_text(status_text("queued"))
        terminal = next(
            (
                item.outcome.value
                for item in retained
                if item.outcome and item.outcome.value != "observed"
            ),
            "completed",
        )
        return Content.from_rich_text(status_text(terminal))

    @staticmethod
    def _lane_summary(calls: tuple[LiveOccurrenceProgress | None, ...]) -> Content:
        if not calls:
            return Content("—")
        done = sum(item is not None and item.outcome is not None for item in calls)
        active = sum(item is not None and item.state.value == "running" for item in calls)
        return Content(f"{done}/{len(calls)} done" + (f" · {active} active" if active else ""))

    def _facts(self, owners: set[str], engine: str) -> Content:
        return (
            compact_facts(
                fact.outcome.value
                for fact in self.snapshot.facts
                if fact.owner_id in owners and fact.engine == engine
            )
            if self.snapshot
            else Content("—")
        )

    def _diffs(self, owners: set[str]) -> Content:
        comparisons = (
            {
                item.occurrence_id
                for item in self.snapshot.occurrences
                if item.comparison is not None
                and {item.comparison.baseline_execution_id, item.comparison.candidate_execution_id}
                <= owners
            }
            if self.snapshot
            else set()
        )
        return self._facts(comparisons, "diff")

    @staticmethod
    def _times(calls: tuple[LiveOccurrenceProgress | None, ...]) -> str:
        values = [
            item.elapsed_nanoseconds
            for item in calls
            if item is not None and item.elapsed_nanoseconds is not None
        ]
        return duration(max(values)) if values else "—"

    def _group_record(
        self,
        key: str,
        first: LiveOccurrenceProgress,
        group: tuple[LiveOccurrenceProgress | None, ...],
        names: tuple[str, ...],
    ) -> Record[str]:
        execution = first.execution
        if execution is None or self.snapshot is None:
            raise ValueError("live group requires exact execution coordinates")
        case = " · ".join(axis.case_id for axis in execution.matrices) or "Default case"
        if execution.trial_ordinal:
            case += f" · trial {execution.trial_ordinal + 1}"
        owners = {item.occurrence_id for item in group if item is not None}
        return Record(
            key,
            (
                self._request_label(first) + Content("\n" + case),
                self._state(group),
                self._lane(group[0], names[0]).split("\n")[0],
                self._lane(group[1], names[1]).split("\n")[0] if len(group) == 2 else Content("—"),
                self._facts(owners, "assertion"),
                self._diffs(owners),
                " / ".join(duration(item.elapsed_nanoseconds) if item else "—" for item in group),
            ),
        )

    @staticmethod
    def _lane(item: LiveOccurrenceProgress | None, name: str) -> Content:
        if item is None:
            return Content(f"{name} · pending")
        state = (
            "error"
            if item.transport_failed
            else item.outcome.value
            if item.outcome
            else item.state.value
        )
        elapsed = item.elapsed_nanoseconds
        if item.state.value == "running" and item.started_at_nanoseconds is not None:
            elapsed = monotonic_ns() - item.started_at_nanoseconds
        text = status_text(state)
        text.append(f" {item.http_status}" if item.http_status is not None else "")
        text.append(f"\n{duration(elapsed)} · {name}")
        return Content.from_rich_text(text)

    @on(Table.RowHighlighted)
    def _highlighted(self, event: Table.RowHighlighted[str]) -> None:
        event.stop()
        self._detail(event.row)

    def _detail(self, identity: str | None) -> None:
        if self.snapshot is None:
            return
        group = self.groups.get(identity or "", ())
        calls = tuple(item for item in group if item is not None and item.execution is not None)
        if not calls:
            return
        first = calls[0].execution
        if first is None:
            return
        owners = {item.occurrence_id for item in calls}
        text = Text()
        text.append(first.subject.resource_id, style("bright"))
        if len(calls) > 2:
            text.append(f" · {len(calls)} calls · request summary\n", style("muted"))
            text.append("Space expands this request into its matrix cases.\n\n", style("muted"))
            for environment in dict.fromkeys(
                item.execution.environment.resource_id for item in calls if item.execution
            ):
                lane = tuple(
                    item
                    for item in calls
                    if item.execution and item.execution.environment.resource_id == environment
                )
                text.append(f"{environment:<15}", style("label"))
                text.append(self._lane_summary(lane).plain + "\n")
        else:
            case = " · ".join(f"{axis.binding}={axis.case_id}" for axis in first.matrices)
            text.append(
                " / " + (case or "Default case") + f" · trial {first.trial_ordinal + 1}\n\n"
            )
            for index, item in enumerate(calls):
                if item.execution is not None:
                    role = (
                        "baseline"
                        if index == 0 and len(calls) == 2
                        else "candidate"
                        if len(calls) == 2
                        else "fg"
                    )
                    text.append(f"{item.execution.environment.resource_id:<15}", style(role))
                    text.append_text(
                        Table._cell(
                            self._lane(item, item.execution.environment.resource_id).split("\n")[0]
                        )
                    )
                    elapsed = item.elapsed_nanoseconds
                    if item.state.value == "running" and item.started_at_nanoseconds is not None:
                        elapsed = monotonic_ns() - item.started_at_nanoseconds
                    text.append(f" · {duration(elapsed)}\n")
                    if item.failure_code:
                        text.append(
                            f"               {item.failure_stage}: {item.failure_code}\n",
                            style("error-op"),
                        )
        text.append("\nAssertions     ", style("label"))
        text.append_text(Table._cell(self._facts(owners, "assertion")))
        text.append("\nEnvironment Δ  ", style("label"))
        text.append_text(Table._cell(self._diffs(owners)))
        self.query_one("#run-detail", Caption).update(text)
        identities = {item.occurrence_id for item in calls}
        events = [
            event for event in self.snapshot.recent_events if event.occurrence_id in identities
        ]
        self.query_one("#run-events", Caption).update(
            "\n".join(
                f"{duration(max(0, event.observed_at_nanoseconds - self.started))} "
                f" {event.kind.value.replace('-', ' ')}"
                f"  {event.outcome.value if event.outcome else ''}"
                for event in events[-6:]
            )
        )

    def on_resize(self, event: Resize) -> None:
        """Keep the overview and inspectors proportional to the terminal."""
        self.set_class(event.size.width < 88, "compact")
        self.call_after_refresh(self._fit_columns)

    def _fit_columns(self) -> None:
        table = self.query_one(GroupedTable)
        table.fit_proportions((0.25, 0.10, 0.13, 0.13, 0.14, 0.14, 0.11), minimum_width=95)

    @override
    def focus(self, scroll_visible: bool = True) -> "LiveRunView":
        """Route major-region focus to the stable execution cursor."""
        self.query_one(Table).focus(scroll_visible)
        return self
