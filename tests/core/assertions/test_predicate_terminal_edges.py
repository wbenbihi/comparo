"""Predicate operand, prepared-capability, digest, and error terminals."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from hashlib import sha512
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.assertions.evidence import _match_outcome
from comparo.core.assertions.inputs import PreparedCapabilityKind
from comparo.core.assertions.inputs import PreparedExpression
from comparo.core.assertions.inputs import PreparedOperandError
from comparo.core.assertions.inputs import PreparedOperandReady
from comparo.core.assertions.inputs import PreparedOperandUnavailable
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.assertions.predicates import PredicateError
from comparo.core.assertions.predicates import PredicateSatisfied
from comparo.core.assertions.predicates import PredicateUnavailable
from comparo.core.assertions.predicates import PredicateViolated
from comparo.core.assertions.predicates import evaluate_predicate
from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaRelation
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import TokenOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import BetweenOperator
from comparo.core.domain.assertions.operators import ContainsMode
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import CountRelation
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import KeySetRelation
from comparo.core.domain.assertions.operators import LengthOperator
from comparo.core.domain.assertions.operators import OrderingOperator
from comparo.core.domain.assertions.operators import OrderingRelation
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import StringRelation
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.assertions.test_predicates import _evaluate
from tests.core.assertions.test_predicates import _inputs
from tests.core.assertions.test_predicates import _match
from tests.core.assertions.test_predicates import _predicate
from tests.core.domain.assertions.test_contracts import _literal
from tests.core.domain.assertions.test_contracts import _regex
from tests.core.selection.test_body_evaluator_coverage import _response_payload_snapshot


def test_operand_and_prepared_capability_terminal_branches() -> None:
    operator = rule().operator
    expression = cast("LiteralExpr", operator.expected)
    base = _inputs(operator)
    unavailable = msgspec.structs.replace(
        base,
        expressions=(
            PreparedExpression(
                expression=expression,
                result=PreparedOperandUnavailable(code=DiagnosticCode(value="operand.missing")),
            ),
        ),
    )
    errored = msgspec.structs.replace(
        base,
        expressions=(
            PreparedExpression(
                expression=expression,
                result=PreparedOperandError(code=DiagnosticCode(value="operand.bad")),
            ),
        ),
    )
    match = _match(JsonInteger(value=200))
    assert (
        type(evaluate_predicate(match, operator, unavailable, snapshot())) is PredicateUnavailable
    )
    assert type(evaluate_predicate(match, operator, errored, snapshot())) is PredicateError

    regex = _regex()
    for outcome, expected in (
        (PreparedPredicateOutcome.VIOLATED, PredicateViolated),
        (PreparedPredicateOutcome.UNAVAILABLE, PredicateUnavailable),
        (PreparedPredicateOutcome.ERROR, PredicateError),
    ):
        code = (
            DiagnosticCode(value="capability.failed")
            if outcome in {PreparedPredicateOutcome.UNAVAILABLE, PreparedPredicateOutcome.ERROR}
            else None
        )
        predicate = _predicate(
            regex,
            match,
            PreparedCapabilityKind.REGEX,
            outcome=outcome,
            code=code,
        )
        assert (
            type(evaluate_predicate(match, regex, _inputs(regex, predicate=predicate), snapshot()))
            is expected
        )


def test_predicate_invalid_runtime_types_and_helpers() -> None:
    assert (
        type(
            _evaluate(
                JsonString(value="x"),
                OrderingOperator(
                    relation=OrderingRelation.LESS, expected=_literal(JsonInteger(value=1))
                ),
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                JsonString(value="x"),
                ApproximateOperator(
                    expected=_literal(JsonInteger(value=1)),
                    absolute_tolerance=_literal(JsonInteger(value=2)),
                ),
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                JsonInteger(value=1),
                StringOperator(
                    relation=StringRelation.CONTAINS, expected=_literal(JsonString(value="1"))
                ),
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                BytesValue(value=b"x"),
                ContainsOperator(
                    mode=ContainsMode.ONE, expected=(_literal(JsonString(value="x")),)
                ),
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                JsonString(value="x"), KeySetOperator(relation=KeySetRelation.HAS, keys=("x",))
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                JsonInteger(value=1),
                LengthOperator(relation=CountRelation.EQUALS, expected=Count(value=1)),
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                JsonString(value="text"),
                MediaTypeOperator(
                    relation=MediaRelation.IGNORE_PARAMETERS,
                    expected=MediaType(type="text", subtype="plain"),
                ),
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                JsonString(value="text"),
                DigestOperator(
                    algorithm=DigestAlgorithm.SHA512, expected=_literal(BytesValue(value=b"x" * 64))
                ),
            )
        )
        is PredicateError
    )

    assert _match_outcome(PredicateSatisfied()) == (MatchOutcome.SATISFIED, None, None)
    assert _match_outcome(PredicateViolated()) == (MatchOutcome.VIOLATED, None, None)
    assert (
        _match_outcome(PredicateError(code=DiagnosticCode(value="operand.bad")))[0]
        is MatchOutcome.ERROR
    )
    with pytest.raises(TypeError):
        _match_outcome(
            PredicateUnavailable(
                code=DiagnosticCode(value="operand.missing"),
                reason=AssertionUnavailableReason.OPERAND,
            )
        )


def test_remaining_predicate_relation_and_error_branches() -> None:
    assert (
        type(
            _evaluate(
                JsonString(value="x"),
                BetweenOperator(
                    lower=_literal(JsonInteger(value=1)),
                    upper=_literal(JsonInteger(value=2)),
                    include_lower=True,
                    include_upper=True,
                ),
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                JsonString(value="x"),
                TokenOperator(token=MediaTypeTokenValue(value="gzip")),
            )
        )
        is PredicateError
    )
    assert (
        type(
            _evaluate(
                JsonObject(members=(JsonObjectMember(name="id", value=JsonInteger(value=1)),)),
                KeySetOperator(relation=KeySetRelation.HAS, keys=("id",)),
            )
        )
        is PredicateSatisfied
    )
    assert (
        type(
            _evaluate(
                JsonArray(items=(JsonInteger(value=1),)),
                LengthOperator(relation=CountRelation.EQUALS, expected=Count(value=1)),
            )
        )
        is PredicateSatisfied
    )

    parameter = MediaTypeParameter(name="charset", value=MediaTypeTokenValue(value="utf-8"))
    actual_media = MediaType(type="application", subtype="json", parameters=(parameter,))
    for relation, expected_media, expected_result in (
        (
            MediaRelation.EXACT,
            MediaType(type="text", subtype="plain"),
            PredicateViolated,
        ),
        (
            MediaRelation.EXACT,
            actual_media,
            PredicateSatisfied,
        ),
        (
            MediaRelation.SUBSET_PARAMETERS,
            MediaType(type="application", subtype="json", parameters=(parameter,)),
            PredicateSatisfied,
        ),
        (
            MediaRelation.IGNORE_PARAMETERS,
            MediaType(type="application", subtype="json"),
            PredicateSatisfied,
        ),
    ):
        assert (
            type(
                _evaluate(
                    actual_media,
                    MediaTypeOperator(relation=relation, expected=expected_media),
                )
            )
            is expected_result
        )

    approximate = ApproximateOperator(
        expected=_literal(JsonInteger(value=10)),
        absolute_tolerance=_literal(JsonInteger(value=1)),
        relative_tolerance=_literal(JsonDecimal(value=Decimal("0.1"))),
    )
    assert type(_evaluate(JsonInteger(value=11), approximate)) is PredicateSatisfied
    relative_only = ApproximateOperator(
        expected=_literal(JsonInteger(value=10)),
        relative_tolerance=_literal(JsonDecimal(value=Decimal("0.1"))),
    )
    assert type(_evaluate(JsonInteger(value=11), relative_only)) is PredicateSatisfied
    prepared = _inputs(approximate)
    negative = msgspec.structs.replace(
        prepared,
        expressions=(
            *prepared.expressions[:-1],
            msgspec.structs.replace(
                prepared.expressions[-1],
                result=PreparedOperandReady(value=JsonDecimal(value=Decimal("-0.1"))),
            ),
        ),
    )
    assert (
        type(evaluate_predicate(_match(JsonInteger(value=10)), approximate, negative, snapshot()))
        is PredicateError
    )

    assert (
        type(
            evaluate_predicate(
                _match(JsonInteger(value=1)),
                CardinalityOperator(exact=Count(value=1)),
                _inputs(CardinalityOperator(exact=Count(value=1))),
                snapshot(),
            )
        )
        is PredicateError
    )
    body_id = BodyId(value=UUID(int=999_999))
    body_match = SelectionMatch(
        value=body_id,
        location=BodyLocation(
            environment_execution_id=snapshot().observation.execution_id,
            source=SelectorSource.RESPONSE_BODY,
            body_id=body_id,
            body_view=BodyViewKind.HANDLE,
        ),
    )
    body_digest = DigestOperator(
        algorithm=DigestAlgorithm.SHA512,
        expected=_literal(BytesValue(value=b"x" * 64)),
    )
    assert (
        type(evaluate_predicate(body_match, body_digest, _inputs(body_digest), snapshot()))
        is PredicateUnavailable
    )
    resolved_snapshot, resolved_body_id = _response_payload_snapshot(b"data")
    resolved_match = SelectionMatch(
        value=resolved_body_id,
        location=BodyLocation(
            environment_execution_id=resolved_snapshot.observation.execution_id,
            source=SelectorSource.RESPONSE_BODY,
            body_id=resolved_body_id,
            body_view=BodyViewKind.HANDLE,
        ),
    )
    resolved_digest = DigestOperator(
        algorithm=DigestAlgorithm.SHA512,
        expected=_literal(BytesValue(value=sha512(b"data").digest())),
    )
    assert (
        type(
            evaluate_predicate(
                resolved_match,
                resolved_digest,
                _inputs(resolved_digest),
                resolved_snapshot,
            )
        )
        is PredicateUnavailable
    )
    text_digest = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(JsonString(value=sha256(b"payload").hexdigest())),
    )
    assert type(_evaluate(BytesValue(value=b"payload"), text_digest)) is PredicateUnavailable
    invalid_digest_type = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(JsonInteger(value=1)),
    )
    assert type(_evaluate(BytesValue(value=b"payload"), invalid_digest_type)) is PredicateError
    assert (
        type(
            _evaluate(
                BytesValue(value=b"payload"),
                DigestOperator(
                    algorithm=DigestAlgorithm.SHA256,
                    expected=_literal(BytesValue(value=b"x")),
                ),
            )
        )
        is PredicateError
    )
