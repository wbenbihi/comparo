"""Static value and cardinality contracts shared by evaluation stages."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeGuard
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.expressions import ConversionType
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import InterpolationMode
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString


class ValueKind(StrEnum):
    """Exact runtime value categories visible to static evaluation planning."""

    UNKNOWN = "unknown"
    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    ARRAY = "array"
    OBJECT = "object"
    BYTES = "bytes"
    DURATION = "duration"
    SIZE = "size"
    INSTANT = "instant"
    COUNT = "count"
    RATIO = "ratio"
    BODY_HANDLE = "body-handle"
    VIEW_HANDLE = "view-handle"
    MEDIA_TYPE = "media-type"
    HTTP_FIELD_ITEMS = "http-field-items"


JSON_VALUE_KINDS = frozenset(
    {
        ValueKind.NULL,
        ValueKind.BOOLEAN,
        ValueKind.INTEGER,
        ValueKind.NUMBER,
        ValueKind.STRING,
        ValueKind.ARRAY,
        ValueKind.OBJECT,
    }
)
NUMERIC_VALUE_KINDS = frozenset({ValueKind.INTEGER, ValueKind.NUMBER})
ORDERED_VALUE_KINDS = frozenset(
    {
        ValueKind.INTEGER,
        ValueKind.NUMBER,
        ValueKind.DURATION,
        ValueKind.SIZE,
        ValueKind.INSTANT,
        ValueKind.COUNT,
        ValueKind.RATIO,
    }
)


@final
class ValueContract(msgspec.Struct, frozen=True, kw_only=True):
    """The possible value kinds and result cardinality proven at compilation."""

    kinds: tuple[ValueKind, ...]
    cardinality: SelectorCardinality

    def __post_init__(self) -> None:
        """Require a non-empty canonical set and one typed cardinality."""
        if type(self.kinds) is not tuple or not self.kinds:
            raise ValueError("value contract kinds must be a non-empty immutable tuple")
        if any(type(kind) is not ValueKind for kind in self.kinds):
            raise TypeError("value contract kinds must contain ValueKind values")
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("value contract kinds must be unique")
        if ValueKind.UNKNOWN in self.kinds and len(self.kinds) != 1:
            raise ValueError("unknown cannot be combined with concrete value kinds")
        if type(self.cardinality) is not SelectorCardinality:
            raise TypeError("value contract cardinality must be a SelectorCardinality")


def scalar_contract(*kinds: ValueKind) -> ValueContract:
    """Build a scalar static value contract."""
    return ValueContract(kinds=kinds, cardinality=SelectorCardinality.EXACTLY_ONE)


def with_cardinality(
    contract: ValueContract,
    cardinality: SelectorCardinality,
) -> ValueContract:
    """Retain possible kinds while deriving a new static cardinality."""
    if type(contract) is not ValueContract:
        raise TypeError("value contract must be a ValueContract")
    return ValueContract(kinds=contract.kinds, cardinality=cardinality)


def contract_accepts(actual: ValueContract, accepted: frozenset[ValueKind]) -> bool:
    """Return whether known actual kinds fit an accepted static type family."""
    if type(actual) is not ValueContract:
        raise TypeError("actual value contract must be a ValueContract")
    if ValueKind.UNKNOWN in actual.kinds:
        return True
    return all(_kind_is_accepted(kind, accepted) for kind in actual.kinds)


def contracts_overlap(left: ValueContract, right: ValueContract) -> bool:
    """Return whether two contracts can contain strictly comparable values."""
    if type(left) is not ValueContract or type(right) is not ValueContract:
        raise TypeError("comparison requires ValueContract values")
    if ValueKind.UNKNOWN in left.kinds or ValueKind.UNKNOWN in right.kinds:
        return True
    return any(
        _kind_is_accepted(left_kind, frozenset({right_kind}))
        or _kind_is_accepted(right_kind, frozenset({left_kind}))
        for left_kind in left.kinds
        for right_kind in right.kinds
    )


def contract_can_feed(actual: ValueContract, expected: ValueContract) -> bool:
    """Return whether an output can feed a stage's declared scalar input contract."""
    if type(actual) is not ValueContract or type(expected) is not ValueContract:
        raise TypeError("pipeline compatibility requires ValueContract values")
    if ValueKind.UNKNOWN in actual.kinds or ValueKind.UNKNOWN in expected.kinds:
        return True
    return all(_kind_is_accepted(kind, frozenset(expected.kinds)) for kind in actual.kinds)


def expression_kinds(expression: ValueExpression) -> frozenset[ValueKind]:
    """Infer every kind known from the closed compiled expression tree."""
    if type(expression) in {LiteralExpr, LookupExpr, FileExpr, ConvertExpr}:
        return _atomic_expression_kinds(cast("AtomicExpression", expression))
    return _composite_expression_kinds(cast("CompositeExpression", expression))


type AtomicExpression = LiteralExpr | LookupExpr | FileExpr | ConvertExpr
type CompositeExpression = FallbackExpr | OptionalExpr | InterpolationExpr


def _atomic_expression_kinds(expression: AtomicExpression) -> frozenset[ValueKind]:
    if type(expression) is LiteralExpr:
        return frozenset({_literal_kind(expression)})
    if type(expression) is LookupExpr:
        return frozenset({ValueKind.UNKNOWN})
    if type(expression) is FileExpr:
        if expression.conversion is None:
            return frozenset({ValueKind.BYTES})
        return _conversion_kinds(expression.conversion)
    return _conversion_kinds(expression.target)


def _composite_expression_kinds(expression: CompositeExpression) -> frozenset[ValueKind]:
    if type(expression) is FallbackExpr:
        kinds = frozenset().union(*(expression_kinds(source) for source in expression.sources))
        if ValueKind.UNKNOWN in kinds:
            return frozenset({ValueKind.UNKNOWN})
        return kinds
    if type(expression) is OptionalExpr:
        return expression_kinds(expression.source)
    interpolation = expression
    if interpolation.mode is InterpolationMode.EMBEDDED_TEXT:
        return frozenset({ValueKind.STRING})
    segment = interpolation.segments[0]
    if type(segment) is not ExpressionSegment:  # pragma: no cover - guarded by expression
        raise TypeError("whole-value interpolation requires an expression segment")
    return expression_kinds(segment.expression)


def expression_accepts(
    expression: ValueExpression,
    accepted: frozenset[ValueKind],
) -> bool:
    """Return whether a statically known operand is compatible with an operation."""
    kinds = expression_kinds(expression)
    if ValueKind.UNKNOWN in kinds:
        return True
    return all(_kind_is_accepted(kind, accepted) for kind in kinds)


def is_statically_negative_number(expression: ValueExpression) -> bool:
    """Recognize only direct, exact negative numeric literals."""
    if type(expression) is not LiteralExpr:
        return False
    value = expression.value
    return (type(value) is JsonInteger and value.value < 0) or (
        type(value) is JsonDecimal and value.value < 0
    )


def is_value_contract(value: object) -> TypeGuard[ValueContract]:
    """Narrow an object to the sealed contract type."""
    return type(value) is ValueContract


def _kind_is_accepted(kind: ValueKind, accepted: frozenset[ValueKind]) -> bool:
    if kind in accepted:
        return True
    return kind is ValueKind.INTEGER and ValueKind.NUMBER in accepted


def _literal_kind(expression: LiteralExpr) -> ValueKind:
    value = expression.value
    mapping: dict[type[object], ValueKind] = {
        JsonNull: ValueKind.NULL,
        JsonBoolean: ValueKind.BOOLEAN,
        JsonInteger: ValueKind.INTEGER,
        JsonDecimal: ValueKind.NUMBER,
        JsonString: ValueKind.STRING,
        JsonArray: ValueKind.ARRAY,
        JsonObject: ValueKind.OBJECT,
        BytesValue: ValueKind.BYTES,
    }
    return mapping[type(value)]


def _conversion_kinds(conversion: ConversionType) -> frozenset[ValueKind]:
    mapping = {
        ConversionType.STRING: frozenset({ValueKind.STRING}),
        ConversionType.BYTES: frozenset({ValueKind.BYTES}),
        ConversionType.INTEGER: frozenset({ValueKind.INTEGER}),
        ConversionType.NUMBER: frozenset({ValueKind.NUMBER}),
        ConversionType.BOOLEAN: frozenset({ValueKind.BOOLEAN}),
        ConversionType.JSON: JSON_VALUE_KINDS,
        ConversionType.YAML: JSON_VALUE_KINDS,
        ConversionType.DURATION: frozenset({ValueKind.DURATION}),
        ConversionType.SIZE: frozenset({ValueKind.SIZE}),
    }
    return mapping[conversion]
