"""Safe result navigation and literal evidence formatting; never evaluate a verdict."""

from dataclasses import dataclass

from comparo.application.result_analysis import ResultProjectionWindow
from comparo.application.result_analysis import project_fields
from comparo.application.result_analysis import project_issues
from comparo.application.result_analysis import project_requests
from comparo.application.result_analysis import project_rules
from comparo.application.result_analysis import project_timeline
from comparo.application.results import ComparisonResultRow
from comparo.application.results import ExecutionResultRow
from comparo.application.results import ResultFact
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.application.results import ResultTiming
from comparo.tui.components.status import duration

WINDOW = ResultProjectionWindow(maximum_items=500)
type ResultItem = ExecutionResultRow | ComparisonResultRow | ResultFact


@dataclass(frozen=True)
class AnalysisEntry:
    """One lens group linked to exact artifact-owned result references."""

    label: str
    outcome: str
    detail: str
    references: tuple[ResultReference, ...]


def index_results(projection: ResultProjection) -> dict[ResultReference, ResultItem]:
    """Index immutable manifest entities for navigation only."""
    items: tuple[ResultItem, ...] = (
        *projection.executions,
        *projection.comparisons,
        *projection.assertions,
        *projection.diffs,
    )
    return {item.reference: item for item in items}


def analysis_entries(
    projection: ResultProjection, lens: str
) -> tuple[tuple[AnalysisEntry, ...], int]:
    """Project the reference lenses through the application-owned aggregation API."""
    if lens == "issues":
        issues = project_issues(projection, WINDOW)
        entries = tuple(
            AnalysisEntry(
                item.rule_id,
                item.outcome,
                f"{item.occurrences} occurrences · {item.affected_cells} cells · {item.severity}",
                item.references,
            )
            for item in issues.items
        )
        # Operational failures also need a route to their evidence even without rule facts.
        errors = tuple(
            AnalysisEntry(
                item.subject.resource_id,
                "error",
                f"{item.environment.resource_id} · {item.failure.code}",
                (item.reference,),
            )
            for item in projection.executions
            if item.failure is not None
        )
        return (*entries, *errors[:500]), issues.omitted + max(0, len(errors) - 500)
    if lens == "requests":
        requests = project_requests(projection, WINDOW)
        return tuple(
            AnalysisEntry(
                item.subject.resource_id,
                "",
                f"{item.executions} calls · {item.environments} environments · {item.cells} cells",
                item.references,
            )
            for item in requests.items
        ), requests.omitted
    if lens == "rules":
        rules = project_rules(projection, WINDOW)
        return tuple(
            AnalysisEntry(
                item.rule_id,
                "",
                ", ".join(f"{state}: {count}" for state, count in item.outcomes)
                + f" · {item.severity}",
                item.references,
            )
            for item in rules.items
        ), rules.omitted
    if lens == "fields":
        fields = project_fields(projection, WINDOW)
        return tuple(
            AnalysisEntry(
                item.path,
                item.outcome,
                f"{item.occurrences} occurrences · {item.affected_subjects} requests",
                item.references,
            )
            for item in fields.items
        ), fields.omitted
    timeline = project_timeline(projection, WINDOW)
    return tuple(
        AnalysisEntry(
            f"{item.ordinal + 1}. {item.label}",
            item.outcome,
            f"{item.subject.resource_id} · {item.phase}",
            (item.reference,),
        )
        for item in timeline.items
    ), timeline.omitted


def timings_text(timings: tuple[ResultTiming, ...]) -> str:
    """Preserve each measured timing or its explicit not-measured reason."""
    return " · ".join(
        f"{item.name}: "
        f"{duration(item.nanoseconds) if item.nanoseconds is not None else item.reason}"
        for item in timings
    )


def field_location_label(path: str, projection: ResultProjection) -> str:
    """Shorten known execution coordinates for display; preserve unknown locations verbatim."""
    executions = {row.reference.result_id: row for row in projection.executions}
    labels: list[tuple[str, str]] = []
    for location in path.split(" | "):
        owner, separator, relative = location.partition("/")
        execution = executions.get(owner)
        if not separator or execution is None:
            labels.append((location, ""))
            continue
        source, separator, suffix = relative.partition("/")
        coordinate, slash, remainder = suffix.partition("/")
        known_coordinates = {
            identifier
            for attempt in execution.attempts
            for identifier in (attempt.attempt_id, *(row.exchange_id for row in attempt.exchanges))
        }
        if coordinate in known_coordinates:
            suffix = remainder if slash else ""
        if suffix == source:
            suffix = ""
        label = source + (f"/{suffix}" if separator and suffix else "")
        labels.append((label, execution.environment.resource_id))
    if len({label for label, _environment in labels}) == 1 and all(
        environment for _label, environment in labels
    ):
        return labels[0][0] + " · " + " → ".join(environment for _label, environment in labels)
    return " ↔ ".join(
        f"{label} · {environment}" if environment else label for label, environment in labels
    )


def item_label(item: ResultItem) -> str:
    """Give a stable occurrence a human-readable environment or rule label."""
    if isinstance(item, ResultFact):
        return f"{item.rule_id} · {item.engine}"
    if isinstance(item, ExecutionResultRow):
        return f"{item.subject.resource_id} → {item.environment.resource_id}"
    return f"Δ {item.subject.resource_id} · comparison"


def related_references(item: ResultItem) -> tuple[ResultReference, ...]:
    """Follow explicit ownership and comparison links without guessing missing sides."""
    if isinstance(item, ResultFact):
        return (item.owner,)
    if isinstance(item, ExecutionResultRow):
        return (*item.assertions, *item.comparisons)
    return (item.baseline, item.candidate, *item.diffs)
