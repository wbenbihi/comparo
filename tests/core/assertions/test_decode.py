"""Exercise dedicated body-view decodability as an observation-state assertion."""

from __future__ import annotations

import msgspec

from comparo.core.assertions import evaluate_assertion
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.selection import FailedBodyView
from comparo.core.selection import SelectionSnapshot
from tests.core.assertions.factories import application
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import single_stream_execution
from tests.core.selection.test_observation_json_integration import _body_selector


def test_decode_failure_is_a_state_violation_with_zero_predicate_counts() -> None:
    observation = single_stream_execution(ViewKind.SSE)
    terminal_response = observation.attempts[-1].exchanges[-1].response
    assert terminal_response is not None
    assert type(terminal_response.body) is ResponseEntityBody
    assert terminal_response.body.handle is not None
    failed = FailedBodyView(
        source=terminal_response.body.handle,
        requested_view=BodyViewKind.JSON,
        outcome=ViewDecodeError(
            decoder=decoder(ViewKind.JSON),
            code=DecodeErrorCode.INVALID_SYNTAX,
        ),
    )
    prepared_snapshot = SelectionSnapshot(observation=observation, body_views=(failed,))
    compiled = msgspec.structs.replace(
        rule(),
        selector=_body_selector(NoPath(), ValueKind.UNKNOWN),
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.DECODES_AS,
            version="1",
        ),
        operator=DecodeOperator(view=BodyViewKind.JSON),
        quantifier=None,
    )

    evaluated = evaluate_assertion(
        prepared_snapshot,
        application(compiled, prepared_snapshot),
        compiled,
        inputs(compiled, prepared_snapshot),
        control(),
    )

    assert type(evaluated.fact.outcome) is Violated
    assert evaluated.fact.outcome.reason is ViolationReason.STATE
    assert evaluated.fact.counts.evaluated.value == 0
    assert evaluated.fact.evidence.retained == ()
