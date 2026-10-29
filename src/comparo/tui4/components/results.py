"""Reusable analysis surface for sanitized live or archived result projections."""

from typing import ClassVar
from typing import override

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.content import Content
from textual.events import Resize
from textual.message import Message

from comparo.application.artifact_references import ArtifactBodyReference
from comparo.application.results import ComparisonResultRow
from comparo.application.results import ExecutionResultRow
from comparo.application.results import ResultFact
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.application.results import result_value_preview
from comparo.tui.design.breakpoints import LayoutClass
from comparo.tui.design.breakpoints import layout_class
from comparo.tui4.components.actionbar import Action
from comparo.tui4.components.actionbar import ActionBar
from comparo.tui4.components.choicemenu import ChoiceMenu
from comparo.tui4.components.diffview import DiffView
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.groupedtable import GroupedTable
from comparo.tui4.components.groupedtable import RecordGroup
from comparo.tui4.components.menu import Menu
from comparo.tui4.components.menu import MenuItem
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.resultdetail import ResultDetail
from comparo.tui4.components.status import compact_facts
from comparo.tui4.components.status import duration
from comparo.tui4.components.status import fact_counts
from comparo.tui4.components.status import status_cell
from comparo.tui4.components.status import status_text
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Caption
from comparo.tui4.fragments import chip
from comparo.tui4.fragments import glyph
from comparo.tui4.models.diff import DiffDocument
from comparo.tui4.models.results import AnalysisEntry
from comparo.tui4.models.results import ResultItem
from comparo.tui4.models.results import analysis_entries
from comparo.tui4.models.results import field_location_label
from comparo.tui4.models.results import index_results
from comparo.tui4.models.results import item_label
from comparo.tui4.theme import kind_glyph
from comparo.tui4.theme import style

LENSES = ("issues", "requests", "rules", "fields", "timeline")


class ResultsView(Vertical):
    """Outcome → issue/request → occurrence → evidence with a local back stack."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left_square_bracket", "cycle_lens(-1)", "Previous view", key_display="["),
        Binding("right_square_bracket", "cycle_lens(1)", "Next view", key_display="]"),
        *(
            Binding(f"alt+{index + 1}", f"lens('{lens}')", lens.title(), show=False, priority=True)
            for index, lens in enumerate(LENSES)
        ),
        Binding("/", "focus_filter", "Filter"),
        Binding("g", "groups", "Groups"),
        Binding("o", "occurrences", "Occurrences"),
        Binding("e", "evidence", "Evidence"),
        Binding("escape", "back", "Back"),
    ]
    DEFAULT_CSS = """
    ResultsView { width: 1fr; height: 1fr; }
    #result-headline, #result-counts, #result-coverage { height: auto; padding: 0 1; }
    #result-layout { height: 1fr; }
    #result-index-panel { width: 36; }
    ResultsView.wide #result-index-panel { width: 44; }
    #result-index-panel Table { height: 1fr; }
    #result-detail-panel { width: 1fr; }
    #result-detail { height: auto; }
    #result-related { height: 7; }
    #result-messages { height: 3; }
    #result-body { height: 16; }
    #result-body-status { height: auto; }
    ResultsView.compact #result-layout { layout: vertical; }
    ResultsView.compact #result-index-panel { width: 100%; height: 1fr; }
    ResultsView.compact #result-detail-panel { width: 100%; height: 2fr; }
    """

    class BodyRequested(Message):
        """Request authenticated body reads through the owning workspace's backend."""

        def __init__(
            self, view: "ResultsView", references: tuple[ArtifactBodyReference, ...]
        ) -> None:
            """Retain exact manifest-owned body coordinates, never paths."""
            super().__init__()
            self.view = view
            self.references = references

    def __init__(self, *, id: str | None = None) -> None:
        """Initialize a stable index and detail without a loaded manifest."""
        super().__init__(id=id)
        self.projection: ResultProjection | None = None
        self.items: dict[ResultReference, ResultItem] = {}
        self.entries: dict[str, AnalysisEntry] = {}
        self.links: dict[str, ResultReference] = {}
        self.messages: dict[str, ArtifactBodyReference] = {}
        self.lens = "issues"
        self.lens_counts: dict[str, int] = {}
        self.selected: ResultReference | None = None
        self.history: list[ResultReference] = []
        self.body_references: tuple[ArtifactBodyReference, ...] = ()
        self._reveal_on_body = True
        self.group_key: str | None = None
        self.evidence_links: dict[str, ResultReference] = {}
        self.index_children: dict[str, tuple[str, ResultReference]] = {}
        self.issue_links: dict[str, ResultReference] = {}
        self._body_labels: list[Content] = []

    @override
    def compose(self) -> ComposeResult:
        """Compose the shared library into the reference's analysis hierarchy."""
        yield Caption("Loading sanitized results…", id="result-headline", markup=False)
        yield Caption("", id="result-counts", markup=False)
        yield ActionBar(tuple(Action(lens, lens.title()) for lens in LENSES), id="result-lenses")
        yield Caption("Alt+1-5 views · [ / ] previous/next view", id="result-view-keys")
        with Horizontal(id="result-layout"):
            with Panel(title="Issues", id="result-index-panel"):
                yield FilterInput("Filter this view…", id="result-filter")
                with Vertical(id="result-empty"):
                    yield Caption("", id="result-empty-title", markup=False)
                    yield Caption("", id="result-empty-description", markup=False)
                    yield ActionBar(id="result-empty-actions")
                yield GroupedTable(
                    columns=(Column("entry", "Issue", 32),),
                    row_height=3,
                    id="result-index",
                )
                yield Caption("", id="result-coverage", markup=False)
            with Panel(title="", id="result-detail-panel"):
                yield Caption("", id="result-context", markup=False)
                with VerticalScroll(id="result-scroll"):
                    with Vertical(id="result-overview"):
                        yield Caption("", id="result-group-title", markup=False)
                        yield Caption("", id="result-group-summary", markup=False)
                        yield Caption("", id="result-request-rollup", markup=False)
                        yield ResultDetail(id="result-detail")
                        yield Caption("Top issues", id="result-issues-title", markup=False)
                        yield Menu(id="result-issue-links")
                        yield Caption("Occurrences", id="result-occurrences-title", markup=False)
                        yield Table[str](
                            columns=(
                                Column("related", "Request / case", 26),
                                Column("environment", "Environment", 18),
                                Column("outcome", "Result", 12),
                                Column("http", "HTTP", 9),
                                Column("elapsed", "HTTP time", 16),
                            ),
                            row_height=2,
                            id="result-related",
                        )
                    with Vertical(id="result-evidence"):
                        yield Caption("", id="result-selection", markup=False)
                        yield Caption("", id="result-lanes", markup=False)
                        yield Caption("Related evidence", id="result-links-title", markup=False)
                        yield Menu(id="result-evidence-links")
                        yield ChoiceMenu(id="result-messages")
                        with Horizontal(id="result-body-labels"):
                            yield Caption("", id="result-body-left", markup=False)
                            yield Caption("", id="result-body-right", markup=False)
                        yield DiffView(id="result-body")
                        yield Caption("", id="result-body-status", markup=False)
                yield ActionBar(id="result-detail-actions")

    def reset(self) -> None:
        """Clear terminal evidence before another run begins."""
        self.projection = None
        self.items.clear()
        self.entries.clear()
        self.links.clear()
        self.messages.clear()
        self.history.clear()
        self.selected = None
        self.group_key = None
        self.query_one(FilterInput).value = ""
        self._clear_body()
        for table in self.query(Table):
            table.records = ()
        self.query_one(ResultDetail).item = None
        self.query_one(ResultDetail).context = ""
        self.query_one("#result-headline", Caption).update("Loading sanitized results…")
        self.query_one("#result-counts", Caption).update("")
        self._group(None)

    def load(self, projection: ResultProjection) -> None:
        """Load the sanitized manifest; keep backend and filesystem effects outside."""
        self.projection = projection
        self.items = index_results(projection)
        self.lens_counts = {}
        for name in LENSES:
            entries, omitted = analysis_entries(projection, name)
            self.lens_counts[name] = len(entries) + omitted
        self.history.clear()
        self.selected = None
        summary = projection.summary
        headline = status_text(summary.gate_verdict)
        headline.append(
            f"  ·  {len(analysis_entries(projection, 'issues')[0])} issue groups"
            f"  ·  {len(projection.executions)} calls · {summary.completion}"
        )
        self.query_one("#result-headline", Caption).update(headline)
        counts = Content("")
        baselines = {item.baseline for item in projection.comparisons}
        candidates = {item.candidate for item in projection.comparisons}
        for label, owners in (("Baseline", baselines), ("Candidate", candidates)):
            if owners:
                counts += (
                    Content.styled(f"{label} assertions: ", style(label.lower()))
                    + fact_counts(
                        fact.outcome for fact in projection.assertions if fact.owner in owners
                    )
                    + Content(" · ")
                )
        other = tuple(
            fact.outcome
            for fact in projection.assertions
            if fact.owner not in baselines | candidates
        )
        if other:
            counts += Content("Assertions: ") + fact_counts(other) + Content(" · ")
        counts += Content("Diffs: ") + (
            fact_counts(fact.outcome for fact in projection.diffs)
            if projection.diffs
            else Content("None")
        )
        errors = sum(item.failure is not None for item in projection.executions)
        counts += Content(
            f"\n{len(projection.executions)} calls · {len(projection.comparisons)} comparisons · "
            f"{errors} transport errors · {summary.coverage_issues} coverage issues"
        )
        self.query_one("#result-counts", Caption).update(counts)
        self._show_lens("issues")

    def _show_lens(self, lens: str) -> None:
        if lens != self.lens:
            self.query_one(FilterInput).value = ""
        self.lens = lens
        self.group_key = None
        self.remove_class("inspecting", "body-focus", "detail-focus")
        for name in LENSES:
            self.set_class(name == lens, f"lens-{name}")
        self.selected = None
        self.history.clear()
        self.query_one("#result-index-panel", Panel).border_title = lens.title()
        self.query_one("#result-lenses", ActionBar).actions = tuple(
            Action(name, f"{name.title()} ({self.lens_counts.get(name, 0)})", primary=name == lens)
            for name in LENSES
        )
        self._populate(self.query_one(FilterInput).value)

    def _populate(self, query: str) -> None:
        if self.projection is None:
            return
        self.group_key = None
        entries, omitted = analysis_entries(self.projection, self.lens)
        self.entries = {
            str(index): entry
            for index, entry in enumerate(entries)
            if query.casefold() in f"{entry.label} {entry.outcome} {entry.detail}".casefold()
        }
        if self.lens != "timeline":
            self.entries = dict(
                sorted(self.entries.items(), key=lambda pair: self._entry_priority(pair[1]))
            )
        self._populate_index()
        self._update_empty_state(query)
        coverage = f"{len(self.entries)} groups shown · {omitted} outside this window"
        if self.lens == "fields":
            evidence_omitted = sum(
                fact.evidence_omitted
                for fact in (*self.projection.assertions, *self.projection.diffs)
            )
            coverage += f" · {evidence_omitted} evidence records omitted from report"
        self.query_one("#result-coverage", Caption).update(coverage)
        self._group(next(iter(self.entries), None))

    def _update_empty_state(self, query: str) -> None:
        empty = not self.entries
        self.set_class(empty, "empty-index")
        self.query_one("#result-empty").display = empty
        self.query_one("#result-index", Table).display = not empty
        if not empty:
            return
        actions: tuple[Action, ...] = (
            Action("requests", "Browse requests", key="Alt+2", primary=True),
            Action("rules", "Browse rules", key="Alt+3"),
        )
        if query:
            title = "No matching entries"
            description = "No results in this view match your filter. Clear it to see all entries."
            actions = (Action("clear-filter", "Clear filter", primary=True),)
        elif self.lens == "fields":
            title = "No field evidence retained"
            description = (
                "Fields groups the evidence locations recorded by assertion and diff rules. "
                "This report contains no retained field evidence.\n\n"
                "Open Requests to inspect captured responses, "
                "or Rules to review recorded outcomes. "
                "The omitted-evidence count below shows the report's coverage."
            )
        elif self.lens == "issues":
            title = "No retained issues"
            description = "Browse Requests or Rules to inspect the recorded results and coverage."
        else:
            title = f"No {self.lens} recorded"
            description = "This report has no entries for this view."
            actions = ()
        self.query_one("#result-empty-title", Caption).update(title)
        self.query_one("#result-empty-description", Caption).update(description)
        self.query_one("#result-empty-actions", ActionBar).actions = actions

    def _entry_priority(self, entry: AnalysisEntry) -> int:
        references = set(entry.references)
        if self.lens == "requests" and self.projection:
            references.update(
                row.reference
                for row in self.projection.comparisons
                if row.baseline in references or row.candidate in references
            )
        facts = [
            item
            for item in self.items.values()
            if isinstance(item, ResultFact)
            and (item.reference in references or item.owner in references)
        ]
        return 0 if any(fact.outcome in {"violated", "drifted", "error"} for fact in facts) else 1

    def _entry_title(self, entry: AnalysisEntry) -> str:
        if self.lens == "fields" and self.projection is not None:
            return field_location_label(entry.label, self.projection)
        if self.lens != "issues" or not entry.references:
            return entry.label
        item = self.items.get(entry.references[0])
        if isinstance(item, ResultFact):
            if item.engine == "diff":
                return (
                    "Responses differ"
                    if item.outcome == "drifted"
                    else f"Comparison {item.outcome}"
                )
            owner = self._owner(item)
            role = self._role(owner).title() if isinstance(owner, ExecutionResultRow) else ""
            return f"{role or 'Request'} assertion {item.outcome}"
        return "Transport error"

    def _populate_index(self) -> None:
        table = self.query_one("#result-index", GroupedTable)
        self.index_children.clear()
        schemas = {
            "issues": (Column("entry", "Issue"), Column("impact", "Impact")),
            "requests": (
                Column("entry", "Request / case"),
                Column("baseline", "Baseline ✓"),
                Column("candidate", "Candidate ✓"),
                Column("diff", "Diff"),
                Column("time", "Max HTTP"),
            ),
            "rules": (Column("entry", "Rule / source"), Column("result", "Evaluations")),
            "fields": (Column("entry", "Evidence path"), Column("result", "Result / scope")),
            "timeline": (
                Column("entry", "Event"),
                Column("context", "Request / environment"),
                Column("result", "Outcome"),
            ),
        }
        table.configure(
            schemas[self.lens], row_height=3 if self.lens in {"issues", "fields"} else 2
        )
        groups = []
        for key, entry in self.entries.items():
            items = tuple(self.items[ref] for ref in entry.references if ref in self.items)
            title = Content.styled(entry.label, style("bright"))
            children: tuple[Record[str], ...] = ()
            if self.lens == "requests":
                references = self._group_references(entry)
                execution = next(
                    (row for row in items if isinstance(row, ExecutionResultRow)), None
                )
                if execution:
                    title_cell = self._occurrence_cells(execution)[0]
                    title = (
                        title_cell.split("\n")[0]
                        if isinstance(title_cell, Content)
                        else Content(title_cell)
                    )
                cells = (
                    title + Content(f"\n{len(references)} cases"),
                    *self._request_metrics(references),
                )
                child_rows = []
                for index, reference in enumerate(references):
                    child_key = f"case:{key}:{index}"
                    self.index_children[child_key] = (key, reference)
                    row = self.items[reference]
                    owner = self._owner(row)
                    case_execution = (
                        self.items.get(owner.baseline)
                        if isinstance(owner, ComparisonResultRow)
                        else owner
                    )
                    label = (
                        self._case_label(case_execution, compact=True)
                        if isinstance(case_execution, ExecutionResultRow)
                        else item_label(row)
                    )
                    child_rows.append(
                        Record(child_key, (label, *self._request_metrics((reference,))))
                    )
                children = tuple(child_rows)
            elif self.lens == "rules":
                fact = next((row for row in items if isinstance(row, ResultFact)), None)
                cells = (
                    title + Content("\n" + (fact.rule.resource_id if fact else "")),
                    compact_facts(row.outcome for row in items),
                )
            elif self.lens == "fields":
                cells = (
                    Content.styled(self._entry_title(entry), style("bright")),
                    status_cell(entry.outcome) + Content("\n" + entry.detail),
                )
            elif self.lens == "timeline":
                event_item = items[0] if items else None
                owner = self._owner(event_item) if event_item else None
                context = owner.subject.resource_id if owner else ""
                if isinstance(owner, ExecutionResultRow):
                    context += "\n" + owner.environment.resource_id
                cells = (title, context, status_cell(entry.outcome))
            else:
                if self.lens == "issues":
                    title = Content.styled(self._entry_title(entry), style("bright")) + Content(
                        "\n" + entry.label
                    )
                cells = (title + Content("\n" + entry.detail), status_cell(entry.outcome))
            groups.append(RecordGroup(Record(key, cells), children))
        table.set_groups(tuple(groups), reset=True)
        self.call_after_refresh(self._fit_tables)

    def _request_metrics(
        self, references: tuple[ResultReference, ...]
    ) -> tuple[Content | str, ...]:
        baseline: set[ResultReference] = set()
        candidate: set[ResultReference] = set()
        diffs: set[ResultReference] = set()
        executions = []
        for reference in references:
            row = self.items[reference]
            if isinstance(row, ComparisonResultRow):
                baseline.add(row.baseline)
                candidate.add(row.candidate)
                diffs.update(row.diffs)
            elif isinstance(row, ExecutionResultRow):
                baseline.add(reference)
        for reference in baseline | candidate:
            execution_item = self.items.get(reference)
            if isinstance(execution_item, ExecutionResultRow):
                executions.append(execution_item)
        times = [
            timing.nanoseconds
            for row in executions
            for attempt in row.attempts
            if attempt.terminal
            for exchange in attempt.exchanges
            if exchange.terminal
            for timing in exchange.timings
            if timing.name == "elapsed" and timing.nanoseconds is not None
        ]
        return (
            compact_facts(
                fact.outcome
                for fact in self.items.values()
                if isinstance(fact, ResultFact) and fact.owner in baseline
            ),
            compact_facts(
                fact.outcome
                for fact in self.items.values()
                if isinstance(fact, ResultFact) and fact.owner in candidate
            ),
            compact_facts(self.items[ref].outcome for ref in diffs if ref in self.items),
            duration(max(times)) if times else "—",
        )

    def _top_issues(self, references: tuple[ResultReference, ...]) -> None:
        owners = set(references)
        for reference in references:
            row = self.items.get(reference)
            if isinstance(row, ComparisonResultRow):
                owners.update((row.baseline, row.candidate))
        facts = [
            fact
            for fact in self.items.values()
            if isinstance(fact, ResultFact)
            and fact.owner in owners
            and fact.outcome in {"violated", "drifted", "error"}
        ]
        grouped: dict[str, list[ResultFact]] = {}
        for fact in facts:
            grouped.setdefault(fact.rule_id, []).append(fact)
        self.issue_links = {key: values[0].reference for key, values in grouped.items()}
        self.query_one("#result-issue-links", Menu).items = tuple(
            MenuItem(key, f"{key} · {len(values)} occurrences · {values[0].severity}")
            for key, values in grouped.items()
        )
        self.query_one("#result-issues-title", Caption).update(
            "Top issues" if facts else "No retained rule failures in this request"
        )
        visible = self.lens == "requests"
        self.query_one("#result-issues-title").display = visible
        self.query_one("#result-issue-links").display = visible and bool(facts)

    @on(Menu.Selected, "#result-issue-links")
    def _open_issue(self, event: Menu.Selected) -> None:
        event.stop()
        if event.item in self.issue_links:
            self.open_reference(self.issue_links[event.item])

    def _group_references(self, entry: AnalysisEntry) -> tuple[ResultReference, ...]:
        if self.lens != "requests" or self.projection is None:
            return entry.references
        executions = set(entry.references)
        pairs = tuple(
            row
            for row in self.projection.comparisons
            if row.baseline in executions or row.candidate in executions
        )
        paired = {reference for row in pairs for reference in (row.baseline, row.candidate)}
        return (
            *tuple(row.reference for row in pairs),
            *tuple(ref for ref in entry.references if ref not in paired),
        )

    def _group(self, key: str | None) -> None:
        if key is not None and key == self.group_key and self.selected is not None:
            return
        self.group_key = key
        entry = self.entries.get(key or "")
        self.selected = None
        self.history.clear()
        self._clear_body()
        detail = self.query_one(ResultDetail)
        self.query_one("#result-group-title", Caption).update(
            self._entry_title(entry) if entry else "No matching entries"
        )
        self.query_one("#result-group-summary", Caption).update(entry.detail if entry else "")
        self.query_one("#result-context", Caption).update(self.lens.title())
        self.query_one("#result-selection", Caption).update("")
        self.query_one("#result-lanes", Caption).update("")
        references = self._group_references(entry) if entry else ()
        self._related(references)
        self._top_issues(references)
        rollup = self.query_one("#result-request-rollup", Caption)
        rollup.display = self.lens == "requests"
        if rollup.display:
            metrics = self._request_metrics(references)
            rollup.update(
                Content.styled("Baseline assertions  ", style("baseline"))
                + metrics[0]
                + Content("\n")
                + Content.styled("Candidate assertions ", style("candidate"))
                + metrics[1]
                + Content("\nEnvironment diff     ")
                + metrics[2]
            )
        self.query_one("#result-occurrences-title", Caption).update(
            f"{'Cases' if self.lens == 'requests' else 'Occurrences'} ({len(self.links)})"
            "  ·  ↑↓ select · e evidence"
        )
        if entry is None or not entry.references:
            detail.context = (
                "No retained issues." if self.lens == "issues" else "No matching entries."
            )
            detail.item = None
            self._update_evidence_links(None)
            self.query_one("#result-detail-actions", ActionBar).actions = ()
            return
        table = self.query_one("#result-related", Table)
        table.move_cursor(row=0, scroll=False)
        self.open_reference(next(iter(self.links.values())), remember=False)

    def _related(self, references: tuple[ResultReference, ...]) -> None:
        self.links = {
            f"{ref.entity.value}:{ref.result_id}": ref for ref in references if ref in self.items
        }
        table = self.query_one("#result-related", Table)
        if self.lens == "requests":
            table.configure(
                (
                    Column("case", "Case"),
                    Column("base", "Baseline ✓"),
                    Column("cand", "Candidate ✓"),
                    Column("diff", "Diff"),
                    Column("time", "HTTP time"),
                ),
                row_height=2,
            )
            records = []
            for key, reference in self.links.items():
                owner = self._owner(self.items[reference])
                execution = (
                    self.items.get(owner.baseline)
                    if isinstance(owner, ComparisonResultRow)
                    else owner
                )
                label = (
                    self._case_label(execution, compact=True)
                    if isinstance(execution, ExecutionResultRow)
                    else "Unavailable"
                )
                records.append(Record(key, (label, *self._request_metrics((reference,)))))
            table.records = tuple(records)
        else:
            table.configure(
                (
                    Column("related", "Request / case"),
                    Column("environment", "Environment"),
                    Column("outcome", "Result"),
                    Column("http", "HTTP"),
                    Column("elapsed", "HTTP time"),
                ),
                row_height=2,
            )
            table.records = tuple(
                Record(key, self._occurrence_cells(self.items[ref]))
                for key, ref in self.links.items()
            )
        self.call_after_refresh(self._fit_tables)

    def _occurrence_cells(self, item: ResultItem) -> tuple[Content | str, ...]:
        owner = self.items.get(item.owner) if isinstance(item, ResultFact) else item
        if isinstance(owner, ExecutionResultRow):
            method, status = "", "—"
            for attempt in owner.attempts:
                for exchange in attempt.exchanges:
                    if exchange.request.metadata is not None:
                        method = exchange.request.metadata.method
                    if (
                        attempt.terminal
                        and exchange.terminal
                        and exchange.response.metadata is not None
                    ):
                        status = str(exchange.response.metadata.status)
            label = glyph(*kind_glyph(owner.subject.kind))
            if method:
                label.append_text(chip(method + " ", f"method.{method}"))
            label.append(owner.subject.resource_id)
            elapsed = next(
                (
                    timing.nanoseconds
                    for attempt in owner.attempts
                    if attempt.terminal
                    for exchange in attempt.exchanges
                    if exchange.terminal
                    for timing in exchange.timings
                    if timing.name == "elapsed"
                ),
                None,
            )
            return (
                Content.from_rich_text(label)
                + Content("\n" + self._case_label(owner, compact=True)),
                Content.styled(
                    f"{self._role(owner).title()}\n{owner.environment.resource_id}",
                    style(self._role(owner) or "fg"),
                ),
                status_cell(item.outcome),
                Content.styled(status, style("err" if status.startswith(("4", "5")) else "ok")),
                duration(elapsed),
            )
        if isinstance(owner, ComparisonResultRow):
            baseline, candidate = self.items.get(owner.baseline), self.items.get(owner.candidate)
            if isinstance(baseline, ExecutionResultRow) and isinstance(
                candidate, ExecutionResultRow
            ):
                left, right = self._occurrence_cells(baseline), self._occurrence_cells(candidate)
                return (
                    self._occurrence_cells(baseline)[0],
                    Content.styled(baseline.environment.resource_id, style("baseline"))
                    + Content(" →\n")
                    + Content.styled(candidate.environment.resource_id, style("candidate")),
                    status_cell(item.outcome)
                    if isinstance(item, ResultFact)
                    else fact_counts(
                        self.items[ref].outcome for ref in owner.diffs if ref in self.items
                    ),
                    Content.styled(f"{left[3]}", style("baseline"))
                    + Content(" / ")
                    + Content.styled(f"{right[3]}", style("candidate")),
                    Content(f"{left[4]} / {right[4]}"),
                )
        return (
            Content(item_label(item)),
            Content("Comparison" if isinstance(owner, ComparisonResultRow) else ""),
            status_cell(item.outcome),
            "—",
            "—",
        )

    def open_reference(self, reference: ResultReference, *, remember: bool = True) -> None:
        """Open a stable result identity without re-evaluating any outcome."""
        item = self.items.get(reference)
        if item is None:
            return
        if remember and self.selected is not None and self.selected != reference:
            self.history = [*self.history[-31:], self.selected]
        self.selected = reference
        if remember:
            self.add_class("inspecting")
            self.call_after_refresh(self._fit_tables)
        self._clear_body()
        self.query_one(ResultDetail).context = ""
        self.query_one(ResultDetail).item = item
        self.set_class(isinstance(item, ComparisonResultRow), "comparison-selected")
        self._update_context(item)
        self._update_evidence_links(item)
        if isinstance(item, ExecutionResultRow):
            self.messages = {
                str(index): message.reference
                for index, message in enumerate(item.messages)
                if message.has_body_capture
            }
            self.query_one("#result-messages", ChoiceMenu).items = tuple(
                MenuItem(
                    str(index),
                    f"Attempt {message.attempt_ordinal + 1}"
                    f" · exchange {message.exchange_ordinal + 1}"
                    f" · {message.reference.message.value} body",
                )
                for index, message in enumerate(item.messages)
                if message.has_body_capture
            )
        self.query_one("#result-messages", ChoiceMenu).display = bool(self.messages)
        self.query_one("#result-scroll", VerticalScroll).scroll_home(animate=False)
        if isinstance(item, ResultFact) and self._comparison_for(item) is not None:
            self._compare_bodies(reveal=False)

    def _owner(self, item: ResultItem) -> ExecutionResultRow | ComparisonResultRow | None:
        owner = self.items.get(item.owner) if isinstance(item, ResultFact) else item
        return owner if isinstance(owner, (ExecutionResultRow, ComparisonResultRow)) else None

    def _comparison_for(self, item: ResultItem) -> ComparisonResultRow | None:
        owner = self._owner(item)
        if isinstance(owner, ComparisonResultRow):
            return owner
        if isinstance(owner, ExecutionResultRow) and len(owner.comparisons) == 1:
            comparison = self.items.get(owner.comparisons[0])
            return comparison if isinstance(comparison, ComparisonResultRow) else None
        return None

    def _case_label(self, item: ExecutionResultRow, *, compact: bool = False) -> str:
        cases = (
            " · ".join(
                ("" if compact else f"{coordinate.axis}=")
                + result_value_preview(coordinate.value, maximum_characters=80)
                for coordinate in item.coordinates
            )
            or "Default case"
        )
        trials = (
            tuple(
                dict.fromkeys(
                    row.trial_id
                    for row in self.projection.executions
                    if row.cell_id == item.cell_id
                )
            )
            if self.projection
            else ()
        )
        return cases + (f" · trial {trials.index(item.trial_id) + 1}" if len(trials) > 1 else "")

    def _environment_label(self, item: ExecutionResultRow) -> Content:
        role = self._role(item)
        return Content.styled(
            f"{role.title()}: {item.environment.resource_id}"
            if role
            else item.environment.resource_id,
            style(role or "fg"),
        )

    def _role(self, item: ExecutionResultRow) -> str:
        if self.projection:
            if any(row.baseline == item.reference for row in self.projection.comparisons):
                return "baseline"
            if any(row.candidate == item.reference for row in self.projection.comparisons):
                return "candidate"
        return ""

    def _update_context(self, item: ResultItem) -> None:
        owner = self._owner(item)
        execution = (
            self.items.get(owner.baseline) if isinstance(owner, ComparisonResultRow) else owner
        )
        level = (
            f"{item.engine.title()} rule evidence"
            if isinstance(item, ResultFact)
            else (
                "Environment comparison"
                if isinstance(item, ComparisonResultRow)
                else "HTTP request & response"
            )
        )
        entry = self.entries.get(self.group_key or "")
        self.query_one("#result-context", Caption).update(
            f"{self.lens.title()} → {self._entry_title(entry) if entry else 'Selection'} → {level}"
        )
        selection = Text("Evidence preview — ", style("label"))
        if isinstance(execution, ExecutionResultRow):
            selection.append(execution.subject.resource_id, style("bright"))
            selection.append(" · " + self._case_label(execution))
        self.query_one("#result-selection", Caption).update(selection)
        lanes = Content("")
        sides = (
            (owner.baseline, owner.candidate)
            if isinstance(owner, ComparisonResultRow)
            else ((owner.reference,) if isinstance(owner, ExecutionResultRow) else ())
        )
        for reference in sides:
            row = self.items.get(reference)
            if isinstance(row, ExecutionResultRow):
                cells = self._occurrence_cells(row)
                if lanes.plain:
                    lanes += Content("\n")
                lanes += self._environment_label(row) + Content(" · HTTP ")
                lanes += cells[3] if isinstance(cells[3], Content) else Content(cells[3])
                lanes += Content(f" · {cells[4]}")
                lanes += Content("  Assertions ") + fact_counts(
                    self.items[ref].outcome for ref in row.assertions if ref in self.items
                )
        self.query_one("#result-lanes", Caption).update(lanes)
        actions = [
            Action("evidence", "Open evidence", "e", primary=True),
            Action("back", "Back", "Esc"),
        ]
        if self._comparison_for(item) is not None and not isinstance(owner, ComparisonResultRow):
            actions.append(Action("compare", "Compare responses"))
        if isinstance(item, ResultFact):
            actions.append(
                Action(
                    "owner",
                    "Comparison details"
                    if isinstance(owner, ComparisonResultRow)
                    else "HTTP details",
                )
            )
        if isinstance(owner, ComparisonResultRow):
            actions.extend(
                (
                    Action("baseline", "Baseline"),
                    Action("candidate", "Candidate"),
                    Action("compare", "Compare bodies", primary=True),
                )
            )
        self.query_one("#result-detail-actions", ActionBar).actions = tuple(actions)

    def _update_evidence_links(self, item: ResultItem | None) -> None:
        references = (
            (*item.assertions, *item.comparisons)
            if isinstance(item, ExecutionResultRow)
            else item.diffs
            if isinstance(item, ComparisonResultRow)
            else ()
        )
        self.evidence_links = {
            str(index): ref for index, ref in enumerate(references) if ref in self.items
        }
        menu = self.query_one("#result-evidence-links", Menu)
        menu.items = tuple(
            MenuItem(key, item_label(self.items[ref]) + " · " + self.items[ref].outcome)
            for key, ref in self.evidence_links.items()
        )
        menu.display = bool(self.evidence_links)
        self.query_one("#result-links-title").display = bool(self.evidence_links)

    @on(Menu.Selected, "#result-evidence-links")
    def _evidence_link(self, event: Menu.Selected) -> None:
        event.stop()
        if event.item in self.evidence_links:
            self.open_reference(self.evidence_links[event.item])

    def _clear_body(self) -> None:
        self.body_references = ()
        self.remove_class("evidence-open", "body-focus", "detail-focus")
        self.messages = {}
        self.query_one("#result-messages", ChoiceMenu).items = ()
        self.query_one("#result-messages", ChoiceMenu).selected = ()
        self.query_one("#result-body", DiffView).document = None
        self.query_one("#result-body", DiffView).display = False
        self.query_one("#result-body-labels").display = False
        self.query_one("#result-messages", ChoiceMenu).display = False
        self.query_one("#result-body-status", Caption).update(
            "Select an occurrence to inspect its request, response and captured bodies."
        )

    def show_body(
        self,
        references: tuple[ArtifactBodyReference, ...],
        document: DiffDocument | None,
        message: str,
    ) -> None:
        """Ignore stale asynchronous reads when the user has navigated elsewhere."""
        if references != self.body_references or not list(self.query(DiffView)):
            return
        self.query_one("#result-body", DiffView).view_mode = (
            "split" if self.query_one("#result-scroll").size.width >= 64 else "unified"
        )
        self.query_one("#result-body", DiffView).document = document
        self.query_one("#result-body", DiffView).display = document is not None
        self.set_class(document is not None, "evidence-open")
        self.query_one("#result-body-labels").display = document is not None
        labels = []
        for reference in references:
            execution = next(
                (
                    row
                    for row in self.items.values()
                    if isinstance(row, ExecutionResultRow)
                    and any(message.reference == reference for message in row.messages)
                ),
                None,
            )
            labels.append(
                self._environment_label(execution) + Content(f" · {reference.message.value}")
                if execution
                else Content(reference.message.value)
            )
        self._body_labels = labels
        self.call_after_refresh(self._layout_body)
        changed = sum(row.kind != "context" for row in document.rows) if document is not None else 0
        prefix = (
            f"Response preview · {changed} changed lines · "
            if len(references) == 2
            else "Body preview · "
        )
        self.query_one("#result-body-status", Caption).update(prefix + message)
        if document is not None and document.sided:
            self.call_after_refresh(self.query_one(DiffView).focus_first_change)
        if document is not None and self._reveal_on_body:
            self.call_after_refresh(self._layout_body)
            self.call_after_refresh(self._reveal_body)

    def _reveal_body(self) -> None:
        if self.display and self.body_references and list(self.query(DiffView)):
            if self.has_class("body-focus"):
                self.query_one("#result-scroll", VerticalScroll).scroll_home(animate=False)
                self.query_one(DiffView).focus(scroll_visible=False)
            else:
                self.query_one(DiffView).scroll_visible(animate=False, top=True, force=True)

    @on(FilterInput.Changed)
    def _filter(self, event: FilterInput.Changed) -> None:
        event.stop()
        self._populate(event.value)

    @on(Table.RowHighlighted, "#result-index")
    def _highlight(self, event: Table.RowHighlighted[str]) -> None:
        event.stop()
        child = self.index_children.get(event.row)
        if child:
            self._group(child[0])
            reference = child[1]
            self.open_reference(reference, remember=False)
            related = self.query_one("#result-related", Table)
            key = next((key for key, ref in self.links.items() if ref == reference), None)
            if key is not None:
                related.move_cursor(row=tuple(self.links).index(key), scroll=False)
        else:
            self._group(event.row)

    @on(Table.RowHighlighted, "#result-related")
    @on(Table.RowSelected, "#result-related")
    def _open(self, event: Table.RowSelected[str]) -> None:
        event.stop()
        reference = self.links.get(event.row)
        if reference is not None and reference != self.selected:
            self.history.clear()
            self.open_reference(reference, remember=False)

    @on(Table.RowSelected, "#result-index")
    def _open_group(self, event: Table.RowSelected[str]) -> None:
        event.stop()
        self.add_class("inspecting")
        self.call_after_refresh(self._fit_tables)
        if self.lens != "requests" or not self.has_class("wide"):
            self.query_one("#result-related", Table).focus()

    @on(ChoiceMenu.Changed, "#result-messages")
    def _body(self, event: ChoiceMenu.Changed) -> None:
        event.stop()
        if event.selected and event.selected[0] in self.messages:
            self._request_body((self.messages[event.selected[0]],))

    def _request_body(
        self, references: tuple[ArtifactBodyReference, ...], *, reveal: bool = True
    ) -> None:
        self.body_references = references
        self._reveal_on_body = reveal
        self.set_class(reveal, "body-focus")
        if reveal:
            self.add_class("inspecting")
        self.query_one("#result-body-status", Caption).update(
            "Loading authenticated body evidence…"
        )
        self.post_message(self.BodyRequested(self, references))

    @on(ActionBar.Requested, "#result-lenses")
    @on(ActionBar.Requested, "#result-empty-actions")
    def _navigate(self, event: ActionBar.Requested) -> None:
        event.stop()
        if event.action in LENSES:
            self.action_lens(event.action)
        elif event.action == "clear-filter":
            self.query_one(FilterInput).value = ""
            self.query_one(FilterInput).focus()

    @on(ActionBar.Requested, "#result-detail-actions")
    def _action(self, event: ActionBar.Requested) -> None:
        event.stop()
        if event.action == "back":
            self.action_back()
        elif event.action == "compare":
            self._compare_bodies()
        elif event.action == "evidence":
            self.action_evidence()
        elif self.selected is not None:
            item = self.items[self.selected]
            owner = self._owner(item)
            if event.action == "owner" and owner is not None:
                self.open_reference(owner.reference)
            elif isinstance(owner, ComparisonResultRow):
                if event.action == "baseline":
                    self.open_reference(owner.baseline)
                elif event.action == "candidate":
                    self.open_reference(owner.candidate)

    def _compare_bodies(self, *, reveal: bool = True) -> None:
        selected = self.items.get(self.selected) if self.selected is not None else None
        item = self._comparison_for(selected) if selected is not None else None
        if item is None:
            self.query_one("#result-body-status", Caption).update(
                "Select a comparison to inspect both responses."
            )
            return
        references: list[ArtifactBodyReference] = []
        for reference in (item.baseline, item.candidate):
            execution = self.items.get(reference)
            if isinstance(execution, ExecutionResultRow):
                references.extend(
                    message.reference
                    for message in execution.messages
                    if message.terminal_attempt
                    and message.terminal_exchange
                    and message.reference.message.value == "response"
                    and message.has_body_capture
                )
        if len(references) == 2:
            self._request_body(tuple(references), reveal=reveal)
        else:
            self.query_one("#result-body-status", Caption).update(
                "Both terminal response captures are required; a missing side cannot be compared."
            )

    def action_lens(self, lens: str) -> None:
        """Select an analysis view and put focus in its visible navigation surface."""
        self._show_lens(lens)
        self.action_groups()

    def action_cycle_lens(self, direction: int) -> None:
        """Cycle through the five views from any analysis child, wrapping at the ends."""
        self.action_lens(LENSES[(LENSES.index(self.lens) + direction) % len(LENSES)])

    def action_focus_filter(self) -> None:
        """Reveal the current view's index and focus its filter."""
        self.action_groups()
        self.query_one(FilterInput).focus()

    @on(FilterInput.Dismissed)
    @on(FilterInput.Submitted)
    def _leave_filter(self, event: FilterInput.Dismissed | FilterInput.Submitted) -> None:
        event.stop()
        self.action_groups()

    def action_back(self) -> None:
        """Pop local evidence navigation before leaving the analysis surface."""
        if self.has_class("body-focus") or self.has_class("detail-focus"):
            self.remove_class("body-focus", "detail-focus")
            self.query_one("#result-scroll", VerticalScroll).scroll_home(animate=False)
            self.call_after_refresh(self._layout_body)
        elif self.history:
            self.open_reference(self.history.pop(), remember=False)
        else:
            self.action_groups()

    def action_groups(self) -> None:
        """Return to the group selector without losing the selected occurrence."""
        self.remove_class("inspecting", "body-focus", "detail-focus")
        if self.entries:
            self.query_one("#result-index", Table).focus()
        else:
            self.query_one(FilterInput).focus()

    def action_occurrences(self) -> None:
        """Jump directly to the stable list for this group."""
        if not self.links:
            return
        self.add_class("inspecting")
        self.remove_class("body-focus", "detail-focus")
        self.call_after_refresh(self._fit_tables)
        self.query_one("#result-related", Table).focus()

    def action_evidence(self) -> None:
        """Give compact terminals room for evidence while preserving its context."""
        if self.selected is None:
            return
        self.add_class("inspecting", "detail-focus")
        self.query_one("#result-scroll", VerticalScroll).focus()
        if self.query_one(DiffView).document is not None:
            self.add_class("body-focus")
            self.call_after_refresh(self._layout_body)
        else:
            self.query_one(ResultDetail).scroll_visible(animate=False, top=True)

    def on_resize(self, event: Resize) -> None:
        """Apply the shared canonical terminal breakpoints."""
        layout = layout_class(event.size.width)
        self.set_class(layout is LayoutClass.COMPACT, "compact")
        self.set_class(layout is LayoutClass.WIDE, "wide")
        self.set_class(layout is LayoutClass.STANDARD, "standard")
        self.call_after_refresh(self._fit_tables)
        self.call_after_refresh(self._layout_body)

    def _layout_body(self) -> None:
        view = self.query_one(DiffView)
        if view.document is None:
            return
        view.view_mode = "split" if view.size.width >= 64 else "unified"
        self._body_mode_labels()

    @on(DiffView.ViewChanged)
    def _body_mode_changed(self, event: DiffView.ViewChanged) -> None:
        event.stop()
        self._body_mode_labels()

    def _body_mode_labels(self) -> None:
        labels = self._body_labels
        sided = len(labels) == 2
        split = self.query_one(DiffView).view_mode == "split"
        left = labels[0] if labels else Content("")
        if sided and not split:
            left = Content("Unified diff · ") + labels[0] + Content(" → ") + labels[1]
        self.query_one("#result-body-left", Caption).update(left)
        self.query_one("#result-body-right", Caption).update(labels[1] if sided else "")
        self.query_one("#result-body-right").display = sided and split
        self.query_one("#result-body-labels").set_class(not sided or not split, "single-body")

    def _fit_tables(self) -> None:
        if self.projection is None:
            return
        table = self.query_one("#result-related", Table)
        table.fit_proportions(
            (0.36, 0.17, 0.17, 0.17, 0.13)
            if self.lens == "requests"
            else (0.28, 0.24, 0.16, 0.13, 0.19)
        )
        index = self.query_one("#result-index", Table)
        shares = {
            "issues": (0.66, 0.34),
            "requests": (0.37, 0.17, 0.17, 0.17, 0.12),
            "rules": (0.6, 0.4),
            "fields": (0.63, 0.37),
            "timeline": (0.45, 0.33, 0.22),
        }[self.lens]
        index.fit_proportions(shares)

    @override
    def focus(self, scroll_visible: bool = True) -> "ResultsView":
        """Focus the stable result index without activating a row."""
        selector = (
            "#result-related"
            if self.has_class("inspecting")
            and (
                self.has_class("compact")
                or (self.lens == "requests" and self.has_class("standard"))
            )
            else "#result-index"
        )
        self.query_one(selector, Table).focus(scroll_visible)
        return self
