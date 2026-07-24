"""Canonical value-expression tree shared by all runtime-resolvable fields."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import is_json_value

_SYMBOL_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class ValueNamespace(StrEnum):
    """Disjoint symbol tables available to explicit value lookups."""

    VARIABLES = "variables"
    SECRETS = "secrets"
    ENV = "env"
    PARAMETERS = "parameters"
    MATRIX = "matrix"
    STEPS = "steps"


_NAMESPACE_SCOPES = {
    ValueNamespace.VARIABLES: frozenset(ValueScopeKind),
    ValueNamespace.SECRETS: frozenset(ValueScopeKind),
    ValueNamespace.ENV: frozenset({ValueScopeKind.ENVIRONMENT}),
    ValueNamespace.PARAMETERS: frozenset({ValueScopeKind.CELL, ValueScopeKind.STEP}),
    ValueNamespace.MATRIX: frozenset({ValueScopeKind.CELL}),
    ValueNamespace.STEPS: frozenset({ValueScopeKind.STEP}),
}


class ConversionType(StrEnum):
    """The strict, explicit value-conversion vocabulary."""

    STRING = "string"
    BYTES = "bytes"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"
    YAML = "yaml"
    DURATION = "duration"
    SIZE = "size"


class FileTrim(StrEnum):
    """Explicit text trimming behavior for file conversion."""

    NONE = "none"
    START = "start"
    END = "end"
    BOTH = "both"


@final
class SymbolPath(msgspec.Struct, frozen=True, kw_only=True):
    """A non-empty static namespace path such as ``login.token``."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require an immutable path of deterministic symbol segments."""
        if type(self.segments) is not tuple:
            raise TypeError("symbol path segments must be an immutable tuple")
        if not self.segments:
            raise ValueError("symbol path must contain at least one segment")
        if any(
            type(segment) is not str or _SYMBOL_PATTERN.fullmatch(segment) is None
            for segment in self.segments
        ):
            raise ValueError("symbol path contains an invalid segment")


@final
class ExpressionDependency(msgspec.Struct, frozen=True, kw_only=True):
    """One symbol dependency and the narrowest scope at which it is available."""

    namespace: ValueNamespace
    symbol: SymbolPath
    available_at: ValueScopeKind

    def __post_init__(self) -> None:
        """Require typed dependency coordinates."""
        if type(self.namespace) is not ValueNamespace:
            raise TypeError("dependency namespace must be a ValueNamespace")
        if type(self.symbol) is not SymbolPath:
            raise TypeError("dependency symbol must be a SymbolPath")
        if type(self.available_at) is not ValueScopeKind:
            raise TypeError("dependency scope must be a ValueScopeKind")
        if self.available_at not in _NAMESPACE_SCOPES[self.namespace]:
            raise ValueError("dependency namespace is unavailable at the declared scope")


@final
class LiteralExpr(msgspec.Struct, frozen=True, kw_only=True):
    """Inert exact data produced by a literal or ``$literal`` directive."""

    value: ExactValue
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require exact data and value-free provenance."""
        if not (is_json_value(self.value) or type(self.value) is BytesValue):
            raise TypeError("literal expression requires an exact value")
        _require_provenance(self.provenance)


@final
class LookupExpr(msgspec.Struct, frozen=True, kw_only=True):
    """A lookup in exactly one declared namespace."""

    dependency: ExpressionDependency
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require a typed dependency and provenance."""
        if type(self.dependency) is not ExpressionDependency:
            raise TypeError("lookup expression requires an ExpressionDependency")
        _require_provenance(self.provenance)


@final
class FileExpr(msgspec.Struct, frozen=True, kw_only=True):
    """A policy-controlled file read whose path is itself resolvable."""

    path: ValueExpression
    conversion: ConversionType | None
    trim: FileTrim
    maximum_bytes: ByteCount
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require an expression path and explicit finite read policy."""
        if not is_value_expression(self.path):
            raise TypeError("file path must be a value expression")
        if self.conversion is not None and type(self.conversion) is not ConversionType:
            raise TypeError("file conversion must be a ConversionType")
        if type(self.trim) is not FileTrim:
            raise TypeError("file trim behavior must be a FileTrim")
        if self.conversion is None and self.trim is not FileTrim.NONE:
            raise ValueError("raw byte file reads cannot apply text trimming")
        if type(self.maximum_bytes) is not ByteCount:
            raise TypeError("file maximum bytes must be a ByteCount")
        _require_provenance(self.provenance)


@final
class FallbackExpr(msgspec.Struct, frozen=True, kw_only=True):
    """A non-empty ordered fallback that advances only on Unavailable."""

    sources: tuple[ValueExpression, ...]
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require immutable, typed, non-empty source expressions."""
        if type(self.sources) is not tuple:
            raise TypeError("fallback sources must be an immutable tuple")
        if not self.sources:
            raise ValueError("fallback expression requires at least one source")
        if any(not is_value_expression(source) for source in self.sources):
            raise TypeError("fallback sources must be value expressions")
        _require_provenance(self.provenance)


@final
class OptionalExpr(msgspec.Struct, frozen=True, kw_only=True):
    """Convert an unavailable source into intentional Missing."""

    source: ValueExpression
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require one typed source expression."""
        if not is_value_expression(self.source):
            raise TypeError("optional source must be a value expression")
        _require_provenance(self.provenance)


@final
class ConvertExpr(msgspec.Struct, frozen=True, kw_only=True):
    """Apply one explicit strict conversion to another expression."""

    source: ValueExpression
    target: ConversionType
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require a typed source and target conversion."""
        if not is_value_expression(self.source):
            raise TypeError("conversion source must be a value expression")
        if type(self.target) is not ConversionType:
            raise TypeError("conversion target must be a ConversionType")
        _require_provenance(self.provenance)


@final
class LiteralTextSegment(msgspec.Struct, frozen=True, kw_only=True):
    """One inert text segment after interpolation escaping is compiled."""

    value: str

    def __post_init__(self) -> None:
        """Require exact text, including an intentionally empty segment."""
        if type(self.value) is not str:
            raise TypeError("literal interpolation segment must be text")


@final
class ExpressionSegment(msgspec.Struct, frozen=True, kw_only=True):
    """One embedded value expression within interpolation."""

    expression: ValueExpression

    def __post_init__(self) -> None:
        """Require a typed value expression."""
        if not is_value_expression(self.expression):
            raise TypeError("interpolation expression segment must contain a value expression")


type InterpolationSegment = LiteralTextSegment | ExpressionSegment


class InterpolationMode(StrEnum):
    """Whether interpolation retains a native value or constructs text."""

    WHOLE_VALUE = "whole-value"
    EMBEDDED_TEXT = "embedded-text"


@final
class InterpolationExpr(msgspec.Struct, frozen=True, kw_only=True):
    """Compiled interpolation with explicit whole-value versus text semantics."""

    mode: InterpolationMode
    segments: tuple[InterpolationSegment, ...]
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Reject empty, untyped, or semantically contradictory interpolation."""
        if type(self.mode) is not InterpolationMode:
            raise TypeError("interpolation mode must be an InterpolationMode")
        if type(self.segments) is not tuple:
            raise TypeError("interpolation segments must be an immutable tuple")
        if not self.segments:
            raise ValueError("interpolation requires at least one segment")
        if any(
            type(segment) not in {LiteralTextSegment, ExpressionSegment}
            for segment in self.segments
        ):
            raise TypeError("interpolation contains an unsupported segment")
        expression_count = sum(type(segment) is ExpressionSegment for segment in self.segments)
        if expression_count == 0:
            raise ValueError("interpolation requires at least one expression segment")
        if self.mode is InterpolationMode.WHOLE_VALUE and (
            len(self.segments) != 1 or type(self.segments[0]) is not ExpressionSegment
        ):
            raise ValueError("whole-value interpolation requires exactly one expression segment")
        _require_provenance(self.provenance)


type ValueExpression = (
    LiteralExpr
    | LookupExpr
    | FileExpr
    | FallbackExpr
    | OptionalExpr
    | ConvertExpr
    | InterpolationExpr
)


def is_value_expression(value: object) -> TypeGuard[ValueExpression]:
    """Narrow a value to the closed canonical expression union."""
    return type(value) in {
        LiteralExpr,
        LookupExpr,
        FileExpr,
        FallbackExpr,
        OptionalExpr,
        ConvertExpr,
        InterpolationExpr,
    }


def expression_dependencies(expression: ValueExpression) -> tuple[ExpressionDependency, ...]:
    """Return ordered unique dependencies without storing competing derived metadata."""
    discovered = _all_dependencies(expression)
    return tuple(
        dependency
        for index, dependency in enumerate(discovered)
        if dependency not in discovered[:index]
    )


def _all_dependencies(expression: ValueExpression) -> tuple[ExpressionDependency, ...]:
    if type(expression) is LiteralExpr:
        return ()
    if type(expression) is LookupExpr:
        return (expression.dependency,)
    if type(expression) is FileExpr:
        return _all_dependencies(expression.path)
    if type(expression) is FallbackExpr:
        return tuple(
            dependency for source in expression.sources for dependency in _all_dependencies(source)
        )
    if type(expression) is OptionalExpr:
        return _all_dependencies(expression.source)
    if type(expression) is ConvertExpr:
        return _all_dependencies(expression.source)
    if type(expression) is InterpolationExpr:
        return tuple(
            dependency
            for segment in expression.segments
            if type(segment) is ExpressionSegment
            for dependency in _all_dependencies(segment.expression)
        )
    raise TypeError("unsupported value expression")


def _require_provenance(provenance: object) -> None:
    if type(provenance) is not ValueProvenance:
        raise TypeError("expression provenance must be ValueProvenance")
