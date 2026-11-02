"""Cover aggregate precedence and partial comparison accounting edges."""

from __future__ import annotations

import msgspec
import pytest

import comparo.core.diffs.aggregation as diff_aggregation
from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.aggregation import _aggregate_outcome
from comparo.core.diffs.aggregation import _provenance
from comparo.core.diffs.aggregation import aggregate_diff
from comparo.core.diffs.aggregation import counts_from_totals
from comparo.core.diffs.evidence import EvidenceCandidate
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import PairUnit
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffFact
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.strategies import OrderedPairing
from comparo.core.domain.primitives import Count
from comparo.core.domain.values import JsonInteger
from tests.core.diffs.factories import application
from tests.core.diffs.factories import derived
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import meter
from tests.core.diffs.factories import rule
from tests.core.diffs.factories import snapshots
from tests.core.domain.diffs._invariant_support import _operation


def _counts(
    *,
    same: int = 0,
    changed: int = 0,
    added: int = 0,
    removed: int = 0,
    ignored: int = 0,
    incomparable: int = 0,
) -> DiffFactCounts:
    return DiffFactCounts(
        same=Count(value=same),
        changed=Count(value=changed),
        added=Count(value=added),
        removed=Count(value=removed),
        ignored=Count(value=ignored),
        incomparable=Count(value=incomparable),
    )


def test_aggregate_outcome_precedence_is_closed_and_directed() -> None:
    incomparable = _aggregate_outcome(_counts(incomparable=1), None, False)
    assert type(incomparable) is Incomplete
    assert incomparable.reason.stage is DiffEvaluationStage.COMPARISON
    assert type(_aggregate_outcome(_counts(same=1), None, True)) is Incomplete
    for counts in (_counts(changed=1), _counts(added=1), _counts(removed=1)):
        assert type(_aggregate_outcome(counts, None, False)) is Drifted
    assert type(_aggregate_outcome(_counts(ignored=1), None, False)) is NotEvaluated
    assert type(_aggregate_outcome(_counts(same=1), None, False)) is Equivalent


def test_aggregate_retains_first_incomparable_and_omits_under_zero_evidence() -> None:
    baseline_snapshot, candidate_snapshot = snapshots()
    base = rule()
    compiled = msgspec.structs.replace(
        base,
        pipeline=DiffPipeline(
            baseline=base.pipeline.baseline,
            candidate=base.pipeline.candidate,
            pairing=OrderedPairing(identity=_operation(DiffOperationFamily.PAIRING, "ordered")),
            comparator=base.pipeline.comparator,
        ),
    )
    bound = application(compiled, baseline_snapshot, candidate_snapshot)
    left = derived(JsonInteger(value=1))
    left_two = derived(JsonInteger(value=1), index=101)
    right = derived(JsonInteger(value=1), candidate=True)
    right_two = derived(JsonInteger(value=1), candidate=True, index=102)
    first = DiagnosticCode(value="diff.pairing.first")
    second = DiagnosticCode(value="diff.pairing.second")
    pairs = (
        PairUnit(baseline=left, candidate=right, incomparable=first),
        PairUnit(baseline=left_two, candidate=right_two, incomparable=second),
    )
    result = aggregate_diff(
        bound,
        compiled,
        inputs(compiled),
        meter(evidence=0),
        2,
        2,
        (left, left_two),
        (right, right_two),
        pairs,
        partial_input=True,
    )
    assert type(result.outcome) is Incomplete
    assert result.outcome.issues[0].code == first
    assert len(result.outcome.issues) == 2
    assert result.evidence.omitted.value == 2
    assert result.evidence.exhaustion is not None


def test_aggregate_helpers_preserve_zero_counts_and_absent_provenance() -> None:
    assert _provenance(None) is None
    totals = dict.fromkeys(DiffFactOutcome, 0)
    assert counts_from_totals(totals).total == 0


def test_evidence_work_halt_leaves_current_pair_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_snapshot, candidate_snapshot = snapshots()
    compiled = rule()
    bound = application(compiled, baseline_snapshot, candidate_snapshot)
    left = derived(JsonInteger(value=1))
    right = derived(JsonInteger(value=1), candidate=True)
    pair = PairUnit(baseline=left, candidate=right)

    def halt_during_evidence(
        candidates: list[EvidenceCandidate],
        fact: DiffFact,
        ordinal: int,
        diff_meter: DiffMeter,
    ) -> int | None:
        del candidates, fact, ordinal
        failure = diff_meter.charge_work(200_000)
        assert failure is not None
        raise DiffHaltError(exhaustion=failure)

    monkeypatch.setattr(diff_aggregation, "consider_evidence", halt_during_evidence)
    result = aggregate_diff(
        bound,
        compiled,
        inputs(compiled),
        meter(),
        1,
        1,
        (left,),
        (right,),
        (pair,),
    )

    assert type(result.outcome) is Incomplete
    assert result.counts.total == 0
    assert result.coverage.not_evaluated.value == 1
    assert result.coverage.baseline_resolved.value == 0
    assert result.coverage.candidate_resolved.value == 0
    assert result.evidence.retained == ()
    assert result.evidence.omitted.value == 0
    assert result.evidence.exhaustion is None
