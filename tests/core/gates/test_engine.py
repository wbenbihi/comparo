"""Exercise pure gate reduction, coverage, thresholds, and fail-closed edges."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.gates.model import GateFailureReason
from comparo.core.domain.gates.model import GateRuleCounts
from comparo.core.domain.gates.model import GateThreshold
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.gates.summaries import AssertionGateCounts
from comparo.core.domain.gates.summaries import AssertionGateRuleOutcome
from comparo.core.domain.gates.summaries import AssertionGateSummary
from comparo.core.domain.gates.summaries import DiffGateCoverage
from comparo.core.domain.gates.summaries import DiffGateRuleOutcome
from comparo.core.domain.gates.summaries import DiffGateSummary
from comparo.core.domain.gates.summaries import NormalizedFactSummary
from comparo.core.domain.gates.types import AssertionGateOutcome
from comparo.core.domain.gates.types import DiffGateOutcome
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.lifecycle import PartialRunReason
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import UtcInstant
from comparo.core.gates import evaluate_gate
from tests.core.domain.gates.test_invariants import _assertion_rule
from tests.core.domain.gates.test_invariants import _complete
from tests.core.domain.gates.test_invariants import _definition
from tests.core.domain.gates.test_invariants import _diff_rule
from tests.core.domain.gates.test_invariants import _empty_fact_counts


def _count(value: int) -> Count:
    return Count(value=value)


def _threshold(
    severity: GateSeverity,
    *,
    violated: int = 0,
    drifted: int = 0,
    unavailable: int = 0,
    incomparable: int = 0,
    incomplete: int = 0,
    errors: int = 0,
) -> GateThreshold:
    return GateThreshold(
        severity=severity,
        violated_rules=_count(violated),
        drifted_rules=_count(drifted),
        unavailable_rules=_count(unavailable),
        incomparable_rules=_count(incomparable),
        incomplete_rules=_count(incomplete),
        error_rules=_count(errors),
    )


def _profile(
    *,
    severities: tuple[GateSeverity, ...] = (GateSeverity.BLOCKING,),
    roles: tuple[EnvironmentRole, ...] = (
        EnvironmentRole.BASELINE,
        EnvironmentRole.CANDIDATE,
    ),
    complete: bool = False,
    assertion_coverage: bool = False,
    diff_coverage: bool = False,
    thresholds: tuple[GateThreshold, ...] | None = None,
) -> CompiledGateProfile:
    return CompiledGateProfile(
        definition=_definition(),
        severities=severities,
        environment_roles=roles,
        require_complete_run=complete,
        require_assertion_coverage=assertion_coverage,
        require_diff_coverage=diff_coverage,
        thresholds=(
            tuple(_threshold(severity) for severity in severities)
            if thresholds is None
            else thresholds
        ),
    )


def _summary(
    *,
    assertions: tuple[AssertionGateRuleOutcome, ...] = (),
    diffs: tuple[DiffGateRuleOutcome, ...] = (),
    partial: bool = False,
) -> NormalizedFactSummary:
    completion = (
        RunPartial(
            reasons=(PartialRunReason.EVALUATION_INCOMPLETE,),
            completed_at=UtcInstant(unix_nanoseconds=2),
        )
        if partial
        else _complete()
    )
    return NormalizedFactSummary(
        completion=completion,
        assertions=(AssertionGateSummary(rules=assertions),) if assertions else (),
        diffs=(DiffGateSummary(rules=diffs),) if diffs else (),
    )


def _assertion(
    index: int,
    outcome: AssertionGateOutcome,
    *,
    severity: GateSeverity = GateSeverity.BLOCKING,
    selected: int = 0,
    satisfied: int = 0,
    violated: int = 0,
    errors: int = 0,
    roles: tuple[EnvironmentRole, ...] = (EnvironmentRole.BASELINE,),
) -> AssertionGateRuleOutcome:
    evaluated = satisfied + violated + errors
    source = _assertion_rule(index)
    return msgspec.structs.replace(
        source,
        lineage=msgspec.structs.replace(source.lineage, severity=severity),
        execution=msgspec.structs.replace(source.execution, roles=roles),
        outcome=outcome,
        counts=AssertionGateCounts(
            selected=_count(selected),
            evaluated=_count(evaluated),
            satisfied=_count(satisfied),
            violated=_count(violated),
            errors=_count(errors),
        ),
    )


def _diff(
    index: int,
    outcome: DiffGateOutcome,
    *,
    severity: GateSeverity = GateSeverity.BLOCKING,
    same: int = 0,
    changed: int = 0,
    added: int = 0,
    removed: int = 0,
    ignored: int = 0,
    incomparable: int = 0,
    not_evaluated: int = 0,
) -> DiffGateRuleOutcome:
    compared = same + changed + added + removed + incomparable
    paired = compared + ignored + not_evaluated
    resolved = compared + ignored
    source = _diff_rule(index)
    return msgspec.structs.replace(
        source,
        lineage=msgspec.structs.replace(source.lineage, severity=severity),
        outcome=outcome,
        counts=msgspec.structs.replace(
            _empty_fact_counts(),
            same=_count(same),
            changed=_count(changed),
            added=_count(added),
            removed=_count(removed),
            ignored=_count(ignored),
            incomparable=_count(incomparable),
        ),
        coverage=DiffGateCoverage(
            baseline_selected_values=_count(paired),
            candidate_selected_values=_count(paired),
            baseline_derived_values=_count(paired),
            candidate_derived_values=_count(paired),
            baseline_consumed_values=_count(paired),
            candidate_consumed_values=_count(paired),
            baseline_resolved_values=_count(resolved),
            candidate_resolved_values=_count(resolved),
            paired_units=_count(paired),
            compared_facts=_count(compared),
            ignored_facts=_count(ignored),
            not_evaluated_units=_count(not_evaluated),
        ),
    )


def test_absent_profile_is_not_requested_and_present_profile_is_auditable() -> None:
    summary = _summary()
    not_requested = evaluate_gate(None, summary)
    assert not_requested.result.verdict is GateVerdict.NOT_REQUESTED
    assert not_requested.coverage is None
    assert not_requested.severities == ()

    assertion = _assertion(80, AssertionGateOutcome.SATISFIED)
    diff = _diff(100, DiffGateOutcome.EQUIVALENT, same=1)
    evaluated = evaluate_gate(
        _profile(assertion_coverage=True, diff_coverage=True),
        _summary(assertions=(assertion,), diffs=(diff,)),
    )
    assert evaluated.result.verdict is GateVerdict.PASSED
    assert evaluated.result.profile == _definition()
    assert evaluated.coverage is not None
    assert evaluated.coverage.eligible_assertion_rules.value == 1
    assert evaluated.coverage.covered_assertion_rules.value == 1
    assert evaluated.coverage.eligible_diff_rules.value == 1
    assert evaluated.coverage.covered_diff_rules.value == 1
    assert evaluated.severities[0].actual == _zero_rule_counts(evaluated.severities[0].actual)


def _zero_rule_counts(counts: GateRuleCounts) -> GateRuleCounts:
    assert all(
        count.value == 0
        for count in (
            counts.violated_rules,
            counts.drifted_rules,
            counts.unavailable_rules,
            counts.incomparable_rules,
            counts.incomplete_rules,
            counts.error_rules,
        )
    )
    return counts


@pytest.mark.parametrize(
    ("assertion", "covered"),
    [
        (_assertion(80, AssertionGateOutcome.SATISFIED), 1),
        (_assertion(82, AssertionGateOutcome.VIOLATED), 1),
        (_assertion(84, AssertionGateOutcome.SKIPPED), 0),
        (_assertion(86, AssertionGateOutcome.UNAVAILABLE), 0),
        (_assertion(88, AssertionGateOutcome.ERROR, selected=1, errors=1), 0),
        (
            _assertion(
                90,
                AssertionGateOutcome.ERROR,
                selected=2,
                satisfied=1,
                errors=1,
            ),
            1,
        ),
    ],
)
def test_assertion_coverage_is_conclusive_including_zero_cardinality(
    assertion: AssertionGateRuleOutcome,
    covered: int,
) -> None:
    evaluation = evaluate_gate(
        _profile(
            assertion_coverage=True,
            thresholds=(_threshold(GateSeverity.BLOCKING, unavailable=1, errors=1),),
        ),
        _summary(assertions=(assertion,)),
    )
    assert evaluation.coverage is not None
    assert evaluation.coverage.covered_assertion_rules.value == covered
    assert (GateFailureReason.ASSERTION_COVERAGE_EMPTY in evaluation.result.reasons) is not bool(
        covered
    )


@pytest.mark.parametrize(
    ("diff", "covered"),
    [
        (_diff(100, DiffGateOutcome.EQUIVALENT, same=1), 1),
        (_diff(102, DiffGateOutcome.DRIFTED, changed=1), 1),
        (_diff(104, DiffGateOutcome.NOT_EVALUATED), 0),
        (_diff(106, DiffGateOutcome.NOT_EVALUATED, ignored=1), 0),
        (_diff(108, DiffGateOutcome.INCOMPLETE, incomparable=1), 0),
    ],
)
def test_diff_coverage_excludes_empty_ignored_and_incomparable_only_rules(
    diff: DiffGateRuleOutcome,
    covered: int,
) -> None:
    evaluation = evaluate_gate(
        _profile(
            diff_coverage=True,
            thresholds=(_threshold(GateSeverity.BLOCKING, incomparable=1, incomplete=1),),
        ),
        _summary(diffs=(diff,)),
    )
    assert evaluation.coverage is not None
    assert evaluation.coverage.covered_diff_rules.value == covered
    assert (GateFailureReason.DIFF_COVERAGE_EMPTY in evaluation.result.reasons) is not bool(covered)


def test_known_adverse_facts_survive_error_and_incomplete_parent_outcomes() -> None:
    assertion = _assertion(
        80,
        AssertionGateOutcome.ERROR,
        selected=2,
        violated=1,
        errors=1,
    )
    diff = _diff(
        100,
        DiffGateOutcome.INCOMPLETE,
        changed=1,
        not_evaluated=1,
    )
    evaluation = evaluate_gate(
        _profile(
            thresholds=(_threshold(GateSeverity.BLOCKING, incomplete=1, errors=1),),
        ),
        _summary(assertions=(assertion,), diffs=(diff,)),
    )
    assert evaluation.result.reasons == (
        GateFailureReason.ASSERTION_VIOLATION_LIMIT,
        GateFailureReason.DIFF_DRIFT_LIMIT,
    )
    actual = evaluation.severities[0].actual
    assert actual.violated_rules.value == 1
    assert actual.drifted_rules.value == 1
    assert actual.incomplete_rules.value == 1
    assert actual.error_rules.value == 1


def test_threshold_equality_passes_and_one_more_rule_fails() -> None:
    first = _assertion(80, AssertionGateOutcome.VIOLATED)
    second = _assertion(82, AssertionGateOutcome.VIOLATED)
    profile = _profile(
        thresholds=(_threshold(GateSeverity.BLOCKING, violated=1),),
    )
    assert (
        evaluate_gate(profile, _summary(assertions=(first,))).result.verdict is GateVerdict.PASSED
    )
    failed = evaluate_gate(profile, _summary(assertions=(first, second)))
    assert failed.result.reasons == (GateFailureReason.ASSERTION_VIOLATION_LIMIT,)


def test_severity_and_role_filters_are_exact_and_do_not_double_count() -> None:
    dual_role_warning = _assertion(
        80,
        AssertionGateOutcome.VIOLATED,
        severity=GateSeverity.WARNING,
        roles=(EnvironmentRole.BASELINE, EnvironmentRole.CANDIDATE),
    )
    out_of_scope = _assertion(
        82,
        AssertionGateOutcome.VIOLATED,
        roles=(EnvironmentRole.OTHER,),
    )
    diff = _diff(100, DiffGateOutcome.DRIFTED, changed=1)
    baseline_only = _profile(
        severities=(GateSeverity.WARNING, GateSeverity.BLOCKING),
        roles=(EnvironmentRole.BASELINE,),
        thresholds=(
            _threshold(GateSeverity.WARNING, violated=1),
            _threshold(GateSeverity.BLOCKING),
        ),
    )
    evaluation = evaluate_gate(
        baseline_only,
        _summary(assertions=(dual_role_warning,), diffs=(diff,)),
    )
    assert evaluation.result.verdict is GateVerdict.PASSED
    assert evaluation.coverage is not None
    assert evaluation.coverage.eligible_assertion_rules.value == 1
    assert evaluation.coverage.eligible_diff_rules.value == 0
    assert tuple(item.severity for item in evaluation.severities) == (
        GateSeverity.BLOCKING,
        GateSeverity.WARNING,
    )
    assert evaluation.severities[1].actual.violated_rules.value == 1

    filtered = evaluate_gate(baseline_only, _summary(assertions=(out_of_scope,)))
    assert filtered.result.verdict is GateVerdict.PASSED
    assert filtered.coverage is not None
    assert filtered.coverage.eligible_assertion_rules.value == 0


def test_all_failures_are_reported_once_in_canonical_order_without_early_exit() -> None:
    assertion_rules = (
        _assertion(80, AssertionGateOutcome.VIOLATED),
        _assertion(82, AssertionGateOutcome.UNAVAILABLE),
        _assertion(84, AssertionGateOutcome.ERROR, selected=1, errors=1),
    )
    diff_rules = (
        _diff(100, DiffGateOutcome.DRIFTED, changed=1),
        _diff(102, DiffGateOutcome.INCOMPLETE, incomparable=1),
        _diff(104, DiffGateOutcome.ERROR),
    )
    evaluation = evaluate_gate(
        _profile(complete=True),
        _summary(assertions=assertion_rules, diffs=diff_rules, partial=True),
    )
    assert evaluation.result.reasons == (
        GateFailureReason.RUN_INCOMPLETE,
        GateFailureReason.ASSERTION_VIOLATION_LIMIT,
        GateFailureReason.DIFF_DRIFT_LIMIT,
        GateFailureReason.UNAVAILABLE_LIMIT,
        GateFailureReason.INCOMPARABLE_RULE_LIMIT,
        GateFailureReason.INCOMPLETE_LIMIT,
        GateFailureReason.ERROR_LIMIT,
    )


def test_out_of_order_profile_dimensions_produce_deterministic_assessments() -> None:
    profile = _profile(
        severities=(GateSeverity.INFO, GateSeverity.BLOCKING, GateSeverity.WARNING),
        thresholds=(
            _threshold(GateSeverity.WARNING),
            _threshold(GateSeverity.INFO),
            _threshold(GateSeverity.BLOCKING),
        ),
    )
    evaluation = evaluate_gate(profile, _summary())
    assert tuple(item.severity for item in evaluation.severities) == (
        GateSeverity.BLOCKING,
        GateSeverity.WARNING,
        GateSeverity.INFO,
    )


def test_gate_rejects_untrusted_boundary_objects() -> None:
    with pytest.raises(TypeError, match="profile"):
        evaluate_gate(cast("CompiledGateProfile", "bad"), _summary())
    with pytest.raises(TypeError, match="summary"):
        evaluate_gate(None, cast("NormalizedFactSummary", "bad"))


def test_adverse_count_is_per_rule_not_per_concrete_match_or_fact() -> None:
    assertion = _assertion(
        80,
        AssertionGateOutcome.VIOLATED,
        selected=3,
        violated=3,
    )
    diff = _diff(100, DiffGateOutcome.DRIFTED, changed=3)
    evaluation = evaluate_gate(
        _profile(
            thresholds=(_threshold(GateSeverity.BLOCKING, violated=1, drifted=1),),
        ),
        _summary(assertions=(assertion,), diffs=(diff,)),
    )
    assert evaluation.result.verdict is GateVerdict.PASSED
    assert evaluation.severities[0].actual.violated_rules.value == 1
    assert evaluation.severities[0].actual.drifted_rules.value == 1


@pytest.mark.parametrize("allowed", range(4))
def test_widening_one_threshold_is_monotonic(allowed: int) -> None:
    rules = tuple(_assertion(80 + index * 2, AssertionGateOutcome.VIOLATED) for index in range(3))
    evaluation = evaluate_gate(
        _profile(
            thresholds=(_threshold(GateSeverity.BLOCKING, violated=allowed),),
        ),
        _summary(assertions=rules),
    )
    assert (evaluation.result.verdict is GateVerdict.PASSED) is (allowed >= len(rules))
