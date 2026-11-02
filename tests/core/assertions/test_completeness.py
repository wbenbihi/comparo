"""Exercise exact stream extent admission at the assertion boundary."""

from __future__ import annotations

import msgspec

from comparo.core.assertions import evaluate_assertion
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.operators import ExactValueType
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import StringRelation
from comparo.core.domain.assertions.operators import TypeOperator
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.http.stream_termination import EventCountWindowEvidence
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonString
from tests.core.assertions.factories import application
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.domain.http.stream_factories import location
from tests.core.selection.test_body_evaluator_coverage import _selector as _body_selector
from tests.core.selection.test_body_evaluator_coverage import _view_snapshot
from tests.core.selection.test_stream_evaluator_coverage import _event
from tests.core.selection.test_stream_evaluator_coverage import _selector
from tests.core.selection.test_stream_evaluator_coverage import _snapshot
from tests.core.selection.test_stream_evaluator_coverage import _stream


def _stream_rule(completeness: CompletenessRequirement) -> CompiledAssertionRule:
    return msgspec.structs.replace(
        rule(),
        selector=_selector(SelectorSource.STREAM_EVENTS, kind=ValueKind.OBJECT),
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.IS_TYPE,
            version="1",
        ),
        operator=TypeOperator(expected=ExactValueType.OBJECT),
        quantifier=Quantifier.ANY,
        completeness=completeness,
    )


def test_complete_rejects_authored_window_while_closed_window_accepts_it() -> None:
    complete_stream = _stream(StreamKind.SSE, records=1)
    window = msgspec.structs.replace(
        complete_stream,
        segments=(
            msgspec.structs.replace(
                complete_stream.segments[0],
                end_reason=SegmentEndReason.WINDOW_CLOSED,
            ),
        ),
        extent=StreamExtent(
            completeness=StreamCompleteness.WINDOW,
            termination=StreamTerminationReason.EVENT_COUNT_WINDOW,
        ),
        termination_evidence=EventCountWindowEvidence(limit=PositiveCount(value=1)),
    )
    prepared_snapshot = _snapshot(window, (_event(),))
    complete = _stream_rule(CompletenessRequirement.COMPLETE)
    closed = _stream_rule(CompletenessRequirement.CLOSED_WINDOW)

    complete_result = evaluate_assertion(
        prepared_snapshot,
        application(complete, prepared_snapshot),
        complete,
        inputs(complete, prepared_snapshot),
        control(),
    )
    closed_result = evaluate_assertion(
        prepared_snapshot,
        application(closed, prepared_snapshot),
        closed,
        inputs(closed, prepared_snapshot),
        control(),
    )

    assert type(complete_result.fact.outcome) is AssertionUnavailable
    assert type(closed_result.fact.outcome) is Satisfied


def test_incomplete_stream_requires_explicit_partial_prefix() -> None:
    incomplete_stream = _stream(StreamKind.SSE, records=1, incomplete=True)
    prepared_snapshot = _snapshot(incomplete_stream, (_event(),))
    complete = _stream_rule(CompletenessRequirement.COMPLETE)
    partial = _stream_rule(CompletenessRequirement.PARTIAL_PREFIX)

    complete_result = evaluate_assertion(
        prepared_snapshot,
        application(complete, prepared_snapshot),
        complete,
        inputs(complete, prepared_snapshot),
        control(),
    )
    partial_result = evaluate_assertion(
        prepared_snapshot,
        application(partial, prepared_snapshot),
        partial,
        inputs(partial, prepared_snapshot),
        control(),
    )

    assert type(complete_result.fact.outcome) is AssertionUnavailable
    assert type(partial_result.fact.outcome) is Satisfied


def test_body_view_prefix_never_proves_a_generic_value_predicate() -> None:
    prepared_snapshot, _resolved = _view_snapshot(
        ViewKind.TEXT,
        BodyViewKind.TEXT,
        JsonString(value="prefix"),
        completeness=ViewCompleteness.PREFIX,
    )
    base = rule()
    assert type(base.operator) is EqualityOperator
    selector = _body_selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.TEXT,
        kind=ValueKind.STRING,
    )
    expression = base.operator.expected
    cases = (
        (
            AssertionOperatorKind.EQUALITY,
            EqualityOperator(
                relation=EqualityRelation.EQUALS,
                expected=msgspec.structs.replace(
                    expression,
                    value=JsonString(value="prefix"),
                ),
            ),
        ),
        (
            AssertionOperatorKind.EQUALITY,
            EqualityOperator(
                relation=EqualityRelation.EQUALS,
                expected=msgspec.structs.replace(
                    expression,
                    value=JsonString(value="prefix-suffix"),
                ),
            ),
        ),
        (
            AssertionOperatorKind.STRING,
            StringOperator(
                relation=StringRelation.CONTAINS,
                expected=msgspec.structs.replace(
                    expression,
                    value=JsonString(value="fix"),
                ),
            ),
        ),
    )

    for kind, operator in cases:
        compiled = msgspec.structs.replace(
            base,
            selector=selector,
            operator_id=AssertionOperatorIdentity(
                namespace="comparo.core",
                kind=kind,
                version="1",
            ),
            operator=operator,
            quantifier=Quantifier.ALL,
            completeness=CompletenessRequirement.PARTIAL_PREFIX,
        )
        evaluated = evaluate_assertion(
            prepared_snapshot,
            application(compiled, prepared_snapshot),
            compiled,
            inputs(compiled, prepared_snapshot),
            control(),
        )

        assert type(evaluated.fact.outcome) is AssertionUnavailable
        assert evaluated.fact.outcome.reason is AssertionUnavailableReason.INCOMPLETE
        assert evaluated.fact.counts.evaluated.value == 0


def test_mid_sequence_budget_exhaustion_preserves_exact_partial_coverage() -> None:
    complete_stream = _stream(StreamKind.SSE, records=2)
    second = msgspec.structs.replace(
        _event(item=1),
        location=location(item=1, category=1, start=0, end=0),
    )
    prepared_snapshot = _snapshot(complete_stream, (_event(item=0), second))
    compiled = msgspec.structs.replace(
        _stream_rule(CompletenessRequirement.COMPLETE),
        quantifier=Quantifier.ALL,
    )

    for limited in (control(work=132), control(facts=1)):
        evaluated = evaluate_assertion(
            prepared_snapshot,
            application(compiled, prepared_snapshot),
            compiled,
            inputs(compiled, prepared_snapshot),
            limited,
        )

        assert type(evaluated.fact.outcome) is AssertionUnavailable
        assert evaluated.fact.outcome.reason is AssertionUnavailableReason.BUDGET_EXCEEDED
        assert evaluated.fact.outcome.exhaustion is not None
        assert evaluated.fact.counts.selected.value == 2
        assert evaluated.fact.counts.evaluated.value == 1
        assert evaluated.fact.counts.satisfied.value == 1
        assert len(evaluated.fact.evidence.retained) == 1


def test_decisive_quantifier_proof_stops_before_a_later_budget_failure() -> None:
    complete_stream = _stream(StreamKind.SSE, records=2)
    second = msgspec.structs.replace(
        _event(item=1),
        location=location(item=1, category=1, start=0, end=0),
    )
    prepared_snapshot = _snapshot(complete_stream, (_event(item=0), second))
    compiled = _stream_rule(CompletenessRequirement.COMPLETE)

    evaluated = evaluate_assertion(
        prepared_snapshot,
        application(compiled, prepared_snapshot),
        compiled,
        inputs(compiled, prepared_snapshot),
        control(work=132),
    )

    assert type(evaluated.fact.outcome) is Satisfied
    assert evaluated.fact.counts.selected.value == 2
    assert evaluated.fact.counts.evaluated.value == 1
    assert evaluated.fact.counts.satisfied.value == 1
