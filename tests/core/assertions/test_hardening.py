"""Regression tests for deterministic, bounded, provenance-safe assertions."""

from __future__ import annotations

from decimal import Decimal
from decimal import localcontext
from hashlib import sha256
from uuid import UUID

import msgspec
import pytest

from comparo.core.assertions import PreparedAssertionInputs
from comparo.core.assertions import PreparedCapabilityKind
from comparo.core.assertions import PreparedExpression
from comparo.core.assertions import PreparedOperandReady
from comparo.core.assertions import evaluate_assertion
from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.assertions.inputs import require_known_predicate_bindings
from comparo.core.assertions.predicates import PredicateSatisfied
from comparo.core.assertions.predicates import PredicateUnavailable
from comparo.core.assertions.predicates import PredicateViolated
from comparo.core.assertions.predicates import evaluate_predicate
from comparo.core.assertions.work import charge_predicate_work
from comparo.core.domain.assertions.evidence import ApproximateEvidence
from comparo.core.domain.assertions.evidence import DigestEvidence
from comparo.core.domain.assertions.evidence import SchemaValidationEvidence
from comparo.core.domain.assertions.evidence import SchemaViolationLocation
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import ContainsMode
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import KeySetRelation
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import StringRelation
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from tests.core.assertions.factories import application
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.assertions.test_predicates import _evaluate
from tests.core.assertions.test_predicates import _inputs
from tests.core.assertions.test_predicates import _match
from tests.core.assertions.test_predicates import _predicate
from tests.core.domain.assertions.test_contracts import _literal
from tests.core.domain.assertions.test_contracts import _regex
from tests.core.domain.assertions.test_contracts import _schema
from tests.core.selection.test_body_evaluator_coverage import _selector as _body_selector

_ACCESS = SensitiveValueAccess(purpose="assertion.test.inspect")


def test_approximate_comparison_ignores_ambient_decimal_context() -> None:
    operator = ApproximateOperator(
        expected=_literal(JsonDecimal(value=Decimal("1.2345678"))),
        relative_tolerance=_literal(JsonDecimal(value=Decimal("0.000000072899999"))),
    )

    with localcontext() as context:
        context.prec = 5
        low = _evaluate(JsonDecimal(value=Decimal("1.23456789")), operator)
    with localcontext() as context:
        context.prec = 60
        high = _evaluate(JsonDecimal(value=Decimal("1.23456789")), operator)

    assert type(low) is PredicateViolated
    assert type(high) is PredicateViolated
    assert type(low.evidence) is ApproximateEvidence
    assert low.evidence == high.evidence


def test_sensitive_operand_and_selected_schema_keep_structured_evidence_sealed() -> None:
    origin = TaintOriginId(value="secret/assertion-operand")
    operator = ApproximateOperator(
        expected=_literal(JsonDecimal(value=Decimal("1.1"))),
        absolute_tolerance=_literal(JsonDecimal(value=Decimal("0.1"))),
    )
    prepared = _inputs(operator)
    sensitive_expected = SensitiveValue(
        JsonDecimal(value=Decimal("1.1")),
        origins=(origin,),
    )
    prepared = msgspec.structs.replace(
        prepared,
        expressions=(
            msgspec.structs.replace(
                prepared.expressions[0],
                result=PreparedOperandReady(value=sensitive_expected),
            ),
            prepared.expressions[1],
        ),
    )
    approximate = evaluate_predicate(
        _match(JsonDecimal(value=Decimal("1.05"))),
        operator,
        prepared,
        snapshot(),
    )
    assert type(approximate) is PredicateSatisfied
    assert is_sensitive_value(approximate.evidence)
    assert approximate.evidence.origins == (origin,)
    assert type(approximate.evidence.reveal(access=_ACCESS)) is ApproximateEvidence

    selected = msgspec.structs.replace(
        _match(JsonString(value="secret")),
        value=SensitiveValue(JsonString(value="secret"), origins=(origin,)),
    )
    schema = _schema()
    binding = _predicate(schema, selected, PreparedCapabilityKind.JSON_SCHEMA)
    validated = evaluate_predicate(
        selected,
        schema,
        msgspec.structs.replace(_inputs(schema), predicates=(binding,)),
        snapshot(),
    )
    assert type(validated) is PredicateSatisfied
    assert is_sensitive_value(validated.evidence)
    with pytest.raises(ValueError, match="sensitivity contradicts"):
        msgspec.structs.replace(
            binding,
            evidence=SchemaValidationEvidence(violation_count=Count(value=0)),
        )


def test_digest_uses_exact_prepared_binding_without_snapshot_body_bytes() -> None:
    payload_digest = sha256(b"payload").digest()
    operator = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(BytesValue(value=payload_digest)),
    )
    body_id = BodyId(value=UUID(int=91_001))
    match = SelectionMatch(
        value=body_id,
        location=BodyLocation(
            environment_execution_id=snapshot().observation.execution_id,
            source=SelectorSource.RESPONSE_BODY,
            body_id=body_id,
            body_view=BodyViewKind.HANDLE,
        ),
    )
    prepared = _predicate(
        operator,
        match,
        PreparedCapabilityKind.DIGEST,
        evidence=DigestEvidence(
            algorithm=DigestAlgorithm.SHA256,
            actual=BytesValue(value=payload_digest),
        ),
    )
    prepared_inputs = msgspec.structs.replace(
        _inputs(operator),
        predicates=(prepared,),
    )

    assert snapshot().body_bytes == ()
    assert type(evaluate_predicate(match, operator, prepared_inputs, snapshot())) is (
        PredicateSatisfied
    )
    changed = DigestOperator(
        algorithm=DigestAlgorithm.SHA256,
        expected=_literal(BytesValue(value=b"x" * 32)),
    )
    stale = msgspec.structs.replace(_inputs(changed), predicates=(prepared,))
    assert type(evaluate_predicate(match, changed, stale, snapshot())) is PredicateUnavailable


def test_schema_binding_covers_validator_semantics_selected_value_and_evidence_shape() -> None:
    schema = _schema()
    match = _match(JsonString(value="value"))
    prepared = _predicate(schema, match, PreparedCapabilityKind.JSON_SCHEMA)
    altered = msgspec.structs.replace(
        schema,
        validator=NamedCapabilityTarget(
            kind=CapabilityTargetKind.PROVIDER,
            identifier="community.validator@2",
        ),
    )
    supplied = msgspec.structs.replace(_inputs(altered), predicates=(prepared,))
    assert type(evaluate_predicate(match, altered, supplied, snapshot())) is PredicateUnavailable
    changed_match = msgspec.structs.replace(match, value=JsonString(value="other"))
    assert (
        type(
            evaluate_predicate(
                changed_match,
                schema,
                msgspec.structs.replace(_inputs(schema), predicates=(prepared,)),
                snapshot(),
            )
        )
        is PredicateUnavailable
    )

    location = SchemaViolationLocation(
        instance=JsonPointer(value="/name"),
        schema=JsonPointer(value="/properties/name/type"),
    )
    with pytest.raises(ValueError, match="exactly cover"):
        SchemaValidationEvidence(
            violation_count=Count(value=2),
            retained=(location,),
            omitted=Count(value=0),
        )


def test_possible_path_cache_allows_unused_valid_binding_but_rejects_foreign_location() -> None:
    regex = _regex()
    selector = _body_selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.TEXT,
        kind=ValueKind.STRING,
    )
    compiled = msgspec.structs.replace(
        rule(),
        selector=selector,
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.REGEX,
            version="1",
        ),
        operator=regex,
    )
    match = SelectionMatch(
        value=JsonString(value="200"),
        location=ViewLocation(
            environment_execution_id=snapshot().observation.execution_id,
            source=SelectorSource.RESPONSE_BODY,
            view_id=ViewId(value=UUID(int=91_003)),
            body_view=BodyViewKind.TEXT,
        ),
    )
    prepared = msgspec.structs.replace(
        _predicate(regex, match, PreparedCapabilityKind.REGEX),
        selector_fingerprint=selector.fingerprint,
    )
    supplied = PreparedAssertionInputs(
        rule_fingerprint=compiled.fingerprint,
        environment_execution_id=snapshot().observation.execution_id,
        predicates=(prepared,),
    )
    require_known_predicate_bindings(compiled, supplied)
    cancelled = evaluate_assertion(
        snapshot(),
        application(compiled, snapshot()),
        compiled,
        supplied,
        control(cancelled=True),
    )
    assert type(cancelled.fact.outcome) is AssertionUnavailable
    assert cancelled.fact.outcome.reason is AssertionUnavailableReason.CANCELLED

    foreign_location = ExchangeLocation(
        environment_execution_id=snapshot().observation.execution_id,
        source=SelectorSource.RESPONSE_PROTOCOL,
        exchange_id=ExchangeId(value=UUID(int=91_002)),
        field=StaticFieldPath(segments=("response", "protocol")),
    )
    foreign = msgspec.structs.replace(prepared, location=foreign_location)
    with pytest.raises(ValueError, match="source"):
        require_known_predicate_bindings(
            compiled,
            msgspec.structs.replace(supplied, predicates=(foreign,)),
        )


def test_tiny_work_budgets_stop_large_dynamic_and_static_capability_inputs() -> None:
    meter = AssertionMeter(control(work=10).budgets)
    regex_exhaustion = charge_predicate_work(
        meter,
        JsonString(value="x" * 10_000),
        (),
        msgspec.structs.replace(_regex(), pattern="x" * 10_000),
    )
    assert type(regex_exhaustion) is BudgetExceeded

    key_meter = AssertionMeter(control(work=10).budgets)
    key_exhaustion = charge_predicate_work(
        key_meter,
        JsonObject(members=()),
        (),
        KeySetOperator(
            relation=KeySetRelation.HAS_ONLY,
            keys=tuple(f"key-{index:05d}" for index in range(2_000)),
        ),
    )
    assert type(key_exhaustion) is BudgetExceeded


def test_tiny_work_preflights_actual_and_operand_taint_lineage() -> None:
    origins = tuple(
        TaintOriginId(value=f"secret.hostile.origin-{index:04d}") for index in range(2_000)
    )
    actual_meter = AssertionMeter(control(work=1).budgets)
    actual_exhaustion = charge_predicate_work(
        actual_meter,
        SensitiveValue(JsonString(value="x"), origins=origins),
        (),
        _regex(),
    )
    assert type(actual_exhaustion) is BudgetExceeded
    assert actual_meter.budgets.work.usage.consumed == Count(value=0)

    nested_meter = AssertionMeter(control(work=1).budgets)
    nested_exhaustion = charge_predicate_work(
        nested_meter,
        (SensitiveValue(JsonString(value="x"), origins=origins),),
        (),
        _regex(),
    )
    assert type(nested_exhaustion) is BudgetExceeded
    assert nested_meter.budgets.work.usage.consumed == Count(value=1)

    operand_meter = AssertionMeter(control(work=1).budgets)
    operand_exhaustion = charge_predicate_work(
        operand_meter,
        JsonString(value="x"),
        (JsonString(value="needle"),),
        StringOperator(
            relation=StringRelation.CONTAINS,
            expected=_literal(JsonString(value="needle")),
        ),
        operand_origin_groups=(origins,),
    )
    assert type(operand_exhaustion) is BudgetExceeded
    assert operand_meter.budgets.work.usage.consumed == Count(value=1)

    extra = TaintOriginId(value="secret.hostile.extra")
    assert stable_taint_origin_union((origins[0], extra), (extra, origins[1])) == (
        origins[0],
        extra,
        origins[1],
    )


def test_fact_capacity_is_preflighted_before_predicate_work() -> None:
    evaluated = evaluate_assertion(
        snapshot(),
        application(),
        rule(),
        inputs(),
        control(facts=0),
    )

    assert type(evaluated.fact.outcome) is AssertionUnavailable
    assert evaluated.fact.outcome.reason is AssertionUnavailableReason.BUDGET_EXCEEDED
    assert evaluated.budgets.work.usage.consumed == Count(value=0)


def test_prepared_expression_rejects_static_kind_contradictions_at_boundary() -> None:
    expression = _literal(JsonInteger(value=1))
    with pytest.raises(ValueError, match="static output kind"):
        PreparedExpression(
            expression=expression,
            result=PreparedOperandReady(value=JsonString(value="1")),
        )

    number_expression = LiteralExpr(
        value=JsonDecimal(value=Decimal("1.5")),
        provenance=expression.provenance,
    )
    compatible = PreparedExpression(
        expression=number_expression,
        result=PreparedOperandReady(value=JsonInteger(value=1)),
    )
    assert type(compatible.result) is PreparedOperandReady


def test_contains_contract_rejects_non_text_keys_and_substrings() -> None:
    operator = ContainsOperator(
        mode=ContainsMode.ONE,
        expected=(_literal(JsonInteger(value=1)),),
    )
    from comparo.core.domain.assertions.contracts import require_assertion_operator_contract

    with pytest.raises(ValueError, match="contains operand"):
        require_assertion_operator_contract(
            ValueContract(
                kinds=(ValueKind.OBJECT,),
                cardinality=rule().selector.output.cardinality,
            ),
            operator,
        )
    require_assertion_operator_contract(
        ValueContract(
            kinds=(ValueKind.ARRAY,),
            cardinality=rule().selector.output.cardinality,
        ),
        operator,
    )
