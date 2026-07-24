"""Deterministic value-proportional work accounting for assertion predicates."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.domain.assertions.evidence import SchemaValidationEvidence
from comparo.core.domain.assertions.evidence import SchemaViolationLocation
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import MembershipOperator
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString

_ACCESS = SensitiveValueAccess(purpose="assertion.evaluate.account")


def charge_predicate_work(
    meter: AssertionMeter,
    actual: object,
    operands: tuple[object, ...],
    operator: AssertionOperator,
    *,
    operand_origin_groups: tuple[tuple[TaintOriginId, ...], ...] = (),
) -> BudgetExceeded | None:
    """Prepay an upper-bound unit count before value-proportional predicate work."""
    actual_cost, exhaustion = _charge_value(meter, actual)
    if exhaustion is not None:
        return exhaustion
    if (exhaustion := _charge_origin_groups(meter, operand_origin_groups)) is not None:
        return exhaustion
    operand_cost = 0
    for operand in operands:
        cost, exhaustion = _charge_value(meter, operand)
        if exhaustion is not None:
            return exhaustion
        operand_cost += cost
    multiplier = 1
    if type(operator) in {MembershipOperator, ContainsOperator}:
        multiplier = max(1, len(operands))
    exhaustion = _charge_repeated_work(meter, actual_cost, multiplier)
    if exhaustion is not None:
        return exhaustion
    exhaustion = _charge_string_work(meter, actual, operands, actual_cost + operand_cost, operator)
    if exhaustion is not None:
        return exhaustion
    return _charge_operator_work(meter, operator)


def _charge_repeated_work(
    meter: AssertionMeter,
    actual_cost: int,
    multiplier: int,
) -> BudgetExceeded | None:
    if multiplier <= 1:
        return None
    return meter.charge_work(actual_cost * (multiplier - 1))


def _charge_string_work(
    meter: AssertionMeter,
    actual: object,
    operands: tuple[object, ...],
    prepaid: int,
    operator: AssertionOperator,
) -> BudgetExceeded | None:
    if type(operator) is not StringOperator or not operands:
        return None
    left = _text_length(actual)
    right = _text_length(operands[0])
    if left is None or right is None:
        return None
    # Keep the public budget independent of one Python substring implementation.
    required = max(1, left * max(1, right))
    return None if required <= prepaid else meter.charge_work(required - prepaid)


def charge_evidence_measurement_work(
    meter: AssertionMeter,
    value: object,
    operator_evidence: object | None,
) -> BudgetExceeded | None:
    """Bound the payload traversal needed to size a candidate evidence fact."""
    _cost, exhaustion = _charge_value(meter, value)
    if exhaustion is not None:
        return exhaustion
    if operator_evidence is None:
        return meter.charge_work()
    _cost, exhaustion = _charge_value(meter, operator_evidence)
    return exhaustion


def _charge_value(
    meter: AssertionMeter,
    root: object,
) -> tuple[int, BudgetExceeded | None]:
    if is_sensitive_value(root):
        if (exhaustion := _charge_origin_groups(meter, (root.origins,))) is not None:
            return 0, exhaustion
        root = root.reveal(access=_ACCESS)
    total = 0
    stack: list[Iterator[tuple[object, int]]] = [iter(((root, 0),))]
    while stack:
        try:
            value, named_cost = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if named_cost and (exhaustion := meter.charge_work(named_cost)) is not None:
            return total, exhaustion
        total += named_cost
        if is_sensitive_value(value):
            if (exhaustion := _charge_origin_groups(meter, (value.origins,))) is not None:
                return total, exhaustion
            value = value.reveal(access=_ACCESS)
        unit = _local_cost(value)
        if (exhaustion := meter.charge_work(unit)) is not None:
            return total, exhaustion
        total += unit
        children = _children(value)
        if children is not None:
            stack.append(children)
    return total, None


def _charge_origin_groups(
    meter: AssertionMeter,
    groups: tuple[tuple[TaintOriginId, ...], ...],
) -> BudgetExceeded | None:
    for group in groups:
        for origin in group:
            if (exhaustion := meter.charge_work(max(1, len(origin.value)))) is not None:
                return exhaustion
    return None


def _children(value: object) -> Iterator[tuple[object, int]] | None:
    if type(value) is JsonArray:
        return ((item, 0) for item in value.items)
    if type(value) is JsonObject:
        return ((member.value, max(1, len(member.name))) for member in value.members)
    if type(value) is HttpFieldItems:
        return ((item.value, 0) for item in value.items)
    if type(value) is MediaType:
        return _media_type_children(value)
    if type(value) is tuple:
        return ((item, 0) for item in value)
    if type(value) is SchemaValidationEvidence:
        return ((item, 0) for item in value.retained)
    if type(value) is SchemaViolationLocation:
        return iter(((value.instance.value, 0), (value.schema.value, 0)))
    return None


def _local_cost(value: object) -> int:
    scalar = _scalar_cost(value)
    if scalar is not None:
        return scalar
    structured = _structured_cost(value)
    return 1 if structured is None else structured


def _scalar_cost(value: object) -> int | None:
    if type(value) in {JsonNull, JsonBoolean, BareQueryFieldValue}:
        return 1
    if type(value) is JsonInteger:
        return _integer_cost(value.value)
    if type(value) is JsonDecimal:
        return _decimal_cost(value.value)
    if type(value) is JsonString or type(value) is HttpFieldTextValue:
        return max(1, len(value.value))
    if type(value) is BytesValue:
        return max(1, len(value.value))
    if type(value) in {JsonArray, JsonObject}:
        return 1
    return None


def _structured_cost(value: object) -> int | None:
    if type(value) in {HttpFieldItems, MediaType}:
        return 1
    if type(value) is Count or type(value) is ByteCount:
        return _integer_cost(value.value)
    if type(value) is Duration:
        return _integer_cost(value.nanoseconds)
    if type(value) is UtcInstant:
        return _integer_cost(value.unix_nanoseconds)
    if type(value) is Ratio:
        return _integer_cost(value.numerator) + _integer_cost(value.denominator.value)
    if type(value) is str:
        return max(1, len(value))
    if type(value) is tuple:
        return 1
    if type(value) is SchemaValidationEvidence:
        return _integer_cost(value.violation_count.value) + _integer_cost(value.omitted.value)
    return None


def _media_type_children(value: MediaType) -> Iterator[tuple[object, int]]:
    yield value.type, 0
    yield value.subtype, 0
    for item in value.parameters:
        yield item.name, 0
        yield item.value.value, 0


def _charge_operator_work(
    meter: AssertionMeter,
    operator: AssertionOperator,
) -> BudgetExceeded | None:
    if type(operator) is KeySetOperator:
        return _charge_key_set_work(meter, operator)
    if type(operator) is MediaTypeOperator:
        return _charge_media_type_work(meter, operator)
    if type(operator) is RegexOperator:
        return meter.charge_work(max(1, len(operator.pattern)))
    if type(operator) is SchemaOperator:
        schema_source = (
            operator.schema.remote_uri.identifier
            if operator.schema.remote_uri is not None
            else operator.schema.fingerprint.digest.hex_value
        )
        return meter.charge_work(
            max(
                1,
                len(schema_source)
                + len(operator.default_dialect.identifier)
                + len(operator.validator.identifier),
            )
        )
    if type(operator) is DigestOperator:
        return meter.charge_work(len(operator.algorithm.value))
    return meter.charge_work()


def _charge_key_set_work(
    meter: AssertionMeter,
    operator: KeySetOperator,
) -> BudgetExceeded | None:
    if not operator.keys:
        return meter.charge_work()
    for key in operator.keys:
        if (exhaustion := meter.charge_work(max(1, len(key)))) is not None:
            return exhaustion
    return None


def _charge_media_type_work(
    meter: AssertionMeter,
    operator: MediaTypeOperator,
) -> BudgetExceeded | None:
    expected = operator.expected
    exhaustion = meter.charge_work(max(1, len(expected.type) + len(expected.subtype)))
    if exhaustion is not None:
        return exhaustion
    for item in expected.parameters:
        exhaustion = meter.charge_work(max(1, len(item.name) + len(item.value.value)))
        if exhaustion is not None:
            return exhaustion
    return None


def _integer_cost(value: int) -> int:
    return max(1, (abs(value).bit_length() + 2) // 3)


def _decimal_cost(value: Decimal) -> int:
    return max(1, len(value.as_tuple().digits))


def _text_length(value: object) -> int | None:
    if type(value) is JsonString:
        return len(value.value)
    if type(value) is HttpFieldTextValue:
        return len(value.value)
    return None
