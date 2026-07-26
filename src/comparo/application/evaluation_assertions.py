"""Lower authored deterministic assertion operations to the pure assertion kernel."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaRelation
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import TokenOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import BetweenOperator
from comparo.core.domain.assertions.operators import ContainsMode
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import CountRelation
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.operators import ExactValueType
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import KeySetRelation
from comparo.core.domain.assertions.operators import LengthOperator
from comparo.core.domain.assertions.operators import MembershipOperator
from comparo.core.domain.assertions.operators import MembershipRelation
from comparo.core.domain.assertions.operators import OrderingOperator
from comparo.core.domain.assertions.operators import OrderingRelation
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import StringRelation
from comparo.core.domain.assertions.operators import TypeOperator
from comparo.core.domain.assertions.operators import operator_kind
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString


@final
class AssertionOperationError(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free operation rejection for the outer diagnostic adapter."""

    alias: str | None = None


class InvalidAssertionOperationError(ValueError):
    """Internal control-flow exception for an invalid authored assertion operation."""

    def __init__(self, error: AssertionOperationError) -> None:
        """Retain one safe error and no authored operand values."""
        super().__init__("assertion operation cannot be lowered")
        self.error = error


def compile_assertion_operation(
    expression: ValueExpression | None,
) -> tuple[AssertionOperatorIdentity, AssertionOperator]:
    """Compile one closed public alias and its exact typed argument schema."""
    operation = _operation(expression)
    if operation is None:
        raise InvalidAssertionOperationError(AssertionOperationError())
    alias, fields = operation
    try:
        operator = _compile(alias, fields)
        identity = AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=operator_kind(operator),
            version="1",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidAssertionOperationError(
            AssertionOperationError(alias=_bounded_alias(alias))
        ) from error
    return identity, operator


def _compile(alias: str, fields: dict[str, ValueExpression]) -> AssertionOperator:  # noqa: C901
    if alias in {"equals", "notEquals"}:
        _require_fields(fields, "name", "expected")
        return EqualityOperator(
            relation=(
                EqualityRelation.EQUALS if alias == "equals" else EqualityRelation.NOT_EQUALS
            ),
            expected=fields["expected"],
        )
    if alias in {"in", "notIn"}:
        _require_fields(fields, "name", "choices")
        return MembershipOperator(
            relation=MembershipRelation.IN if alias == "in" else MembershipRelation.NOT_IN,
            choices=_expressions(fields["choices"]),
        )
    if alias == "isType":
        _require_fields(fields, "name", "expected")
        return TypeOperator(expected=ExactValueType(_required_text(fields, "expected")))
    ordering = {
        "less": OrderingRelation.LESS,
        "lessOrEqual": OrderingRelation.LESS_OR_EQUAL,
        "greater": OrderingRelation.GREATER,
        "greaterOrEqual": OrderingRelation.GREATER_OR_EQUAL,
    }
    if alias in ordering:
        _require_fields(fields, "name", "expected")
        return OrderingOperator(relation=ordering[alias], expected=fields["expected"])
    if alias == "between":
        _require_optional_fields(
            fields,
            {"name", "lower", "upper"},
            {"includeLower", "includeUpper"},
        )
        return BetweenOperator(
            lower=fields["lower"],
            upper=fields["upper"],
            include_lower=_boolean(fields.get("includeLower"), default=True),
            include_upper=_boolean(fields.get("includeUpper"), default=True),
        )
    if alias == "approximatelyEquals":
        _require_optional_fields(fields, {"name", "expected"}, {"absolute", "relative"})
        return ApproximateOperator(
            expected=fields["expected"],
            absolute_tolerance=fields.get("absolute"),
            relative_tolerance=fields.get("relative"),
        )
    string_relations = {
        "contains": StringRelation.CONTAINS,
        "notContains": StringRelation.NOT_CONTAINS,
        "startsWith": StringRelation.STARTS_WITH,
        "endsWith": StringRelation.ENDS_WITH,
    }
    if alias in string_relations:
        _require_fields(fields, "name", "expected")
        return StringOperator(relation=string_relations[alias], expected=fields["expected"])
    if alias in {"containsAll", "containsAny"}:
        _require_fields(fields, "name", "expected")
        return ContainsOperator(
            mode=ContainsMode.ALL if alias == "containsAll" else ContainsMode.ANY,
            expected=_expressions(fields["expected"]),
        )
    if alias in {"hasKey", "hasKeys", "hasOnlyKeys"}:
        _require_fields(fields, "name", "keys" if alias != "hasKey" else "key")
        key_field = "key" if alias == "hasKey" else "keys"
        keys = (
            (_required_text(fields, key_field),) if alias == "hasKey" else _texts(fields[key_field])
        )
        key_relation = {
            "hasKey": KeySetRelation.HAS,
            "hasKeys": KeySetRelation.HAS_ALL,
            "hasOnlyKeys": KeySetRelation.HAS_ONLY,
        }[alias]
        return KeySetOperator(relation=key_relation, keys=keys)
    if alias == "length":
        _require_optional_fields(fields, {"name", "expected"}, {"relation"})
        relation_text = _text(fields.get("relation")) or "equals"
        return LengthOperator(
            relation=CountRelation(relation_text),
            expected=Count(value=_required_integer(fields, "expected")),
        )
    if alias == "decodesAs":
        _require_fields(fields, "name", "view")
        return DecodeOperator(view=BodyViewKind(_required_text(fields, "view")))
    if alias in {"mediaTypeIs", "mediaTypeMatches"}:
        _require_optional_fields(fields, {"name", "expected"}, {"relation"})
        media_relation = (
            MediaRelation.SUFFIX_MATCH
            if alias == "mediaTypeMatches"
            else MediaRelation(_text(fields.get("relation")) or "exact")
        )
        return MediaTypeOperator(
            relation=media_relation,
            expected=_media_type(_required_text(fields, "expected")),
        )
    if alias == "containsToken":
        _require_fields(fields, "name", "token")
        return TokenOperator(token=MediaTypeTokenValue(value=_required_text(fields, "token")))
    if alias == "digestEquals":
        _require_fields(fields, "name", "algorithm", "expected")
        return DigestOperator(
            algorithm=DigestAlgorithm(_required_text(fields, "algorithm")),
            expected=fields["expected"],
        )
    if alias == "cardinality":
        _require_optional_fields(fields, {"name"}, {"exact", "minimum", "maximum"})
        return CardinalityOperator(
            exact=_optional_count(fields.get("exact")),
            minimum=_optional_count(fields.get("minimum")),
            maximum=_optional_count(fields.get("maximum")),
        )
    raise ValueError("operation alias requires a prepared capability or is unsupported")


def _operation(
    expression: ValueExpression | None,
) -> tuple[str, dict[str, ValueExpression]] | None:
    shorthand = _text(expression)
    if shorthand is not None and expression is not None:
        return _normalize(shorthand), {"name": expression}
    if type(expression) is not ObjectExpr:
        return None
    fields = {member.name: member.expression for member in expression.members}
    name = _text(fields.get("name"))
    return None if name is None else (_normalize(name), fields)


def _normalize(name: str) -> str:
    prefix = "comparo.core/"
    return name[len(prefix) : -2] if name.startswith(prefix) and name.endswith("@1") else name


def _require_fields(fields: dict[str, ValueExpression], *required: str) -> None:
    if set(fields) != set(required):
        raise ValueError("operation fields do not match its typed schema")


def _require_optional_fields(
    fields: dict[str, ValueExpression],
    required: set[str],
    optional: set[str],
) -> None:
    if not required <= set(fields) or not set(fields) <= required | optional:
        raise ValueError("operation fields do not match its typed schema")


def _expressions(value: ValueExpression) -> tuple[ValueExpression, ...]:
    if type(value) is not ArrayExpr or not value.items:
        raise ValueError("operation requires a non-empty expression array")
    return value.items


def _texts(value: ValueExpression) -> tuple[str, ...]:
    values = tuple(_text(item) for item in _expressions(value))
    if any(item is None for item in values):
        raise ValueError("operation requires a text array")
    return tuple(item for item in values if item is not None)


def _text(value: ValueExpression | None) -> str | None:
    return (
        value.value.value
        if type(value) is LiteralExpr and type(value.value) is JsonString
        else None
    )


def _required_text(fields: dict[str, ValueExpression], name: str) -> str:
    value = _text(fields[name])
    if value is None:
        raise ValueError("operation requires static text")
    return value


def _integer(value: ValueExpression | None) -> int | None:
    return (
        value.value.value
        if type(value) is LiteralExpr and type(value.value) is JsonInteger
        else None
    )


def _required_integer(fields: dict[str, ValueExpression], name: str) -> int:
    value = _integer(fields[name])
    if value is None:
        raise ValueError("operation requires a static integer")
    return value


def _boolean(value: ValueExpression | None, *, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is not LiteralExpr or type(value.value) is not JsonBoolean:
        raise ValueError("operation requires a static boolean")
    return value.value.value


def _optional_count(value: ValueExpression | None) -> Count | None:
    integer = _integer(value)
    if value is not None and integer is None:
        raise ValueError("operation count must be a static integer")
    return None if integer is None else Count(value=integer)


def _media_type(value: str) -> MediaType:
    if ";" in value or value.count("/") != 1:
        raise ValueError("media assertion currently requires a parameter-free media type")
    type_name, subtype = value.lower().split("/", 1)
    return MediaType(type=type_name, subtype=subtype)


def _bounded_alias(value: str) -> str | None:
    return value if len(value) <= 64 else None
