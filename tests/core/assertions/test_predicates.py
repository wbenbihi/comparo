"""Conformance tests for pure typed assertion predicates."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import cast

import msgspec
import pytest

from comparo.core.assertions import PreparedAssertionInputs
from comparo.core.assertions import PreparedCapabilityKind
from comparo.core.assertions import PreparedExpression
from comparo.core.assertions import PreparedOperandReady
from comparo.core.assertions import PreparedPredicate
from comparo.core.assertions import PreparedPredicateOutcome
from comparo.core.assertions import capability_implementation
from comparo.core.assertions import operands_fingerprint
from comparo.core.assertions import operation_fingerprint
from comparo.core.assertions import selected_fingerprint
from comparo.core.assertions.inputs import PreparedOperatorEvidence
from comparo.core.assertions.inputs import operator_expressions
from comparo.core.assertions.predicates import PredicateError
from comparo.core.assertions.predicates import PredicateSatisfied
from comparo.core.assertions.predicates import PredicateUnavailable
from comparo.core.assertions.predicates import PredicateViolated
from comparo.core.assertions.predicates import evaluate_predicate
from comparo.core.domain.assertions.evidence import AssertionOperatorEvidence
from comparo.core.domain.assertions.evidence import AssertionOperatorEvidenceValue
from comparo.core.domain.assertions.evidence import DigestEvidence
from comparo.core.domain.assertions.evidence import SchemaValidationEvidence
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaRelation
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import TokenOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.operators import BetweenOperator
from comparo.core.domain.assertions.operators import ContainsMode
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import CountRelation
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import KeySetRelation
from comparo.core.domain.assertions.operators import LengthOperator
from comparo.core.domain.assertions.operators import MembershipOperator
from comparo.core.domain.assertions.operators import MembershipRelation
from comparo.core.domain.assertions.operators import OrderingOperator
from comparo.core.domain.assertions.operators import OrderingRelation
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import StringRelation
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.evaluation import semantic_equal
from comparo.core.selection import select
from tests.core.assertions.factories import control
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.domain.assertions.test_contracts import _literal
from tests.core.domain.assertions.test_contracts import _regex
from tests.core.domain.assertions.test_contracts import _schema


def _match(value: SelectedValue) -> SelectionMatch:
    prepared_snapshot = snapshot()
    selected = select(prepared_snapshot, rule().selector, control().selection)
    ready = selected.result
    assert hasattr(ready, "matches")
    return msgspec.structs.replace(ready.matches[0], value=value)


def _inputs(
    operator: AssertionOperator,
    *,
    predicate: PreparedPredicate | None = None,
) -> PreparedAssertionInputs:
    prepared_snapshot = snapshot()
    expressions_list: list[PreparedExpression] = []
    for expression in operator_expressions(operator):
        assert type(expression) is LiteralExpr
        expressions_list.append(
            PreparedExpression(
                expression=expression,
                result=PreparedOperandReady(value=expression.value),
            )
        )
    return PreparedAssertionInputs(
        rule_fingerprint=rule().fingerprint,
        environment_execution_id=prepared_snapshot.observation.execution_id,
        expressions=tuple(expressions_list),
        predicates=() if predicate is None else (predicate,),
    )


def _predicate(
    operator: AssertionOperator,
    match: SelectionMatch,
    kind: PreparedCapabilityKind,
    *,
    outcome: PreparedPredicateOutcome = PreparedPredicateOutcome.SATISFIED,
    code: DiagnosticCode | None = None,
    evidence: AssertionOperatorEvidenceValue | None = None,
) -> PreparedPredicate:
    prepared = _inputs(operator)
    operands = tuple(
        item.result.value
        for item in prepared.expressions
        if type(item.result) is PreparedOperandReady
    )
    if evidence is None and kind is PreparedCapabilityKind.JSON_SCHEMA:
        evidence = SchemaValidationEvidence(violation_count=Count(value=0))
    if evidence is None and kind is PreparedCapabilityKind.DIGEST:
        digest = cast("DigestOperator", operator)
        expected = operands[0]
        actual_digest = (
            expected.value
            if type(expected) is BytesValue
            else bytes.fromhex(cast("JsonString", expected).value)
        )
        evidence = DigestEvidence(
            algorithm=digest.algorithm,
            actual=BytesValue(value=actual_digest),
        )
    selected_binding = selected_fingerprint(match)
    operands_binding = operands_fingerprint(operands)
    origins = tuple(
        origin
        for binding in (selected_binding, operands_binding)
        if is_sensitive_value(binding)
        for origin in binding.origins
    )
    if evidence is not None and origins and not is_sensitive_value(evidence):
        evidence = SensitiveValue(
            cast("AssertionOperatorEvidence", evidence),
            origins=tuple(dict.fromkeys(origins)),
        )
    return PreparedPredicate(
        kind=kind,
        selector_fingerprint=rule().selector.fingerprint,
        operation_fingerprint=operation_fingerprint(operator),
        implementation=capability_implementation(operator),
        selected_fingerprint=selected_binding,
        operands_fingerprint=operands_binding,
        environment_execution_id=snapshot().observation.execution_id,
        location=match.location,
        outcome=outcome,
        code=code,
        evidence=cast("PreparedOperatorEvidence | None", evidence),
    )


def _evaluate(value: SelectedValue, operator: AssertionOperator) -> object:
    return evaluate_predicate(_match(value), operator, _inputs(operator), snapshot())


def test_structural_equality_ignores_object_order_but_not_array_order() -> None:
    first = JsonObject(
        members=(
            JsonObjectMember(name="a", value=JsonInteger(value=1)),
            JsonObjectMember(name="b", value=JsonInteger(value=2)),
        )
    )
    reordered = JsonObject(members=tuple(reversed(first.members)))

    assert semantic_equal(first, reordered)
    assert not semantic_equal(
        JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))),
        JsonArray(items=(JsonInteger(value=2), JsonInteger(value=1))),
    )


@pytest.mark.parametrize(
    ("actual", "operator", "expected_type"),
    [
        (
            JsonInteger(value=2),
            EqualityOperator(
                relation=EqualityRelation.EQUALS,
                expected=_literal(JsonInteger(value=2)),
            ),
            PredicateSatisfied,
        ),
        (
            JsonInteger(value=2),
            MembershipOperator(
                relation=MembershipRelation.NOT_IN,
                choices=(_literal(JsonInteger(value=3)),),
            ),
            PredicateSatisfied,
        ),
        (
            JsonInteger(value=2),
            OrderingOperator(
                relation=OrderingRelation.GREATER,
                expected=_literal(JsonInteger(value=1)),
            ),
            PredicateSatisfied,
        ),
        (
            JsonInteger(value=2),
            BetweenOperator(
                lower=_literal(JsonInteger(value=2)),
                upper=_literal(JsonInteger(value=3)),
                include_lower=True,
                include_upper=False,
            ),
            PredicateSatisfied,
        ),
        (
            JsonDecimal(value=Decimal("1.1")),
            ApproximateOperator(
                expected=_literal(JsonInteger(value=1)),
                absolute_tolerance=_literal(JsonDecimal(value=Decimal("0.1"))),
            ),
            PredicateSatisfied,
        ),
        (
            JsonString(value="abcdef"),
            StringOperator(
                relation=StringRelation.STARTS_WITH,
                expected=_literal(JsonString(value="abc")),
            ),
            PredicateSatisfied,
        ),
        (
            JsonArray(items=(JsonString(value="x"),)),
            ContainsOperator(
                mode=ContainsMode.ONE,
                expected=(_literal(JsonString(value="x")),),
            ),
            PredicateSatisfied,
        ),
        (
            JsonObject(members=(JsonObjectMember(name="id", value=JsonInteger(value=1)),)),
            KeySetOperator(relation=KeySetRelation.HAS_ONLY, keys=("id",)),
            PredicateSatisfied,
        ),
        (
            BytesValue(value=b"abc"),
            LengthOperator(relation=CountRelation.EQUALS, expected=Count(value=3)),
            PredicateSatisfied,
        ),
        (
            HttpFieldItems(items=(MediaTypeTokenValue(value="gzip"),)),
            TokenOperator(token=MediaTypeTokenValue(value="gzip")),
            PredicateSatisfied,
        ),
        (
            MediaType(type="application", subtype="problem+json"),
            MediaTypeOperator(
                relation=MediaRelation.SUFFIX_MATCH,
                expected=MediaType(type="application", subtype="*+json"),
            ),
            PredicateSatisfied,
        ),
        (
            BytesValue(value=b"payload"),
            DigestOperator(
                algorithm=DigestAlgorithm.SHA256,
                expected=_literal(BytesValue(value=sha256(b"payload").digest())),
            ),
            PredicateUnavailable,
        ),
        (
            JsonInteger(value=1),
            EqualityOperator(
                relation=EqualityRelation.NOT_EQUALS,
                expected=_literal(JsonInteger(value=1)),
            ),
            PredicateViolated,
        ),
    ],
)
def test_built_in_predicates_are_typed_and_deterministic(
    actual: SelectedValue,
    operator: AssertionOperator,
    expected_type: type[object],
) -> None:
    assert type(_evaluate(actual, operator)) is expected_type


@pytest.mark.parametrize(
    ("operator", "kind"),
    [
        (_regex(), PreparedCapabilityKind.REGEX),
        (_schema(), PreparedCapabilityKind.JSON_SCHEMA),
    ],
)
def test_prepared_regex_and_schema_never_execute_or_fetch(
    operator: AssertionOperator,
    kind: PreparedCapabilityKind,
) -> None:
    match = _match(JsonString(value="xxx"))
    missing = evaluate_predicate(match, operator, _inputs(operator), snapshot())
    prepared = _predicate(operator, match, kind)
    ready = evaluate_predicate(match, operator, _inputs(operator, predicate=prepared), snapshot())

    assert type(missing) is PredicateUnavailable
    assert type(ready) is PredicateSatisfied


def test_dynamic_inverted_between_bounds_are_operand_errors() -> None:
    operator = BetweenOperator(
        lower=_literal(JsonInteger(value=3)),
        upper=_literal(JsonInteger(value=2)),
        include_lower=True,
        include_upper=True,
    )

    assert type(_evaluate(JsonInteger(value=2), operator)) is PredicateError


@pytest.mark.parametrize("expected", ["f" * 63, "F" * 64, "g" * 64])
def test_text_digest_requires_exact_lowercase_hex(expected: str) -> None:
    operator = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(JsonString(value=expected)),
    )

    assert type(_evaluate(BytesValue(value=b"payload"), operator)) is PredicateError
