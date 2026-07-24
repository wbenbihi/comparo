"""Source-bound, value-free per-rule outcomes accepted by gate reduction."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Skipped
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.gates.types import AssertionGateOutcome
from comparo.core.domain.gates.types import DiffGateOutcome
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateRuleLineage
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.gates.types import RunCompletion
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count


@final
class AssertionGateRuleOutcome(msgspec.Struct, frozen=True, kw_only=True):
    """One exact normalized assertion rule result with no selected values."""

    fact: AssertionFact

    def __post_init__(self) -> None:
        """Require the exact evaluator fact from which all gate claims derive."""
        if type(self.fact) is not AssertionFact:
            raise TypeError("assertion gate rule requires an AssertionFact")

    @property
    def lineage(self) -> GateRuleLineage:
        """Derive immutable gate lineage from the bound fact and compiled rule."""
        return _assertion_lineage(self.fact)

    @property
    def outcome(self) -> AssertionGateOutcome:
        """Derive the gate outcome from the exact evaluator outcome."""
        return _assertion_outcome(self.fact)

    @property
    def selected_matches(self) -> Count:
        """Return the exact selected-match count carried by the source fact."""
        return self.fact.counts.selected

    @property
    def evaluated_matches(self) -> Count:
        """Return the exact evaluated-match count carried by the source fact."""
        return self.fact.counts.evaluated


@final
class AssertionGateSummary(msgspec.Struct, frozen=True, kw_only=True):
    """Source-bound assertion rule outcomes for one Environment execution."""

    role: EnvironmentRole
    rules: tuple[AssertionGateRuleOutcome, ...]

    def __post_init__(self) -> None:
        """Require a non-empty, unique set of exact per-rule outcomes."""
        if type(self.role) is not EnvironmentRole:
            raise TypeError("assertion gate summary role must be an EnvironmentRole")
        _require_rule_tuple(self.rules, AssertionGateRuleOutcome, "assertion")
        _require_unique_lineages(tuple(rule.lineage for rule in self.rules), "assertion")
        coordinates = {
            (
                rule.fact.application.environment,
                rule.fact.application.environment_execution_id,
            )
            for rule in self.rules
        }
        if len(coordinates) != 1:
            raise ValueError("assertion gate rules must share one exact Environment execution")

    @property
    def environment(self) -> DefinitionRef:
        """Return the exact Environment definition bound by every source fact."""
        return self.rules[0].fact.application.environment

    @property
    def execution_id(self) -> EnvironmentExecutionId:
        """Return the exact Environment execution bound by every source fact."""
        return self.rules[0].fact.application.environment_execution_id


@final
class GateEnvironmentPair(msgspec.Struct, frozen=True, kw_only=True):
    """A directed pair including exact Environment execution occurrences."""

    comparison_id: ComparisonId
    baseline_environment: ResourceIdentity
    baseline_execution_id: EnvironmentExecutionId
    baseline_role: EnvironmentRole
    candidate_environment: ResourceIdentity
    candidate_execution_id: EnvironmentExecutionId
    candidate_role: EnvironmentRole

    def __post_init__(self) -> None:
        """Require distinct Environment resources and execution occurrences."""
        if type(self.comparison_id) is not ComparisonId:
            raise TypeError("gate pair comparison ID must be a ComparisonId")
        _require_environment(self.baseline_environment, "gate pair baseline")
        _require_environment(self.candidate_environment, "gate pair candidate")
        if self.baseline_environment == self.candidate_environment:
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


@final
class DiffGateCoverage(msgspec.Struct, frozen=True, kw_only=True):
    """Exact per-rule stage coverage with no field values or evidence payloads."""

    baseline_selected_values: Count
    candidate_selected_values: Count
    baseline_derived_values: Count
    candidate_derived_values: Count
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
            self.paired_units,
            self.compared_facts,
            self.ignored_facts,
            self.not_evaluated_units,
        )
        if any(type(count) is not Count for count in counts):
            raise TypeError("diff gate coverage must use Count values")
        resolved = (
            self.compared_facts.value + self.ignored_facts.value + self.not_evaluated_units.value
        )
        if resolved != self.paired_units.value:
            raise ValueError("every paired gate unit must be compared, ignored, or not evaluated")


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
    """One exact normalized diff rule outcome and its value-free fact coverage."""

    fact: DiffRuleFact

    def __post_init__(self) -> None:
        """Require the exact evaluator fact from which all gate claims derive."""
        if type(self.fact) is not DiffRuleFact:
            raise TypeError("diff gate rule requires a DiffRuleFact")

    @property
    def lineage(self) -> GateRuleLineage:
        """Derive immutable gate lineage from the bound fact and compiled rule."""
        return _diff_lineage(self.fact)

    @property
    def outcome(self) -> DiffGateOutcome:
        """Derive the gate outcome from the exact evaluator outcome."""
        return _diff_outcome(self.fact)

    @property
    def counts(self) -> DiffGateFactCounts:
        """Project exact value-free fact buckets from the source fact."""
        return DiffGateFactCounts(
            same=self.fact.counts.same,
            changed=self.fact.counts.changed,
            added=self.fact.counts.added,
            removed=self.fact.counts.removed,
            ignored=self.fact.counts.ignored,
            incomparable=self.fact.counts.incomparable,
        )

    @property
    def coverage(self) -> DiffGateCoverage:
        """Project exact value-free coverage from the source fact."""
        return DiffGateCoverage(
            baseline_selected_values=self.fact.coverage.baseline_selected,
            candidate_selected_values=self.fact.coverage.candidate_selected,
            baseline_derived_values=self.fact.coverage.baseline_derived,
            candidate_derived_values=self.fact.coverage.candidate_derived,
            paired_units=self.fact.coverage.paired,
            compared_facts=self.fact.coverage.compared,
            ignored_facts=self.fact.coverage.ignored,
            not_evaluated_units=self.fact.coverage.not_evaluated,
        )

    @property
    def incomparable(self) -> bool:
        """Derive whether this exact rule produced incomparable facts."""
        return self.counts.incomparable.value > 0


@final
class DiffGateSummary(msgspec.Struct, frozen=True, kw_only=True):
    """Source-bound diff rule outcomes for one directed execution pair."""

    baseline_role: EnvironmentRole
    candidate_role: EnvironmentRole
    rules: tuple[DiffGateRuleOutcome, ...]

    def __post_init__(self) -> None:
        """Require a typed pair and non-empty unique exact per-rule outcomes."""
        if (
            type(self.baseline_role) is not EnvironmentRole
            or type(self.candidate_role) is not EnvironmentRole
        ):
            raise TypeError("diff gate summary roles must be EnvironmentRole values")
        _require_rule_tuple(self.rules, DiffGateRuleOutcome, "diff")
        _require_unique_lineages(tuple(rule.lineage for rule in self.rules), "diff")
        pairs = {rule.fact.application.pair for rule in self.rules}
        if len(pairs) != 1:
            raise ValueError("diff gate rules must share one exact directed execution pair")

    @property
    def pair(self) -> GateEnvironmentPair:
        """Derive the gate pair from exact source facts and invocation-owned roles."""
        source = self.rules[0].fact.application.pair
        return GateEnvironmentPair(
            comparison_id=source.comparison_id,
            baseline_environment=source.baseline_environment,
            baseline_execution_id=source.baseline_execution_id,
            baseline_role=self.baseline_role,
            candidate_environment=source.candidate_environment,
            candidate_execution_id=source.candidate_execution_id,
            candidate_role=self.candidate_role,
        )


@final
class NormalizedFactSummary(msgspec.Struct, frozen=True, kw_only=True):
    """The complete value-free input accepted by the pure gate reducer."""

    completion: RunCompletion
    assertions: tuple[AssertionGateSummary, ...] = ()
    diffs: tuple[DiffGateSummary, ...] = ()

    def __post_init__(self) -> None:
        """Reject duplicate source facts or applications across all summaries."""
        if type(self.completion) is not RunCompletion:
            raise TypeError("normalized fact completion must be a RunCompletion")
        if type(self.assertions) is not tuple or any(
            type(item) is not AssertionGateSummary for item in self.assertions
        ):
            raise TypeError("normalized assertions must contain AssertionGateSummary values")
        if type(self.diffs) is not tuple or any(
            type(item) is not DiffGateSummary for item in self.diffs
        ):
            raise TypeError("normalized diffs must contain DiffGateSummary values")
        assertion_coordinates = tuple(
            (item.environment, item.execution_id, item.role) for item in self.assertions
        )
        if len(set(assertion_coordinates)) != len(assertion_coordinates):
            raise ValueError("normalized assertion summary coordinates must be unique")
        diff_pairs = tuple(item.pair for item in self.diffs)
        if len(set(diff_pairs)) != len(diff_pairs):
            raise ValueError("normalized diff summary coordinates must be unique")
        assertion_lineages = tuple(
            rule.lineage for summary in self.assertions for rule in summary.rules
        )
        diff_lineages = tuple(rule.lineage for summary in self.diffs for rule in summary.rules)
        lineages = (*assertion_lineages, *diff_lineages)
        fact_ids = tuple(lineage.source_fact_id for lineage in lineages)
        application_ids = tuple(lineage.application_id for lineage in lineages)
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("normalized source fact IDs must be globally unique")
        if len(set(application_ids)) != len(application_ids):
            raise ValueError("normalized rule applications must be globally unique")


def _require_environment(environment: ResourceIdentity, context: str) -> None:
    if type(environment) is not ResourceIdentity:
        raise TypeError(f"{context} environment must be a ResourceIdentity")
    if environment.kind is not ResourceKind.ENVIRONMENT:
        raise ValueError(f"{context} must address an Environment")


def _require_rule_tuple(rules: tuple[object, ...], rule_type: type[object], label: str) -> None:
    if type(rules) is not tuple or not rules:
        raise ValueError(f"{label} gate rules must be a non-empty immutable tuple")
    if any(type(rule) is not rule_type for rule in rules):
        raise TypeError(f"{label} gate summary contains an unsupported rule outcome")


def _require_unique_lineages(lineages: tuple[GateRuleLineage, ...], label: str) -> None:
    if len(set(lineages)) != len(lineages):
        raise ValueError(f"{label} gate rule lineages must be unique")


def _assertion_lineage(fact: AssertionFact) -> GateRuleLineage:
    return GateRuleLineage(
        source_fact_id=fact.fact_id,
        application_id=fact.application.application_id,
        rule_id=fact.rule.definition.rule_id,
        effective_id=fact.rule.effective_id,
        rule_fingerprint=fact.rule.fingerprint,
        severity=GateSeverity(fact.rule.severity.value),
    )


def _diff_lineage(fact: DiffRuleFact) -> GateRuleLineage:
    return GateRuleLineage(
        source_fact_id=fact.fact_id,
        application_id=fact.application.application_id,
        rule_id=fact.rule.definition.rule_id,
        effective_id=fact.rule.effective_id,
        rule_fingerprint=fact.rule.fingerprint,
        severity=GateSeverity(fact.rule.severity.value),
    )


def _assertion_outcome(fact: AssertionFact) -> AssertionGateOutcome:
    mapping = {
        Satisfied: AssertionGateOutcome.SATISFIED,
        Violated: AssertionGateOutcome.VIOLATED,
        Skipped: AssertionGateOutcome.SKIPPED,
        AssertionUnavailable: AssertionGateOutcome.UNAVAILABLE,
        AssertionError: AssertionGateOutcome.ERROR,
    }
    return mapping[type(fact.outcome)]


def _diff_outcome(fact: DiffRuleFact) -> DiffGateOutcome:
    mapping = {
        Equivalent: DiffGateOutcome.EQUIVALENT,
        Drifted: DiffGateOutcome.DRIFTED,
        Incomplete: DiffGateOutcome.INCOMPLETE,
        DiffEvaluationError: DiffGateOutcome.ERROR,
        NotEvaluated: DiffGateOutcome.NOT_EVALUATED,
    }
    return mapping[type(fact.outcome)]
