"""Canonical value-expression tree shared by all runtime-resolvable fields."""

from __future__ import annotations

from typing import TypeGuard
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.expression_types import ConversionType as ConversionType
from comparo.core.domain.expression_types import FileTrim as FileTrim
from comparo.core.domain.expression_types import InterpolationMode as InterpolationMode
from comparo.core.domain.expression_types import conversion_output_kinds
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.value_references import _NAMESPACE_SCOPES as _NAMESPACE_SCOPES
from comparo.core.domain.value_references import SymbolPath as SymbolPath
from comparo.core.domain.value_references import ValueNamespace as ValueNamespace
from comparo.core.domain.value_references import namespace_supports_declared_scope
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import is_exact_value_type
from comparo.core.domain.values import is_json_value

_JSON_OUTPUT_KINDS = frozenset(
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
_TEXT_OUTPUT_KINDS = frozenset({ValueKind.STRING})
_TEXT_RENDERABLE_OUTPUT_KINDS = _JSON_OUTPUT_KINDS - frozenset(
    {ValueKind.ARRAY, ValueKind.OBJECT}
) | frozenset({ValueKind.DURATION, ValueKind.SIZE})


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
        if not namespace_supports_declared_scope(self.namespace, self.available_at):
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
class SensitiveLiteralExpr(msgspec.Struct, frozen=True, kw_only=True):
    """A sealed literal compiled from a secret declaration without exposing its payload."""

    value: SensitiveValue[ExactValue]
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require the exact sealed carrier and one closed literal payload type."""
        if not is_sensitive_value(self.value) or not is_exact_value_type(self.value.payload_type):
            raise TypeError("sensitive literal expression requires a sealed exact value")
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
    provenance: ValueProvenance
    maximum_bytes: ByteCount | None = None

    def __post_init__(self) -> None:
        """Require an expression path and validate an optional narrowing byte budget."""
        if not is_value_expression(self.path):
            raise TypeError("file path must be a value expression")
        if not expression_outputs_only(self.path, _TEXT_OUTPUT_KINDS):
            raise ValueError("file path expression must resolve to text")
        if self.conversion is not None and type(self.conversion) is not ConversionType:
            raise TypeError("file conversion must be a ConversionType")
        if type(self.trim) is not FileTrim:
            raise TypeError("file trim behavior must be a FileTrim")
        if self.conversion is None and self.trim is not FileTrim.NONE:
            raise ValueError("raw byte file reads cannot apply text trimming")
        if self.maximum_bytes is not None and type(self.maximum_bytes) is not ByteCount:
            raise TypeError("file maximum bytes must be a ByteCount when provided")
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
class ArrayExpr(msgspec.Struct, frozen=True, kw_only=True):
    """An ordered runtime array whose members may independently resolve."""

    items: tuple[ValueExpression, ...]
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require typed members and reject unsupported list-element omission."""
        if type(self.items) is not tuple:
            raise TypeError("array expression items must be an immutable tuple")
        if any(not is_value_expression(item) for item in self.items):
            raise TypeError("array expression items must be value expressions")
        if any(type(item) is OptionalExpr for item in self.items):
            raise ValueError("array expression items cannot be optional")
        _require_provenance(self.provenance)


@final
class ObjectExpressionMember(msgspec.Struct, frozen=True, kw_only=True):
    """One source-ordered runtime object member."""

    name: str
    expression: ValueExpression

    def __post_init__(self) -> None:
        """Require an exact member name and typed expression."""
        if type(self.name) is not str:
            raise TypeError("object expression member name must be text")
        if not is_value_expression(self.expression):
            raise TypeError("object expression member must contain a value expression")


@final
class ObjectExpr(msgspec.Struct, frozen=True, kw_only=True):
    """A runtime object retaining deterministic source member order."""

    members: tuple[ObjectExpressionMember, ...]
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require typed members and reject ambiguous duplicate names."""
        if type(self.members) is not tuple or any(
            type(member) is not ObjectExpressionMember for member in self.members
        ):
            raise TypeError("object expression members must be an immutable tuple of members")
        names = tuple(member.name for member in self.members)
        if len(names) != len(set(names)):
            raise ValueError("object expression member names must be unique")
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
        if self.mode is InterpolationMode.EMBEDDED_TEXT and any(
            type(segment) is ExpressionSegment
            and not expression_outputs_only(
                segment.expression,
                _TEXT_RENDERABLE_OUTPUT_KINDS,
            )
            for segment in self.segments
        ):
            raise ValueError(
                "embedded-text interpolation expressions must be scalar-text-renderable"
            )
        _require_provenance(self.provenance)


type ValueExpression = (
    LiteralExpr
    | SensitiveLiteralExpr
    | LookupExpr
    | FileExpr
    | FallbackExpr
    | OptionalExpr
    | ConvertExpr
    | ArrayExpr
    | ObjectExpr
    | InterpolationExpr
)


def is_value_expression(value: object) -> TypeGuard[ValueExpression]:
    """Narrow a value to the closed canonical expression union."""
    return type(value) in {
        LiteralExpr,
        SensitiveLiteralExpr,
        LookupExpr,
        FileExpr,
        FallbackExpr,
        OptionalExpr,
        ConvertExpr,
        ArrayExpr,
        ObjectExpr,
        InterpolationExpr,
    }


def expression_output_kinds(expression: ValueExpression) -> frozenset[ValueKind]:
    """Infer every possible static output kind without depending on a consumer layer."""
    if type(expression) in {
        LiteralExpr,
        SensitiveLiteralExpr,
        LookupExpr,
        FileExpr,
        ConvertExpr,
    }:
        return _atomic_output_kinds(cast("AtomicExpression", expression))
    if type(expression) in {
        FallbackExpr,
        OptionalExpr,
        ArrayExpr,
        ObjectExpr,
        InterpolationExpr,
    }:
        return _composite_output_kinds(cast("CompositeExpression", expression))
    raise TypeError("unsupported value expression")


def expression_outputs_only(
    expression: ValueExpression,
    accepted: frozenset[ValueKind],
) -> bool:
    """Accept unknown output while requiring every statically known kind to be compatible."""
    if not is_value_expression(expression):
        raise TypeError("output compatibility requires a value expression")
    if type(accepted) is not frozenset or any(type(kind) is not ValueKind for kind in accepted):
        raise TypeError("accepted output kinds must be a frozenset of ValueKind values")
    return all(
        kind is ValueKind.UNKNOWN or kind in accepted
        for kind in expression_output_kinds(expression)
    )


type AtomicExpression = LiteralExpr | SensitiveLiteralExpr | LookupExpr | FileExpr | ConvertExpr
type CompositeExpression = FallbackExpr | OptionalExpr | ArrayExpr | ObjectExpr | InterpolationExpr


def _atomic_output_kinds(expression: AtomicExpression) -> frozenset[ValueKind]:
    if type(expression) is LiteralExpr:
        return frozenset({_literal_type_output_kind(type(expression.value))})
    if type(expression) is SensitiveLiteralExpr:
        return frozenset({_literal_type_output_kind(expression.value.payload_type)})
    if type(expression) is LookupExpr:
        return frozenset({ValueKind.UNKNOWN})
    if type(expression) is FileExpr:
        if expression.conversion is None:
            return frozenset({ValueKind.BYTES})
        return conversion_output_kinds(expression.conversion)
    return conversion_output_kinds(expression.target)


def _composite_output_kinds(expression: CompositeExpression) -> frozenset[ValueKind]:
    if type(expression) is FallbackExpr:
        return frozenset().union(
            *(expression_output_kinds(source) for source in expression.sources)
        )
    if type(expression) is OptionalExpr:
        return expression_output_kinds(expression.source)
    if type(expression) is ArrayExpr:
        return frozenset({ValueKind.ARRAY})
    if type(expression) is ObjectExpr:
        return frozenset({ValueKind.OBJECT})
    if expression.mode is InterpolationMode.EMBEDDED_TEXT:
        return _TEXT_OUTPUT_KINDS
    segment = expression.segments[0]
    if type(segment) is not ExpressionSegment:  # pragma: no cover - constructor invariant
        raise TypeError("whole-value interpolation requires an expression segment")
    return expression_output_kinds(segment.expression)


def _literal_type_output_kind(value_type: type[object]) -> ValueKind:
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
    try:
        return mapping[value_type]
    except KeyError as error:  # pragma: no cover - literal constructors close the value union
        raise TypeError("literal expression contains an unsupported value") from error


def _require_provenance(provenance: object) -> None:
    if type(provenance) is not ValueProvenance:
        raise TypeError("expression provenance must be ValueProvenance")
