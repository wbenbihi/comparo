"""Cover bounded adverse-first evidence selection and structural footprint branches."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from comparo.core.diffs.evidence import EvidenceCandidate
from comparo.core.diffs.evidence import consider_evidence
from comparo.core.diffs.evidence import evidence_footprint
from comparo.core.diffs.evidence import evidence_priority
from comparo.core.diffs.evidence import finalize_evidence
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.results import DiffFact
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.results import DiffValueProvenance
from comparo.core.domain.identity import FactId
from tests.core.diffs.factories import meter
from tests.core.domain.diffs._invariant_support import _location
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _value_provenance


def _fact(outcome: DiffFactOutcome, ordinal: int = 1) -> DiffFact:
    baseline: DiffValueProvenance | None = _value_provenance(_location(600 + ordinal))
    candidate: DiffValueProvenance | None = _value_provenance(
        _location(700 + ordinal, candidate=True)
    )
    if outcome is DiffFactOutcome.ADDED:
        baseline = None
    elif outcome in {
        DiffFactOutcome.REMOVED,
        DiffFactOutcome.IGNORED,
        DiffFactOutcome.INCOMPARABLE,
    }:
        candidate = None
    return DiffFact(
        fact_id=FactId(value=UUID(int=800 + ordinal)),
        outcome=outcome,
        comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        baseline=baseline,
        candidate=candidate,
        diagnostic=(
            DiagnosticCode(value="diff.evidence.incomparable")
            if outcome is DiffFactOutcome.INCOMPARABLE
            else None
        ),
    )


@pytest.mark.parametrize(
    ("outcome", "priority"),
    [
        (DiffFactOutcome.INCOMPARABLE, 4),
        (DiffFactOutcome.CHANGED, 3),
        (DiffFactOutcome.ADDED, 3),
        (DiffFactOutcome.REMOVED, 3),
        (DiffFactOutcome.SAME, 1),
        (DiffFactOutcome.IGNORED, 0),
    ],
)
def test_evidence_priority_is_explicit_for_every_fact_outcome(
    outcome: DiffFactOutcome,
    priority: int,
) -> None:
    assert evidence_priority(outcome) == priority


def test_consider_evidence_short_circuits_zero_capacity() -> None:
    candidates: list[EvidenceCandidate] = []
    assert consider_evidence(candidates, _fact(DiffFactOutcome.SAME), 0, meter(evidence=0)) == 1
    assert candidates == []
    assert (
        consider_evidence(
            candidates,
            _fact(DiffFactOutcome.SAME),
            0,
            meter(evidence_bytes=0),
        )
        == 1
    )


def test_consider_evidence_keeps_adverse_first_and_reports_evicted_size() -> None:
    candidates: list[EvidenceCandidate] = []
    diff_meter = meter(evidence=1, evidence_bytes=100_000)
    same = _fact(DiffFactOutcome.SAME, 1)
    changed = _fact(DiffFactOutcome.CHANGED, 2)
    assert consider_evidence(candidates, same, 1, diff_meter) is None
    same_size = candidates[0].size
    assert consider_evidence(candidates, changed, 2, diff_meter) == same_size
    assert [candidate.fact for candidate in candidates] == [changed]


def test_consider_evidence_rejects_candidate_larger_than_byte_capacity() -> None:
    candidates: list[EvidenceCandidate] = []
    dropped = consider_evidence(
        candidates,
        _fact(DiffFactOutcome.SAME),
        1,
        meter(evidence=1, evidence_bytes=1),
    )
    assert dropped == 2
    assert candidates == []


def test_finalize_evidence_charges_in_ordinal_order() -> None:
    first = _fact(DiffFactOutcome.SAME, 1)
    second = _fact(DiffFactOutcome.CHANGED, 2)
    candidates = [
        EvidenceCandidate(fact=second, size=1, ordinal=2),
        EvidenceCandidate(fact=first, size=1, ordinal=1),
    ]
    retained, exhaustion = finalize_evidence(
        candidates,
        None,
        2,
        meter(evidence=2, evidence_bytes=2),
    )
    assert retained == (first, second)
    assert exhaustion is None


def test_finalize_evidence_preserves_first_failure_and_omission_probe() -> None:
    candidates = [
        EvidenceCandidate(
            fact=_fact(DiffFactOutcome.CHANGED, ordinal),
            size=1,
            ordinal=ordinal,
        )
        for ordinal in (1, 2)
    ]
    retained, exhaustion = finalize_evidence(
        candidates,
        None,
        2,
        meter(evidence=0, evidence_bytes=0),
    )
    assert retained == ()
    assert exhaustion is not None

    retained, exhaustion = finalize_evidence(
        [],
        None,
        1,
        meter(evidence=0),
    )
    assert retained == ()
    assert exhaustion is not None


def test_evidence_footprint_covers_safe_structural_vocabulary() -> None:
    fact = _fact(DiffFactOutcome.SAME)
    payload = [
        None,
        True,
        1,
        "é",
        b"x",
        UUID(int=1),
        DiffFactOutcome.SAME,
        ("tuple",),
        ["list"],
        frozenset({"set"}),
        fact,
        object(),
    ]
    assert evidence_footprint(cast("DiffFact", payload), meter()) > 1
    assert evidence_footprint(fact, meter()) > 1


def test_evidence_footprint_ceiling_is_exact_and_rejects_invalid_limits() -> None:
    for invalid in (-1, True):
        with pytest.raises(ValueError, match="non-negative integer"):
            evidence_footprint(_fact(DiffFactOutcome.SAME), meter(), maximum=invalid)
    assert evidence_footprint(cast("DiffFact", "é"), meter(), maximum=0) == 1
    assert evidence_footprint(cast("DiffFact", 1), meter(), maximum=0) == 1
    assert evidence_footprint(cast("DiffFact", ()), meter(), maximum=None) == 8


def test_evidence_footprint_honors_work_budget_before_traversal() -> None:
    with pytest.raises(DiffHaltError) as caught:
        evidence_footprint(_fact(DiffFactOutcome.SAME), meter(work=0))
    assert caught.value.exhaustion is not None
