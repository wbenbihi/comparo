"""Directed diff facts, aggregate outcomes, and bounded evidence."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import require_operation_family
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.side_coverage_validation import require_complete_side_coverage
from comparo.core.domain.diffs.side_coverage_validation import require_extra_side_shapes
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.locations import location_environment_execution_id
from comparo.core.domain.selectors.model import require_location_matches_selector


class DiffFactOutcome(DomainStrEnum):
    """Concrete directed field facts."""

    SAME = "same"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"
    IGNORED = "ignored"
    INCOMPARABLE = "incomparable"


@final
class DiffFact(msgspec.Struct, frozen=True, kw_only=True):
    """One directed field fact at zero, one, or two concrete locations."""

    fact_id: FactId
    outcome: DiffFactOutcome
    comparator: DiffOperationIdentity
    baseline_location: ConcreteLocation | None = None
    candidate_location: ConcreteLocation | None = None
    diagnostic: DiagnosticCode | None = None

    def __post_init__(self) -> None:
        """Enforce added/removed direction and incomparable diagnostics."""
        if type(self.fact_id) is not FactId or type(self.outcome) is not DiffFactOutcome:
            raise TypeError("diff fact requires typed ID and outcome")
        require_operation_family(self.comparator, DiffOperationFamily.COMPARATOR)
        for location in (self.baseline_location, self.candidate_location):
            if location is not None and not is_concrete_location(location):
                raise TypeError("diff fact location has an unsupported type")
        if self.diagnostic is not None and type(self.diagnostic) is not DiagnosticCode:
            raise TypeError("diff fact diagnostic must be a DiagnosticCode")
        baseline = self.baseline_location is not None
        candidate = self.candidate_location is not None
        if self.outcome is DiffFactOutcome.ADDED and (baseline or not candidate):
            raise ValueError("added fact requires only a candidate location")
        if self.outcome is DiffFactOutcome.REMOVED and (not baseline or candidate):
            raise ValueError("removed fact requires only a baseline location")
        if self.outcome in {DiffFactOutcome.SAME, DiffFactOutcome.CHANGED} and (
            not baseline or not candidate
        ):
            raise ValueError("same and changed facts require both locations")
        if (self.outcome is DiffFactOutcome.INCOMPARABLE) != (self.diagnostic is not None):
            raise ValueError("exactly incomparable facts require diagnostics")
        if self.outcome is DiffFactOutcome.IGNORED and not (baseline or candidate):
            raise ValueError("ignored fact requires at least one location")


@final
class DiffEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """Bounded evidence whose omission never changes exact counts."""

    retained: tuple[DiffFact, ...]
    omitted: Count

    def __post_init__(self) -> None:
        """Require immutable typed facts and exact omission count."""
        if type(self.retained) is not tuple:
            raise TypeError("diff evidence must be an immutable tuple")
        if any(type(fact) is not DiffFact for fact in self.retained):
            raise TypeError("diff evidence contains an unsupported fact")
        if type(self.omitted) is not Count:
            raise TypeError("omitted evidence must be a Count")
        fact_ids = tuple(fact.fact_id for fact in self.retained)
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("diff evidence fact IDs must be unique")


@final
class Equivalent(msgspec.Struct, frozen=True, kw_only=True):
    """Fully resolved, non-empty comparable coverage with no drift."""


@final
class Drifted(msgspec.Struct, frozen=True, kw_only=True):
    """Fully resolved comparable coverage with concrete directed drift."""


class DiffSide(DomainStrEnum):
    """The side whose state prevented a complete comparison."""

    BASELINE = "baseline"
    CANDIDATE = "candidate"
    BOTH = "both"


class DiffEvaluationStage(DomainStrEnum):
    """The pure pipeline stage at which completeness was lost."""

    EXECUTION = "execution"
    CAPTURE = "capture"
    DECODE = "decode"
    SELECTION = "selection"
    DERIVATION = "derivation"
    PAIRING = "pairing"


class DiffIncompleteCause(DomainStrEnum):
    """Typed causes that are not evaluator defects."""

    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    BUDGET_EXCEEDED = "budget-exceeded"
    CANCELLED = "cancelled"


@final
class DiffIncompleteReason(msgspec.Struct, frozen=True, kw_only=True):
    """Side, stage, and cause of an incomplete comparison."""

    side: DiffSide
    stage: DiffEvaluationStage
    cause: DiffIncompleteCause

    def __post_init__(self) -> None:
        """Require every incomplete dimension explicitly."""
        if (
            type(self.side) is not DiffSide
            or type(self.stage) is not DiffEvaluationStage
            or type(self.cause) is not DiffIncompleteCause
        ):
            raise TypeError("diff incomplete reason requires typed side, stage, and cause")


@final
class Incomplete(msgspec.Struct, frozen=True, kw_only=True):
    """Comparison could not reach complete comparable coverage."""

    reason: DiffIncompleteReason

    def __post_init__(self) -> None:
        """Require a typed non-diagnostic reason."""
        if type(self.reason) is not DiffIncompleteReason:
            raise TypeError("incomplete outcome requires a DiffIncompleteReason")


@final
class DiffEvaluationError(msgspec.Struct, frozen=True, kw_only=True):
    """A pure evaluator defect or invalid runtime operand with a safe code."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Keep errors distinctly diagnostic."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("diff evaluation error requires a DiagnosticCode")


class DiffNotEvaluatedReason(DomainStrEnum):
    """Reasons a rule intentionally produced no comparable facts."""

    FALSE_APPLICABILITY = "false-applicability"
    EMPTY_SELECTION = "empty-selection"
    ALL_IGNORED = "all-ignored"


@final
class NotEvaluated(msgspec.Struct, frozen=True, kw_only=True):
    """A deliberate absence of comparison, never equivalence."""

    reason: DiffNotEvaluatedReason

    def __post_init__(self) -> None:
        """Require an explicit typed reason."""
        if type(self.reason) is not DiffNotEvaluatedReason:
            raise TypeError("not-evaluated outcome requires a DiffNotEvaluatedReason")


type DiffAggregateOutcome = Equivalent | Drifted | Incomplete | DiffEvaluationError | NotEvaluated


def is_diff_aggregate_outcome(value: object) -> bool:
    """Narrow to the closed aggregate outcome union."""
    return type(value) in {Equivalent, Drifted, Incomplete, DiffEvaluationError, NotEvaluated}


@final
class DiffRuleFact(msgspec.Struct, frozen=True, kw_only=True):
    """The complete aggregate fact for one directed diff application."""

    fact_id: FactId
    application: DiffApplication
    rule: CompiledDiffRule
    outcome: DiffAggregateOutcome
    counts: DiffFactCounts
    coverage: DiffCoverage
    evidence: DiffEvidence

    def __post_init__(self) -> None:  # noqa: C901
        """Validate facts under error > incomplete > drift > equivalent precedence."""
        if type(self.fact_id) is not FactId or type(self.application) is not DiffApplication:
            raise TypeError("diff rule fact requires typed fact and application IDs")
        if type(self.rule) is not CompiledDiffRule:
            raise TypeError("diff rule fact requires a CompiledDiffRule")
        if (
            self.application.definition != self.rule.definition
            or self.application.effective_id != self.rule.effective_id
            or self.application.rule_fingerprint != self.rule.fingerprint
        ):
            raise ValueError("diff application does not identify the effective compiled rule")
        if not is_diff_aggregate_outcome(self.outcome):
            raise TypeError("diff aggregate outcome must be a DiffAggregateOutcome")
        if type(self.counts) is not DiffFactCounts or type(self.coverage) is not DiffCoverage:
            raise TypeError("diff rule fact requires typed counts and coverage")
        if type(self.evidence) is not DiffEvidence:
            raise TypeError("diff rule fact evidence must be DiffEvidence")
        if any(fact.fact_id == self.fact_id for fact in self.evidence.retained):
            raise ValueError("diff parent fact ID must differ from retained child fact IDs")
        _require_evidence_binding(self.evidence, self.application, self.rule)
        if type(self.rule.pipeline.pairing) is SingularPairing and self.coverage.paired.value > 1:
            raise ValueError("singular pairing cannot produce more than one paired unit")
        if len(self.evidence.retained) + self.evidence.omitted.value != self.counts.total:
            raise ValueError("evidence must cover every concrete fact")
        comparator = self.rule.pipeline.comparator.identity
        if any(fact.comparator != comparator for fact in self.evidence.retained):
            raise ValueError("diff evidence comparator does not match the compiled pipeline")
        _require_evidence_buckets(self.evidence, self.counts)
        if self.coverage.compared.value != self.counts.evaluated:
            raise ValueError("compared coverage must equal non-ignored fact counts")
        if self.coverage.ignored.value != self.counts.ignored.value:
            raise ValueError("ignored coverage must equal ignored fact count")
        _require_side_coverage(self.counts, self.coverage)
        _require_evidence_side_shapes(self.evidence, self.counts, self.coverage)
        if type(self.outcome) in {Equivalent, Drifted}:
            require_complete_side_coverage(self.coverage)
        if (
            type(self.outcome) is NotEvaluated
            and self.outcome.reason is DiffNotEvaluatedReason.ALL_IGNORED
        ):
            require_complete_side_coverage(self.coverage)
        drift = self.counts.changed.value + self.counts.added.value + self.counts.removed.value
        unresolved = self.coverage.not_evaluated.value or self.counts.incomparable.value
        if type(self.outcome) is Equivalent and (
            self.coverage.compared.value == 0
            or self.coverage.baseline_derived.value == 0
            or self.coverage.candidate_derived.value == 0
            or drift
            or unresolved
        ):
            raise ValueError("equivalent requires complete comparable coverage without drift")
        if type(self.outcome) is Drifted and (drift == 0 or unresolved):
            raise ValueError("drifted requires complete comparable coverage with concrete drift")
        if type(self.outcome) is Incomplete:
            _require_incomplete_evidence(self.outcome, self.counts, self.coverage)
        if type(self.outcome) is NotEvaluated:
            _require_not_evaluated_evidence(self.outcome, self.counts, self.coverage)

    @property
    def severity(self) -> DiffSeverity:
        """Return effective severity from the exact bound compiled rule."""
        return self.rule.severity


def _require_evidence_binding(
    evidence: DiffEvidence,
    application: DiffApplication,
    rule: CompiledDiffRule,
) -> None:
    pair = application.pair
    for fact in evidence.retained:
        if fact.baseline_location is not None:
            if (
                location_environment_execution_id(fact.baseline_location)
                != pair.baseline_execution_id
            ):
                raise ValueError("baseline diff evidence belongs to a different execution")
            require_location_matches_selector(
                fact.baseline_location,
                rule.pipeline.baseline.selector,
            )
        if fact.candidate_location is not None:
            if (
                location_environment_execution_id(fact.candidate_location)
                != pair.candidate_execution_id
            ):
                raise ValueError("candidate diff evidence belongs to a different execution")
            require_location_matches_selector(
                fact.candidate_location,
                rule.pipeline.candidate.selector,
            )


def _require_side_coverage(counts: DiffFactCounts, coverage: DiffCoverage) -> None:
    bilateral = counts.same.value + counts.changed.value
    baseline_known = bilateral + counts.removed.value
    candidate_known = bilateral + counts.added.value
    extra_count = counts.ignored.value + counts.incomparable.value
    require_extra_side_shapes(
        baseline_extra=coverage.baseline_resolved.value - baseline_known,
        candidate_extra=coverage.candidate_resolved.value - candidate_known,
        extra_count=extra_count,
        label="diff facts",
    )


def _require_evidence_side_shapes(
    evidence: DiffEvidence,
    counts: DiffFactCounts,
    coverage: DiffCoverage,
) -> None:
    bilateral = counts.same.value + counts.changed.value
    baseline_extra = coverage.baseline_resolved.value - bilateral - counts.removed.value
    candidate_extra = coverage.candidate_resolved.value - bilateral - counts.added.value
    extra_count = counts.ignored.value + counts.incomparable.value
    expected_baseline_only = extra_count - candidate_extra
    expected_candidate_only = extra_count - baseline_extra
    expected_bilateral = baseline_extra + candidate_extra - extra_count
    retained = tuple(
        fact
        for fact in evidence.retained
        if fact.outcome in {DiffFactOutcome.IGNORED, DiffFactOutcome.INCOMPARABLE}
    )
    retained_baseline_only = sum(
        fact.baseline_location is not None and fact.candidate_location is None for fact in retained
    )
    retained_candidate_only = sum(
        fact.baseline_location is None and fact.candidate_location is not None for fact in retained
    )
    retained_bilateral = sum(
        fact.baseline_location is not None and fact.candidate_location is not None
        for fact in retained
    )
    if (
        retained_baseline_only > expected_baseline_only
        or retained_candidate_only > expected_candidate_only
        or retained_bilateral > expected_bilateral
    ):
        raise ValueError("retained ignored or incomparable locations contradict side coverage")


def _require_evidence_buckets(evidence: DiffEvidence, counts: DiffFactCounts) -> None:
    limits = {
        DiffFactOutcome.SAME: counts.same.value,
        DiffFactOutcome.CHANGED: counts.changed.value,
        DiffFactOutcome.ADDED: counts.added.value,
        DiffFactOutcome.REMOVED: counts.removed.value,
        DiffFactOutcome.IGNORED: counts.ignored.value,
        DiffFactOutcome.INCOMPARABLE: counts.incomparable.value,
    }
    for outcome, limit in limits.items():
        if sum(fact.outcome is outcome for fact in evidence.retained) > limit:
            raise ValueError("diff evidence contradicts exact outcome counts")


def _require_incomplete_evidence(
    outcome: Incomplete,
    counts: DiffFactCounts,
    coverage: DiffCoverage,
) -> None:
    if outcome.reason.stage is DiffEvaluationStage.PAIRING and not (
        coverage.not_evaluated.value or counts.incomparable.value
    ):
        raise ValueError("pairing incompleteness requires unresolved or incomparable units")


def _require_not_evaluated_evidence(
    outcome: NotEvaluated,
    counts: DiffFactCounts,
    coverage: DiffCoverage,
) -> None:
    if coverage.compared.value:
        raise ValueError("not-evaluated cannot contain compared coverage")
    if outcome.reason is DiffNotEvaluatedReason.ALL_IGNORED:
        if (
            counts.ignored.value == 0
            or counts.total != counts.ignored.value
            or coverage.not_evaluated.value
        ):
            raise ValueError("all-ignored requires at least one and only ignored concrete fact")
        return
    if counts.total != 0:
        raise ValueError("false-applicability or empty-selection cannot contain concrete facts")
    if outcome.reason is DiffNotEvaluatedReason.FALSE_APPLICABILITY:
        if any(
            count.value
            for count in (
                coverage.baseline_selected,
                coverage.candidate_selected,
                coverage.baseline_derived,
                coverage.candidate_derived,
                coverage.paired,
                coverage.compared,
                coverage.ignored,
                coverage.not_evaluated,
            )
        ):
            raise ValueError("false applicability requires zero pipeline coverage")
        return
    if coverage.baseline_selected.value > 0 and coverage.candidate_selected.value > 0:
        raise ValueError("empty selection requires at least one empty selected side")
    if any(
        count.value
        for count in (
            coverage.baseline_derived,
            coverage.candidate_derived,
            coverage.paired,
            coverage.compared,
            coverage.ignored,
            coverage.not_evaluated,
        )
    ):
        raise ValueError("empty selection requires zero derived and resolved coverage")
