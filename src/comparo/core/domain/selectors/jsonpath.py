"""Immutable RFC 9535 JSONPath abstract syntax."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonString


class QueryOrigin(DomainStrEnum):
    """The root used by a JSONPath query expression."""

    ROOT = "root"
    CURRENT = "current"


class ComparisonOperator(DomainStrEnum):
    """RFC 9535 comparison operators."""

    EQUAL = "=="
    NOT_EQUAL = "!="
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="


class StandardFunction(DomainStrEnum):
    """The standard RFC 9535 function-extension catalog."""

    LENGTH = "length"
    COUNT = "count"
    MATCH = "match"
    SEARCH = "search"
    VALUE = "value"


@final
class NameSelector(msgspec.Struct, frozen=True, kw_only=True):
    """Select an object member by its exact Unicode name."""

    name: str

    def __post_init__(self) -> None:
        """Require exact Unicode scalar text."""
        if type(self.name) is not str:
            raise TypeError("JSONPath name selector must contain text")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in self.name):
            raise ValueError("JSONPath name selector must contain Unicode scalar values")


@final
class IndexSelector(msgspec.Struct, frozen=True, kw_only=True):
    """Select one array index; negative indexes count from the end."""

    index: int

    def __post_init__(self) -> None:
        """Reject bool masquerading as an integer."""
        if type(self.index) is not int:
            raise TypeError("JSONPath index selector must contain an integer")


@final
class SliceSelector(msgspec.Struct, frozen=True, kw_only=True):
    """Select an RFC 9535 array slice."""

    start: int | None = None
    stop: int | None = None
    step: int | None = None

    def __post_init__(self) -> None:
        """Require exact optional integers; RFC step zero is valid and selects nothing."""
        if any(value is not None and type(value) is not int for value in self.values):
            raise TypeError("JSONPath slice bounds must be integers")

    @property
    def values(self) -> tuple[int | None, int | None, int | None]:
        """Return the bounds in source order for validation and rendering."""
        return self.start, self.stop, self.step


@final
class WildcardSelector(msgspec.Struct, frozen=True, kw_only=True):
    """Select every direct child in deterministic traversal order."""


type ScalarLiteral = JsonNull | JsonBoolean | JsonInteger | JsonDecimal | JsonString


@final
class LiteralExpression(msgspec.Struct, frozen=True, kw_only=True):
    """A scalar JSON literal in a filter expression."""

    value: ScalarLiteral

    def __post_init__(self) -> None:
        """Restrict filters to RFC scalar literals."""
        if type(self.value) not in {
            JsonNull,
            JsonBoolean,
            JsonInteger,
            JsonDecimal,
            JsonString,
        }:
            raise TypeError("JSONPath filter literal must be a scalar JSON value")
        if type(self.value) is JsonString and any(
            0xD800 <= ord(character) <= 0xDFFF for character in self.value.value
        ):
            raise ValueError("JSONPath string literal must contain Unicode scalar values")


@final
class FilterSelector(msgspec.Struct, frozen=True, kw_only=True):
    """Select children for which a logical expression is true."""

    predicate: FilterExpression

    def __post_init__(self) -> None:
        """Require a closed filter expression node."""
        if not is_filter_expression(self.predicate):
            raise TypeError("JSONPath filter predicate has an unsupported type")


type SegmentSelector = (
    NameSelector | IndexSelector | SliceSelector | WildcardSelector | FilterSelector
)


@final
class ChildSegment(msgspec.Struct, frozen=True, kw_only=True):
    """Apply one or more selectors to every node in the input nodelist."""

    selectors: tuple[SegmentSelector, ...]

    def __post_init__(self) -> None:
        """Require a non-empty immutable selector list."""
        _validate_selectors(self.selectors)


@final
class DescendantSegment(msgspec.Struct, frozen=True, kw_only=True):
    """Apply selectors to each descendant-or-self node."""

    selectors: tuple[SegmentSelector, ...]

    def __post_init__(self) -> None:
        """Require a non-empty immutable selector list."""
        _validate_selectors(self.selectors)


type PathSegment = ChildSegment | DescendantSegment


@final
class QueryExpression(msgspec.Struct, frozen=True, kw_only=True):
    """An absolute or filter-relative JSONPath query."""

    origin: QueryOrigin
    segments: tuple[PathSegment, ...]

    def __post_init__(self) -> None:
        """Require a typed origin and immutable path segments."""
        if type(self.origin) is not QueryOrigin:
            raise TypeError("JSONPath query origin must be a QueryOrigin")
        _validate_segments(self.segments)


@final
class FunctionExpression(msgspec.Struct, frozen=True, kw_only=True):
    """A call to one registered standard JSONPath function."""

    function: StandardFunction
    arguments: tuple[FilterExpression, ...]

    def __post_init__(self) -> None:
        """Require a typed function and immutable closed arguments."""
        if type(self.function) is not StandardFunction:
            raise TypeError("JSONPath function must be a StandardFunction")
        if type(self.arguments) is not tuple or any(
            not is_filter_expression(argument) for argument in self.arguments
        ):
            raise TypeError("JSONPath function arguments must be an immutable expression tuple")


@final
class ComparisonExpression(msgspec.Struct, frozen=True, kw_only=True):
    """Compare two singular query, scalar, or value-function expressions."""

    left: FilterExpression
    operator: ComparisonOperator
    right: FilterExpression

    def __post_init__(self) -> None:
        """Require two closed expressions and a typed operator."""
        if not is_filter_expression(self.left) or not is_filter_expression(self.right):
            raise TypeError("JSONPath comparison operands have unsupported types")
        if type(self.operator) is not ComparisonOperator:
            raise TypeError("JSONPath comparison operator must be a ComparisonOperator")


@final
class NotExpression(msgspec.Struct, frozen=True, kw_only=True):
    """Negate one logical filter expression."""

    operand: FilterExpression

    def __post_init__(self) -> None:
        """Require a closed expression operand."""
        if not is_filter_expression(self.operand):
            raise TypeError("JSONPath negation operand has an unsupported type")


@final
class AndExpression(msgspec.Struct, frozen=True, kw_only=True):
    """Conjoin two logical filter expressions."""

    left: FilterExpression
    right: FilterExpression

    def __post_init__(self) -> None:
        """Require two closed operands."""
        _validate_logical_operands(self.left, self.right)


@final
class OrExpression(msgspec.Struct, frozen=True, kw_only=True):
    """Disjoin two logical filter expressions."""

    left: FilterExpression
    right: FilterExpression

    def __post_init__(self) -> None:
        """Require two closed operands."""
        _validate_logical_operands(self.left, self.right)


type FilterExpression = (
    LiteralExpression
    | QueryExpression
    | FunctionExpression
    | ComparisonExpression
    | NotExpression
    | AndExpression
    | OrExpression
)


@final
class JsonPathAst(msgspec.Struct, frozen=True, kw_only=True):
    """A root-addressed immutable RFC 9535 JSONPath syntax tree."""

    segments: tuple[PathSegment, ...]

    def __post_init__(self) -> None:
        """Require immutable typed path segments."""
        _validate_segments(self.segments)


_SELECTOR_TYPES = frozenset(
    {NameSelector, IndexSelector, SliceSelector, WildcardSelector, FilterSelector}
)
_SEGMENT_TYPES = frozenset({ChildSegment, DescendantSegment})
_EXPRESSION_TYPES = frozenset(
    {
        LiteralExpression,
        QueryExpression,
        FunctionExpression,
        ComparisonExpression,
        NotExpression,
        AndExpression,
        OrExpression,
    }
)


def is_filter_expression(value: object) -> TypeGuard[FilterExpression]:
    """Narrow an arbitrary value to a filter expression node."""
    return type(value) in _EXPRESSION_TYPES


def _validate_selectors(selectors: object) -> None:
    if type(selectors) is not tuple:
        raise TypeError("JSONPath segment selectors must be an immutable tuple")
    if not selectors or any(type(selector) not in _SELECTOR_TYPES for selector in selectors):
        raise ValueError("JSONPath segment must contain supported selectors")


def _validate_segments(segments: object) -> None:
    if type(segments) is not tuple:
        raise TypeError("JSONPath segments must be an immutable tuple")
    if any(type(segment) not in _SEGMENT_TYPES for segment in segments):
        raise TypeError("JSONPath contains an unsupported segment")


def _validate_logical_operands(left: object, right: object) -> None:
    if not is_filter_expression(left) or not is_filter_expression(right):
        raise TypeError("JSONPath logical operands have unsupported types")
