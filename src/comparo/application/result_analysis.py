"""Bounded semantic analysis projections over one sanitized result manifest."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from collections.abc import Iterable
from itertools import chain
from typing import final

import msgspec

from comparo.application.results import ComparisonResultRow
from comparo.application.results import ExecutionResultRow
from comparo.application.results import ResultFact
from comparo.application.results import ResultNamedValue
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.application.results import ResultResource


@final
class ResultProjectionWindow(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned finite window for one derived analysis projection."""

    maximum_items: int

    def __post_init__(self) -> None:
        """Require a positive caller-owned finite output window."""
        if type(self.maximum_items) is not int or self.maximum_items < 1:
            raise ValueError("result projection window must be a positive integer")


@final
class IssueAggregate(msgspec.Struct, frozen=True, kw_only=True):
    """One rule/outcome issue group with exact retained occurrence counts."""

    rule_id: str
    severity: str
    outcome: str
    occurrences: int
    affected_cells: int
    affected_subjects: int
    evidence_locations: int
    references: tuple[ResultReference, ...]


@final
class RequestAggregate(msgspec.Struct, frozen=True, kw_only=True):
    """One Request aggregate across retained environment executions."""

    subject: ResultResource
    executions: int
    environments: int
    cells: int
    outcomes: tuple[tuple[str, int], ...]
    references: tuple[ResultReference, ...]


@final
class RuleAggregate(msgspec.Struct, frozen=True, kw_only=True):
    """One assertion/diff rule aggregate across retained applications."""

    rule: ResultResource
    engine: str
    rule_id: str
    severity: str
    applications: int
    evidence_locations: int
    evidence_omitted: int
    outcomes: tuple[tuple[str, int], ...]
    references: tuple[ResultReference, ...]


@final
class FieldPathAggregate(msgspec.Struct, frozen=True, kw_only=True):
    """One normalized evidence location aggregated across retained fact applications."""

    path: str
    severity: str
    outcome: str
    occurrences: int
    affected_cells: int
    affected_subjects: int
    samples: tuple[ResultNamedValue, ...]
    sample_values_omitted: int
    references: tuple[ResultReference, ...]


@final
class TimelineEvent(msgspec.Struct, frozen=True, kw_only=True):
    """One causally ordered artifact event with an addressable owning result."""

    ordinal: int
    phase: str
    label: str
    outcome: str
    reference: ResultReference
    parent: ResultReference | None
    cell_id: str
    trial_id: str
    subject: ResultResource
    environment: ResultResource | None


@final
class AnalysisProjection[T](msgspec.Struct, frozen=True, kw_only=True):
    """One explicitly incomplete finite derived collection."""

    items: tuple[T, ...]
    total: int
    omitted: int

    def __post_init__(self) -> None:
        """Require exact conservation between visible and omitted items."""
        if type(self.items) is not tuple:
            raise TypeError("analysis projection items must be immutable")
        if any(type(value) is not int or value < 0 for value in (self.total, self.omitted)):
            raise ValueError("analysis projection counts must be non-negative integers")
        if len(self.items) + self.omitted != self.total:
            raise ValueError("analysis projection counts must conserve the derived collection")


def project_issues(
    projection: ResultProjection,
    window: ResultProjectionWindow,
    selected_reference: ResultReference | None = None,
) -> AnalysisProjection[IssueAggregate]:
    """Group verdict-relevant facts without recomputing their outcomes."""
    _require_inputs(projection, window)
    executions, comparisons = _owner_context(projection)
    grouped: dict[tuple[str, str, str], list[ResultFact]] = defaultdict(list)
    for fact in _facts(projection):
        if _is_issue_outcome(fact):
            grouped[(fact.effective_rule_id, fact.severity, fact.outcome)].append(fact)
    items = tuple(
        IssueAggregate(
            rule_id=rule_id,
            severity=severity,
            outcome=outcome,
            occurrences=len(facts),
            affected_cells=len(
                {
                    context.cell_id
                    for fact in facts
                    if (context := _context(fact, executions, comparisons)) is not None
                }
            ),
            affected_subjects=len(
                {
                    (context.subject.namespace, context.subject.kind, context.subject.resource_id)
                    for fact in facts
                    if (context := _context(fact, executions, comparisons)) is not None
                }
            ),
            evidence_locations=len({item.location for fact in facts for item in fact.evidence}),
            references=tuple(fact.reference for fact in facts),
        )
        for (rule_id, severity, outcome), facts in sorted(grouped.items())
    )
    return _window(
        items,
        window,
        selected_reference,
        lambda item, reference: reference in item.references,
    )


def project_requests(
    projection: ResultProjection,
    window: ResultProjectionWindow,
    selected_reference: ResultReference | None = None,
) -> AnalysisProjection[RequestAggregate]:
    """Aggregate environment executions by stable Request identity."""
    _require_inputs(projection, window)
    grouped: dict[tuple[str, str, str], list[ExecutionResultRow]] = defaultdict(list)
    for execution in projection.executions:
        subject = execution.subject
        grouped[(subject.namespace, subject.kind, subject.resource_id)].append(execution)
    items = tuple(
        RequestAggregate(
            subject=rows[0].subject,
            executions=len(rows),
            environments=len(
                {(row.environment.namespace, row.environment.resource_id) for row in rows}
            ),
            cells=len({row.cell_id for row in rows}),
            outcomes=_tallies(row.outcome for row in rows),
            references=tuple(row.reference for row in rows),
        )
        for _identity, rows in sorted(grouped.items())
    )
    return _window(
        items,
        window,
        selected_reference,
        lambda item, reference: reference in item.references,
    )


def project_rules(
    projection: ResultProjection,
    window: ResultProjectionWindow,
    selected_reference: ResultReference | None = None,
) -> AnalysisProjection[RuleAggregate]:
    """Aggregate rule applications by stable resource and effective rule identity."""
    _require_inputs(projection, window)
    grouped: dict[tuple[str, str, str, str, str], list[ResultFact]] = defaultdict(list)
    for fact in _facts(projection):
        rule = fact.rule
        grouped[
            (
                fact.engine,
                rule.namespace,
                rule.resource_id,
                fact.effective_rule_id,
                fact.severity,
            )
        ].append(fact)
    items = tuple(
        RuleAggregate(
            rule=facts[0].rule,
            engine=engine,
            rule_id=rule_id,
            severity=severity,
            applications=len(facts),
            evidence_locations=sum(len(fact.evidence) for fact in facts),
            evidence_omitted=sum(fact.evidence_omitted for fact in facts),
            outcomes=_tallies(fact.outcome for fact in facts),
            references=tuple(fact.reference for fact in facts),
        )
        for (engine, _namespace, _resource_id, rule_id, severity), facts in sorted(grouped.items())
    )
    return _window(
        items,
        window,
        selected_reference,
        lambda item, reference: reference in item.references,
    )


def project_fields(
    projection: ResultProjection,
    window: ResultProjectionWindow,
    selected_reference: ResultReference | None = None,
) -> AnalysisProjection[FieldPathAggregate]:
    """Aggregate sanitized evidence by exact manifest-resident location."""
    _require_inputs(projection, window)
    executions, comparisons = _owner_context(projection)
    grouped: dict[str, list[tuple[ResultFact, tuple[ResultNamedValue, ...]]]] = defaultdict(list)
    for fact in _facts(projection):
        for evidence in fact.evidence:
            grouped[evidence.location].append((fact, evidence.values))
    items: list[FieldPathAggregate] = []
    for path, entries in sorted(grouped.items()):
        facts = tuple(item[0] for item in entries)
        samples, samples_omitted = _unique_samples(
            (value for _fact, values in entries for value in values),
            window.maximum_items,
        )
        contexts = tuple(
            context
            for fact in facts
            if (context := _context(fact, executions, comparisons)) is not None
        )
        items.append(
            FieldPathAggregate(
                path=path,
                severity=_worst_severity(fact.severity for fact in facts),
                outcome=_worst_outcome(fact.outcome for fact in facts),
                occurrences=len(entries),
                affected_cells=len({item.cell_id for item in contexts}),
                affected_subjects=len(
                    {
                        (item.subject.namespace, item.subject.kind, item.subject.resource_id)
                        for item in contexts
                    }
                ),
                samples=samples,
                sample_values_omitted=samples_omitted,
                references=tuple(dict.fromkeys(fact.reference for fact in facts)),
            )
        )
    return _window(
        tuple(items),
        window,
        selected_reference,
        lambda item, reference: reference in item.references,
    )


def project_timeline(
    projection: ResultProjection,
    window: ResultProjectionWindow,
    selected_reference: ResultReference | None = None,
) -> AnalysisProjection[TimelineEvent]:
    """Build a deterministic causal artifact order without inventing wall-clock timing."""
    _require_inputs(projection, window)
    assertions = defaultdict(list)
    diffs = defaultdict(list)
    for fact in projection.assertions:
        assertions[fact.owner].append(fact)
    for fact in projection.diffs:
        diffs[fact.owner].append(fact)
    executions = {item.reference: item for item in projection.executions}
    events: list[TimelineEvent] = []

    def append(
        phase: str,
        label: str,
        outcome: str,
        reference: ResultReference,
        parent: ResultReference | None,
        context: ExecutionResultRow | ComparisonResultRow,
        environment: ResultResource | None,
    ) -> None:
        events.append(
            TimelineEvent(
                ordinal=len(events),
                phase=phase,
                label=label,
                outcome=outcome,
                reference=reference,
                parent=parent,
                cell_id=context.cell_id,
                trial_id=context.trial_id,
                subject=context.subject,
                environment=environment,
            )
        )

    for execution in sorted(projection.executions, key=lambda item: item.ordinal):
        append(
            "request",
            f"{execution.subject.resource_id} @ {execution.environment.resource_id}",
            execution.outcome,
            execution.reference,
            None,
            execution,
            execution.environment,
        )
        for fact in assertions[execution.reference]:
            append(
                "assertion",
                fact.effective_rule_id,
                fact.outcome,
                fact.reference,
                execution.reference,
                execution,
                execution.environment,
            )
    for comparison in sorted(projection.comparisons, key=lambda item: item.ordinal):
        baseline = executions.get(comparison.baseline)
        append(
            "comparison",
            f"{comparison.subject.resource_id} · {comparison.cell_id}",
            comparison.outcome,
            comparison.reference,
            None,
            comparison,
            None if baseline is None else baseline.environment,
        )
        for fact in diffs[comparison.reference]:
            append(
                "diff",
                fact.effective_rule_id,
                fact.outcome,
                fact.reference,
                comparison.reference,
                comparison,
                None,
            )
    return _window(
        tuple(events),
        window,
        selected_reference,
        lambda item, reference: item.reference == reference,
    )


def _facts(projection: ResultProjection) -> Iterable[ResultFact]:
    return chain(projection.assertions, projection.diffs)


def _is_issue_outcome(fact: ResultFact) -> bool:
    return fact.outcome in {
        "violated",
        "drifted",
        "unavailable",
        "incomparable",
        "incomplete",
        "error",
        "failed",
        "advisory",
    } or bool(fact.issues)


def _owner_context(
    projection: ResultProjection,
) -> tuple[
    dict[ResultReference, ExecutionResultRow],
    dict[ResultReference, ComparisonResultRow],
]:
    return (
        {item.reference: item for item in projection.executions},
        {item.reference: item for item in projection.comparisons},
    )


def _context(
    fact: ResultFact,
    executions: dict[ResultReference, ExecutionResultRow],
    comparisons: dict[ResultReference, ComparisonResultRow],
) -> ExecutionResultRow | ComparisonResultRow | None:
    return executions.get(fact.owner) or comparisons.get(fact.owner)


def _tallies(values: Iterable[str]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return tuple((name, counts[name]) for name in sorted(counts))


def _unique_samples(
    values: Iterable[ResultNamedValue],
    maximum_items: int,
) -> tuple[tuple[ResultNamedValue, ...], int]:
    selected: list[ResultNamedValue] = []
    seen: set[ResultNamedValue] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        if len(selected) < maximum_items:
            selected.append(value)
    return tuple(selected), len(seen) - len(selected)


def _worst_severity(values: Iterable[str]) -> str:
    rank = {"blocking": 0, "error": 0, "advisory": 1, "warning": 1, "info": 2}
    selected = tuple(values)
    return min(selected, key=lambda item: rank.get(item, 3)) if selected else "info"


def _worst_outcome(values: Iterable[str]) -> str:
    rank = {
        "error": 0,
        "failed": 0,
        "violated": 0,
        "drifted": 1,
        "unavailable": 1,
        "incomparable": 1,
        "incomplete": 1,
        "advisory": 2,
        "satisfied": 3,
        "equivalent": 3,
        "observed": 3,
    }
    selected = tuple(values)
    return min(selected, key=lambda item: rank.get(item, 2)) if selected else "unavailable"


def _window[T](
    items: tuple[T, ...],
    window: ResultProjectionWindow,
    selected_reference: ResultReference | None,
    contains: Callable[[T, ResultReference], bool],
) -> AnalysisProjection[T]:
    if selected_reference is not None and type(selected_reference) is not ResultReference:
        raise TypeError("selected analysis reference must be typed or absent")
    visible = list(items[: window.maximum_items])
    if selected_reference is not None and not any(
        contains(item, selected_reference) for item in visible
    ):
        selected = next(
            (item for item in items if contains(item, selected_reference)),
            None,
        )
        if selected is not None and visible:
            visible[-1] = selected
    return AnalysisProjection(
        items=tuple(visible),
        total=len(items),
        omitted=len(items) - len(visible),
    )


def _require_inputs(projection: ResultProjection, window: ResultProjectionWindow) -> None:
    if type(projection) is not ResultProjection:
        raise TypeError("analysis projection requires ResultProjection")
    if type(window) is not ResultProjectionWindow:
        raise TypeError("analysis projection requires ResultProjectionWindow")


__all__ = (
    "AnalysisProjection",
    "FieldPathAggregate",
    "IssueAggregate",
    "RequestAggregate",
    "ResultProjectionWindow",
    "RuleAggregate",
    "TimelineEvent",
    "project_fields",
    "project_issues",
    "project_requests",
    "project_rules",
    "project_timeline",
)
