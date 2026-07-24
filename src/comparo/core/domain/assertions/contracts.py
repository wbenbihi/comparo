"""Static selector/operator compatibility for compiled assertion predicates."""

from __future__ import annotations

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.extended_operators import TokenOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.operators import BetweenOperator
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import LengthOperator
from comparo.core.domain.assertions.operators import MembershipOperator
from comparo.core.domain.assertions.operators import OrderingOperator
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import TypeOperator
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.contracts import NUMERIC_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ORDERED_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.contracts import contract_accepts
from comparo.core.domain.selectors.contracts import expression_accepts
from comparo.core.domain.selectors.contracts import expression_kinds

_TEXT = frozenset({ValueKind.STRING})
_CONTAINERS = frozenset({ValueKind.STRING, ValueKind.ARRAY, ValueKind.OBJECT})
_LENGTH_VALUES = frozenset(
    {
        ValueKind.STRING,
        ValueKind.BYTES,
        ValueKind.ARRAY,
        ValueKind.OBJECT,
        ValueKind.HTTP_FIELD_ITEMS,
    }
)
_DIGEST_VALUES = frozenset(
    {
        ValueKind.BYTES,
        ValueKind.STRING,
        ValueKind.BODY_HANDLE,
        ValueKind.VIEW_HANDLE,
    }
)


def require_assertion_operator_contract(
    selected: ValueContract,
    operator: AssertionOperator,
) -> None:
    """Reject a compiled operator that cannot consume its known selector output."""
    if type(selected) is not ValueContract:
        raise TypeError("assertion selector output must be a ValueContract")
    if _require_relational_contract(selected, operator):
        return
    if _require_collection_contract(selected, operator):
        return
    if _require_extended_contract(selected, operator):
        return
    raise TypeError("unsupported assertion operator contract")


def _require_relational_contract(
    selected: ValueContract,
    operator: AssertionOperator,
) -> bool:
    if type(operator) in {TypeOperator, CardinalityOperator}:
        return True
    if type(operator) is EqualityOperator:
        _require_operand_overlap(selected, operator.expected, "equality")
        return True
    if type(operator) is MembershipOperator:
        for choice in operator.choices:
            _require_operand_overlap(selected, choice, "membership")
        return True
    if type(operator) is OrderingOperator:
        _require_selected(selected, ORDERED_VALUE_KINDS, "ordering")
        _require_operand(operator.expected, ORDERED_VALUE_KINDS, "ordering")
        _require_operand_overlap(selected, operator.expected, "ordering")
        return True
    if type(operator) is BetweenOperator:
        _require_selected(selected, ORDERED_VALUE_KINDS, "between")
        _require_operand(operator.lower, ORDERED_VALUE_KINDS, "between")
        _require_operand(operator.upper, ORDERED_VALUE_KINDS, "between")
        _require_operand_overlap(selected, operator.lower, "between")
        _require_operand_overlap(selected, operator.upper, "between")
        return True
    if type(operator) is ApproximateOperator:
        _require_selected(selected, NUMERIC_VALUE_KINDS, "approximate")
        _require_operand(operator.expected, NUMERIC_VALUE_KINDS, "approximate")
        for tolerance in (operator.absolute_tolerance, operator.relative_tolerance):
            if tolerance is not None:
                _require_operand(tolerance, NUMERIC_VALUE_KINDS, "approximate tolerance")
        return True
    return False


def _require_collection_contract(
    selected: ValueContract,
    operator: AssertionOperator,
) -> bool:
    if type(operator) in {StringOperator, RegexOperator}:
        _require_selected(selected, _TEXT, "text")
        if type(operator) is StringOperator:
            _require_operand(operator.expected, _TEXT, "text")
        return True
    if type(operator) is ContainsOperator:
        _require_selected(selected, _CONTAINERS, "contains")
        return True
    if type(operator) is KeySetOperator:
        _require_selected(selected, frozenset({ValueKind.OBJECT}), "key-set")
        return True
    if type(operator) is LengthOperator:
        _require_selected(selected, _LENGTH_VALUES, "length")
        return True
    return False


def _require_extended_contract(
    selected: ValueContract,
    operator: AssertionOperator,
) -> bool:
    if type(operator) is SchemaOperator:
        _require_selected(selected, JSON_VALUE_KINDS, "schema")
        return True
    if type(operator) is DecodeOperator:
        _require_selected(selected, _DIGEST_VALUES, "decode")
        return True
    if type(operator) is MediaTypeOperator:
        _require_selected(selected, frozenset({ValueKind.MEDIA_TYPE}), "media-type")
        return True
    if type(operator) is TokenOperator:
        _require_selected(selected, frozenset({ValueKind.HTTP_FIELD_ITEMS}), "HTTP field item")
        return True
    if type(operator) is DigestOperator:
        _require_selected(selected, _DIGEST_VALUES, "digest")
        _require_operand(
            operator.expected,
            frozenset({ValueKind.STRING, ValueKind.BYTES}),
            "digest",
        )
        return True
    return False


def _require_selected(
    selected: ValueContract,
    accepted: frozenset[ValueKind],
    operation: str,
) -> None:
    if not contract_accepts(selected, accepted):
        raise ValueError(f"{operation} operator cannot consume selector output types")


def _require_operand(
    expression: ValueExpression,
    accepted: frozenset[ValueKind],
    operation: str,
) -> None:
    if not expression_accepts(expression, accepted):
        raise ValueError(f"{operation} operand has an incompatible static type")


def _require_operand_overlap(
    selected: ValueContract,
    expression: ValueExpression,
    operation: str,
) -> None:
    kinds = expression_kinds(expression)
    if ValueKind.UNKNOWN in selected.kinds or ValueKind.UNKNOWN in kinds:
        return
    accepted = frozenset(selected.kinds)
    if not all(
        kind in accepted
        or (kind is ValueKind.INTEGER and ValueKind.NUMBER in accepted)
        or (kind is ValueKind.NUMBER and ValueKind.INTEGER in accepted)
        for kind in kinds
    ):
        raise ValueError(f"{operation} operand contradicts selector output types")
