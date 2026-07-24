"""Immutable, value-free fact projections accepted by gate reduction."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.gates.coverage_validation import require_coverage_bounds
from comparo.core.domain.gates.coverage_validation import require_side_fact_coverage
from comparo.core.domain.gates.coverage_validation import require_unit_footprints
from comparo.core.domain.gates.outcome_validation import require_assertion_outcome_counts
from comparo.core.domain.gates.outcome_validation import require_diff_outcome_counts
from comparo.core.domain.gates.summary_validation import is_lifecycle_complete
from comparo.core.domain.gates.summary_validation import require_environment
from comparo.core.domain.gates.summary_validation import require_globally_unique_lineages
from comparo.core.domain.gates.summary_validation import require_lifecycle_completion
from comparo.core.domain.gates.summary_validation import require_normalized_coordinates
from comparo.core.domain.gates.summary_validation import require_rule_tuple
from comparo.core.domain.gates.summary_validation import require_unique_lineages
from comparo.core.domain.gates.types import AssertionGateOutcome
from comparo.core.domain.gates.types import DiffGateOutcome
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateRuleLineage
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.lifecycle import RunCompletion as LifecycleRunCompletion
from comparo.core.domain.primitives import Count


@final
class GateEnvironmentExecution(msgspec.Struct, frozen=True, kw_only=True):
    """One exact Environment execution and its invocation-owned role set."""

    environment: DefinitionRef
    execution_id: EnvironmentExecutionId
    roles: tuple[EnvironmentRole, ...]

    def __post_init__(self) -> None:
        """Require an exact Environment definition and execution occurrence."""
        if type(self.environment) is not DefinitionRef:
            raise TypeError("gate execution environment must be a DefinitionRef")
        if self.environment.identity.kind is not ResourceKind.ENVIRONMENT:
            raise ValueError("gate execution must address an Environment")
        if type(self.execution_id) is not EnvironmentExecutionId:
            raise TypeError("gate execution ID must be an EnvironmentExecutionId")
        if type(self.roles) is not tuple or not self.roles:
            raise ValueError("gate execution roles must be a non-empty immutable tuple")
        if any(type(role) is not EnvironmentRole for role in self.roles):
            raise TypeError("gate execution roles must contain EnvironmentRole values")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("gate execution roles must be unique")
        canonical = tuple(
            role
            for role in (
                EnvironmentRole.BASELINE,
                EnvironmentRole.CANDIDATE,
                EnvironmentRole.OTHER,
            )
            if role in self.roles
        )
        if self.roles != canonical:
            raise ValueError("gate execution roles must use canonical invocation order")
        if EnvironmentRole.OTHER in self.roles and len(self.roles) != 1:
            raise ValueError("the other role cannot be combined with comparison roles")


@final
class AssertionGateCounts(msgspec.Struct, frozen=True, kw_only=True):
    """Exact assertion counts projected without matches or evidence."""

    selected: Count
    evaluated: Count
    satisfied: Count
    violated: Count
    errors: Count

    def __post_init__(self) -> None:
        """Require exact selection and evaluation conservation."""
        counts = (self.selected, self.evaluated, self.satisfied, self.violated, self.errors)
        if any(type(count) is not Count for count in counts):
            raise TypeError("assertion gate counts must use Count values")
        resolved = self.satisfied.value + self.violated.value + self.errors.value
        if resolved != self.evaluated.value:
            raise ValueError("evaluated gate matches must equal terminal match buckets")
        if self.evaluated.value > self.selected.value:
            raise ValueError("evaluated gate matches cannot exceed selected matches")


@final
class AssertionGateRuleOutcome(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free projection of an exact assertion application fact."""

    lineage: GateRuleLineage
    execution: GateEnvironmentExecution
    outcome: AssertionGateOutcome
    counts: AssertionGateCounts

    def __post_init__(self) -> None:
        """Require typed coordinates and outcome/count consistency."""
        if type(self.lineage) is not GateRuleLineage:
            raise TypeError("assertion gate rule requires a GateRuleLineage")
        if type(self.execution) is not GateEnvironmentExecution:
            raise TypeError("assertion gate rule requires a GateEnvironmentExecution")
        if type(self.outcome) is not AssertionGateOutcome:
            raise TypeError("assertion gate rule outcome must be an AssertionGateOutcome")
        if type(self.counts) is not AssertionGateCounts:
            raise TypeError("assertion gate rule requires AssertionGateCounts")
        require_assertion_outcome_counts(
            self.outcome,
            selected=self.counts.selected.value,
            evaluated=self.counts.evaluated.value,
            satisfied=self.counts.satisfied.value,
            violated=self.counts.violated.value,
            errors=self.counts.errors.value,
        )

    @property
    def selected_matches(self) -> Count:
        """Return the exact selected-match count."""
        return self.counts.selected

    @property
    def evaluated_matches(self) -> Count:
        """Return the exact evaluated-match count."""
        return self.counts.evaluated


@final
class AssertionGateSummary(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free assertion projections for one Environment execution."""

    rules: tuple[AssertionGateRuleOutcome, ...]

    def __post_init__(self) -> None:
        """Require a non-empty unique set sharing one exact execution."""
        require_rule_tuple(self.rules, AssertionGateRuleOutcome, "assertion")
        require_unique_lineages(tuple(rule.lineage for rule in self.rules), "assertion")
        if len({rule.execution for rule in self.rules}) != 1:
            raise ValueError("assertion gate rules must share one exact Environment execution")

    @property
    def coordinate(self) -> GateEnvironmentExecution:
        """Return the exact value-free execution coordinate."""
        return self.rules[0].execution

    @property
    def roles(self) -> tuple[EnvironmentRole, ...]:
        """Return the invocation-owned role set shared by every rule."""
        return self.coordinate.roles

    @property
    def environment(self) -> DefinitionRef:
        """Return the exact Environment definition shared by every rule."""
        return self.coordinate.environment

    @property
    def execution_id(self) -> EnvironmentExecutionId:
        """Return the exact Environment execution shared by every rule."""
        return self.coordinate.execution_id


@final
class GateEnvironmentPair(msgspec.Struct, frozen=True, kw_only=True):
    """A directed pair including exact Environment execution occurrences."""

    comparison_id: ComparisonId
    baseline_environment: DefinitionRef
    baseline_execution_id: EnvironmentExecutionId
    baseline_role: EnvironmentRole
    candidate_environment: DefinitionRef
    candidate_execution_id: EnvironmentExecutionId
    candidate_role: EnvironmentRole

    def __post_init__(self) -> None:
        """Require distinct Environment resources and execution occurrences."""
        if type(self.comparison_id) is not ComparisonId:
            raise TypeError("gate pair comparison ID must be a ComparisonId")
        require_environment(self.baseline_environment, "gate pair baseline")
        require_environment(self.candidate_environment, "gate pair candidate")
        if self.baseline_environment.identity == self.candidate_environment.identity:
            raise ValueError("gate pair requires distinct Environment resources")
        if (
            type(self.baseline_execution_id) is not EnvironmentExecutionId
            or type(self.candidate_execution_id) is not EnvironmentExecutionId
        ):
            raise TypeError("gate pair execution IDs must be EnvironmentExecutionId values")
        if self.baseline_execution_id == self.candidate_execution_id:
            raise ValueError("gate pair requires distinct Environment execution IDs")
        if (
            type(self.baseline_role) is not EnvironmentRole
            or type(self.candidate_role) is not EnvironmentRole
        ):
            raise TypeError("gate pair roles must be EnvironmentRole values")
        if (
            self.baseline_role is not EnvironmentRole.BASELINE
            or self.candidate_role is not EnvironmentRole.CANDIDATE
        ):
            raise ValueError("gate pair roles must preserve baseline and candidate positions")


@final
class DiffGateCoverage(msgspec.Struct, frozen=True, kw_only=True):
    """Exact per-rule stage coverage with no field values or evidence payloads."""

    baseline_selected_values: Count
    candidate_selected_values: Count
    baseline_derived_values: Count
    candidate_derived_values: Count
    baseline_consumed_values: Count
    candidate_consumed_values: Count
    baseline_resolved_values: Count
    candidate_resolved_values: Count
    paired_units: Count
    compared_facts: Count
    ignored_facts: Count
    not_evaluated_units: Count

    def __post_init__(self) -> None:
        """Require exact counts and complete pair resolution conservation."""
        counts = (
            self.baseline_selected_values,
            self.candidate_selected_values,
            self.baseline_derived_values,
            self.candidate_derived_values,
            self.baseline_consumed_values,
            self.candidate_consumed_values,
            self.baseline_resolved_values,
            self.candidate_resolved_values,
            self.paired_units,
            self.compared_facts,
            self.ignored_facts,
            self.not_evaluated_units,
        )
        if any(type(count) is not Count for count in counts):
            raise TypeError("diff gate coverage must use Count values")
        require_coverage_bounds(
            baseline_derived=self.baseline_derived_values.value,
            candidate_derived=self.candidate_derived_values.value,
            baseline_consumed=self.baseline_consumed_values.value,
            candidate_consumed=self.candidate_consumed_values.value,
            baseline_resolved=self.baseline_resolved_values.value,
            candidate_resolved=self.candidate_resolved_values.value,
        )
        resolved = (
            self.compared_facts.value + self.ignored_facts.value + self.not_evaluated_units.value
        )
        if resolved != self.paired_units.value:
            raise ValueError("every paired gate unit must be compared, ignored, or not evaluated")
        require_unit_footprints(
            units=self.paired_units.value,
            baseline=self.baseline_consumed_values.value,
            candidate=self.candidate_consumed_values.value,
            label="paired",
        )
        require_unit_footprints(
            units=self.compared_facts.value + self.ignored_facts.value,
            baseline=self.baseline_resolved_values.value,
            candidate=self.candidate_resolved_values.value,
            label="resolved",
        )
        require_unit_footprints(
            units=self.not_evaluated_units.value,
            baseline=self.baseline_consumed_values.value - self.baseline_resolved_values.value,
            candidate=self.candidate_consumed_values.value - self.candidate_resolved_values.value,
            label="not-evaluated",
        )


@final
class DiffGateFactCounts(msgspec.Struct, frozen=True, kw_only=True):
    """Exact normalized concrete fact buckets for one diff rule."""

    same: Count
    changed: Count
    added: Count
    removed: Count
    ignored: Count
    incomparable: Count

    def __post_init__(self) -> None:
        """Require one Count for every closed concrete outcome."""
        if any(
            type(count) is not Count
            for count in (
                self.same,
                self.changed,
                self.added,
                self.removed,
                self.ignored,
                self.incomparable,
            )
        ):
            raise TypeError("diff gate fact counts must use Count values")

    @property
    def compared(self) -> int:
        """Return every non-ignored concrete fact."""
        return (
            self.same.value
            + self.changed.value
            + self.added.value
            + self.removed.value
            + self.incomparable.value
        )


@final
class DiffGateRuleOutcome(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free projection of an exact directed diff application fact."""

    lineage: GateRuleLineage
    pair: GateEnvironmentPair
    outcome: DiffGateOutcome
    counts: DiffGateFactCounts
    coverage: DiffGateCoverage

    def __post_init__(self) -> None:
        """Require typed coordinates, coverage conservation, and outcome truth."""
        if type(self.lineage) is not GateRuleLineage:
            raise TypeError("diff gate rule requires a GateRuleLineage")
        if type(self.pair) is not GateEnvironmentPair:
            raise TypeError("diff gate rule requires a GateEnvironmentPair")
        if type(self.outcome) is not DiffGateOutcome:
            raise TypeError("diff gate rule outcome must be a DiffGateOutcome")
        if type(self.counts) is not DiffGateFactCounts:
            raise TypeError("diff gate rule requires DiffGateFactCounts")
        if type(self.coverage) is not DiffGateCoverage:
            raise TypeError("diff gate rule requires DiffGateCoverage")
        if self.coverage.compared_facts.value != self.counts.compared:
            raise ValueError("compared gate coverage must equal non-ignored fact counts")
        if self.coverage.ignored_facts != self.counts.ignored:
            raise ValueError("ignored gate coverage must equal ignored fact count")
        require_side_fact_coverage(
            baseline_resolved=self.coverage.baseline_resolved_values.value,
            candidate_resolved=self.coverage.candidate_resolved_values.value,
            same=self.counts.same.value,
            changed=self.counts.changed.value,
            added=self.counts.added.value,
            removed=self.counts.removed.value,
            ignored=self.counts.ignored.value,
            incomparable=self.counts.incomparable.value,
        )
        require_diff_outcome_counts(
            self.outcome,
            same=self.counts.same.value,
            changed=self.counts.changed.value,
            added=self.counts.added.value,
            removed=self.counts.removed.value,
            ignored=self.counts.ignored.value,
            incomparable=self.counts.incomparable.value,
            baseline_derived=self.coverage.baseline_derived_values.value,
            candidate_derived=self.coverage.candidate_derived_values.value,
            baseline_consumed=self.coverage.baseline_consumed_values.value,
            candidate_consumed=self.coverage.candidate_consumed_values.value,
            baseline_resolved=self.coverage.baseline_resolved_values.value,
            candidate_resolved=self.coverage.candidate_resolved_values.value,
            compared=self.coverage.compared_facts.value,
            not_evaluated=self.coverage.not_evaluated_units.value,
        )

    @property
    def incomparable(self) -> bool:
        """Return whether this exact rule produced incomparable facts."""
        return self.counts.incomparable.value > 0


@final
class DiffGateSummary(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free diff projections for one directed execution pair."""

    rules: tuple[DiffGateRuleOutcome, ...]

    def __post_init__(self) -> None:
        """Require a non-empty unique set sharing one exact directed pair."""
        require_rule_tuple(self.rules, DiffGateRuleOutcome, "diff")
        require_unique_lineages(tuple(rule.lineage for rule in self.rules), "diff")
        if len({rule.pair for rule in self.rules}) != 1:
            raise ValueError("diff gate rules must share one exact directed execution pair")

    @property
    def pair(self) -> GateEnvironmentPair:
        """Return the exact value-free pair shared by every rule."""
        return self.rules[0].pair

    @property
    def baseline_role(self) -> EnvironmentRole:
        """Return the invocation-owned baseline role."""
        return self.pair.baseline_role

    @property
    def candidate_role(self) -> EnvironmentRole:
        """Return the invocation-owned candidate role."""
        return self.pair.candidate_role


@final
class NormalizedFactSummary(msgspec.Struct, frozen=True, kw_only=True):
    """The complete value-free input accepted by the pure gate reducer."""

    completion: LifecycleRunCompletion
    assertions: tuple[AssertionGateSummary, ...] = ()
    diffs: tuple[DiffGateSummary, ...] = ()

    def __post_init__(self) -> None:
        """Reject duplicate source facts or applications across all summaries."""
        require_lifecycle_completion(self.completion)
        if type(self.assertions) is not tuple or any(
            type(item) is not AssertionGateSummary for item in self.assertions
        ):
            raise TypeError("normalized assertions must contain AssertionGateSummary values")
        if type(self.diffs) is not tuple or any(
            type(item) is not DiffGateSummary for item in self.diffs
        ):
            raise TypeError("normalized diffs must contain DiffGateSummary values")
        diff_pairs = tuple(item.pair for item in self.diffs)
        require_normalized_coordinates(
            tuple((item.environment, item.execution_id, item.roles) for item in self.assertions),
            tuple(
                (
                    pair.comparison_id,
                    pair.baseline_environment,
                    pair.baseline_execution_id,
                    pair.candidate_environment,
                    pair.candidate_execution_id,
                )
                for pair in diff_pairs
            ),
        )
        assertion_lineages = tuple(
            rule.lineage for summary in self.assertions for rule in summary.rules
        )
        diff_lineages = tuple(rule.lineage for summary in self.diffs for rule in summary.rules)
        require_globally_unique_lineages((*assertion_lineages, *diff_lineages))

    @property
    def is_complete(self) -> bool:
        """Return lifecycle-backed completeness without a caller-authored duplicate claim."""
        return is_lifecycle_complete(self.completion)
