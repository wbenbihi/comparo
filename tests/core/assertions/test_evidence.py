"""Verify deterministic evidence accounting and counterexample priority."""

from __future__ import annotations

import msgspec

from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.assertions.evidence import EvaluatedMatches
from comparo.core.assertions.evidence import evaluate_matches
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from comparo.core.selection import select
from tests.core.assertions.factories import application
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot


def test_counterexample_is_retained_before_earlier_pass_sample() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    selected = select(prepared_snapshot, compiled.selector, control().selection)
    assert type(selected.result) is SelectionReady
    match = selected.result.matches[0]
    matches = (
        match,
        msgspec.structs.replace(match, value=JsonInteger(value=201)),
    )
    meter = AssertionMeter(control(evidence=1).budgets)

    evaluated = evaluate_matches(
        matches,
        compiled,
        application(compiled, prepared_snapshot),
        inputs(compiled, prepared_snapshot),
        prepared_snapshot,
        meter,
    )

    assert type(evaluated) is EvaluatedMatches
    assert evaluated.counts.evaluated.value == 2
    assert evaluated.counts.satisfied.value == 1
    assert evaluated.counts.violated.value == 1
    assert len(evaluated.evidence.retained) == 1
    assert evaluated.evidence.retained[0].outcome is MatchOutcome.VIOLATED
    assert evaluated.evidence.omitted.value == 1


def test_sensitive_evidence_is_measured_then_truncated_without_rendering() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    selected = select(prepared_snapshot, compiled.selector, control().selection)
    assert type(selected.result) is SelectionReady
    secret_digits = "9876543210123456789"
    sealed = SensitiveValue(
        JsonInteger(value=int(secret_digits)),
        origins=(TaintOriginId(value="response.status"),),
    )
    match = msgspec.structs.replace(selected.result.matches[0], value=sealed)
    meter = AssertionMeter(control(evidence=1, evidence_bytes=1).budgets)

    evaluated = evaluate_matches(
        (match,),
        compiled,
        application(compiled, prepared_snapshot),
        inputs(compiled, prepared_snapshot),
        prepared_snapshot,
        meter,
    )

    assert type(evaluated) is EvaluatedMatches
    assert evaluated.evidence.retained == ()
    assert evaluated.evidence.truncation is not None
    assert secret_digits not in repr(evaluated)
