"""Directed diff facts, aggregate outcomes, and bounded evidence."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.evidence import DiffEvidence as DiffEvidence
from comparo.core.domain.diffs.evidence import DiffFact as DiffFact
from comparo.core.domain.diffs.evidence import DiffFactOutcome as DiffFactOutcome
from comparo.core.domain.diffs.evidence import DiffValueProvenance as DiffValueProvenance
from comparo.core.domain.diffs.result_validation import require_evidence_binding
from comparo.core.domain.diffs.result_validation import require_evidence_buckets
from comparo.core.domain.diffs.result_validation import require_evidence_side_shapes
from comparo.core.domain.diffs.result_validation import require_incomplete_evidence
from comparo.core.domain.diffs.result_validation import require_not_evaluated_evidence
from comparo.core.domain.diffs.result_validation import require_side_coverage
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.side_coverage_validation import require_complete_side_coverage
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import FactId
from comparo.core.domain.selectors.result_values import SelectionViewFailureProvenance
from comparo.core.domain.sensitivity import TaintOriginId


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
    COMPARISON = "comparison"


class DiffIncompleteCause(DomainStrEnum):
    """Typed causes that are not evaluator defects."""

    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    BUDGET_EXCEEDED = "budget-exceeded"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    OBSERVED_INVALID = "observed-invalid"


@final
class DiffPipelineIssue(msgspec.Struct, frozen=True, kw_only=True):
    """One exact value-free failure encountered by a pure pipeline stage."""

    side: DiffSide
    stage: DiffEvaluationStage
    cause: DiffIncompleteCause
    code: DiagnosticCode | None = None
    exhaustion: BudgetExceeded | None = None
    view_failure: SelectionViewFailureProvenance | None = None

    def __post_init__(self) -> None:
        """Keep structured upstream and budget evidence without payload text."""
        if type(self.side) is not DiffSide:
            raise TypeError("diff pipeline issue side must be typed")
        if type(self.stage) is not DiffEvaluationStage:
            raise TypeError("diff pipeline issue stage must be typed")
        if type(self.cause) is not DiffIncompleteCause:
            raise TypeError("diff pipeline issue cause must be typed")
        if self.code is not None and type(self.code) is not DiagnosticCode:
            raise TypeError("diff pipeline issue code must be a DiagnosticCode")
        if self.exhaustion is not None and type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("diff pipeline issue exhaustion must be BudgetExceeded")
        if (
            self.view_failure is not None
            and type(self.view_failure) is not SelectionViewFailureProvenance
        ):
            raise TypeError("diff pipeline issue view provenance must be typed")
        if (self.cause is DiffIncompleteCause.BUDGET_EXCEEDED) != (self.exhaustion is not None):
            raise ValueError("exactly budget-exceeded issues require exhaustion evidence")


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
    issues: tuple[DiffPipelineIssue, ...] = ()

    def __post_init__(self) -> None:
        """Require a typed non-diagnostic reason."""
        if type(self.reason) is not DiffIncompleteReason:
            raise TypeError("incomplete outcome requires a DiffIncompleteReason")
        if type(self.issues) is not tuple or any(
            type(issue) is not DiffPipelineIssue for issue in self.issues
        ):
            raise TypeError("incomplete issues must be an immutable typed tuple")
        if self.issues and self.issues[0] != DiffPipelineIssue(
            side=self.reason.side,
            stage=self.reason.stage,
            cause=self.reason.cause,
            code=self.issues[0].code,
            exhaustion=self.issues[0].exhaustion,
            view_failure=self.issues[0].view_failure,
        ):
            raise ValueError("the first pipeline issue must match the primary incomplete reason")


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
    EMPTY_DERIVATION = "empty-derivation"
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
    dependency_taint_origins: tuple[TaintOriginId, ...] = ()

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
        if type(self.dependency_taint_origins) is not tuple or any(
            type(origin) is not TaintOriginId for origin in self.dependency_taint_origins
        ):
            raise TypeError("diff rule dependency taint origins must be immutable and typed")
        if len(set(self.dependency_taint_origins)) != len(self.dependency_taint_origins):
            raise ValueError("diff rule dependency taint origins must be unique")
        if any(
            not set(fact.dependency_taint_origins).issubset(self.dependency_taint_origins)
            for fact in self.evidence.retained
        ):
            raise ValueError("diff evidence dependency taint must be summarized by its parent")
        if any(fact.fact_id == self.fact_id for fact in self.evidence.retained):
            raise ValueError("diff parent fact ID must differ from retained child fact IDs")
        require_evidence_binding(self.evidence, self.application, self.rule)
        if type(self.rule.pipeline.pairing) is SingularPairing and self.coverage.paired.value > 1:
            raise ValueError("singular pairing cannot produce more than one paired unit")
        if len(self.evidence.retained) + self.evidence.omitted.value != self.counts.total:
            raise ValueError("evidence must cover every concrete fact")
        comparator = self.rule.pipeline.comparator.identity
        if any(fact.comparator != comparator for fact in self.evidence.retained):
            raise ValueError("diff evidence comparator does not match the compiled pipeline")
        require_evidence_buckets(self.evidence, self.counts)
        if self.coverage.compared.value != self.counts.evaluated:
            raise ValueError("compared coverage must equal non-ignored fact counts")
        if self.coverage.ignored.value != self.counts.ignored.value:
            raise ValueError("ignored coverage must equal ignored fact count")
        require_side_coverage(self.counts, self.coverage)
        require_evidence_side_shapes(self.evidence, self.counts, self.coverage)
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
            require_incomplete_evidence(
                pairing_stage=self.outcome.reason.stage is DiffEvaluationStage.PAIRING,
                counts=self.counts,
                coverage=self.coverage,
            )
        if type(self.outcome) is NotEvaluated:
            require_not_evaluated_evidence(
                all_ignored=self.outcome.reason is DiffNotEvaluatedReason.ALL_IGNORED,
                false_applicability=(
                    self.outcome.reason is DiffNotEvaluatedReason.FALSE_APPLICABILITY
                ),
                empty_selection=self.outcome.reason is DiffNotEvaluatedReason.EMPTY_SELECTION,
                counts=self.counts,
                coverage=self.coverage,
            )

    @property
    def severity(self) -> DiffSeverity:
        """Return effective severity from the exact bound compiled rule."""
        return self.rule.severity
