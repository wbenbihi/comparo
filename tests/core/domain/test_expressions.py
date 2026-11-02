"""Tests for the canonical value-expression tree."""

from decimal import Decimal
from typing import cast

import pytest

from comparo.core.domain.expression_analysis import expression_dependencies
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConversionType
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionDependency
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import FileTrim
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import InterpolationMode
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LiteralTextSegment
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SymbolPath
from comparo.core.domain.expressions import ValueNamespace
from comparo.core.domain.expressions import expression_output_kinds
from comparo.core.domain.expressions import expression_outputs_only
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString


def _provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def _lookup(name: str = "TOKEN") -> LookupExpr:
    return LookupExpr(
        dependency=ExpressionDependency(
            namespace=ValueNamespace.ENV,
            symbol=SymbolPath(segments=(name,)),
            available_at=ValueScopeKind.ENVIRONMENT,
        ),
        provenance=_provenance(),
    )


def _literal(value: ExactValue) -> LiteralExpr:
    return LiteralExpr(value=value, provenance=_provenance())


def _raw_file() -> FileExpr:
    return FileExpr(
        path=_literal(JsonString(value="fixture")),
        conversion=None,
        trim=FileTrim.NONE,
        maximum_bytes=ByteCount(value=10),
        provenance=_provenance(),
    )


def test_symbol_paths_are_non_empty_static_segments() -> None:
    assert SymbolPath(segments=("login", "token")).segments == ("login", "token")
    with pytest.raises(TypeError, match="immutable tuple"):
        SymbolPath(segments=["token"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one"):
        SymbolPath(segments=())
    with pytest.raises(ValueError, match="invalid segment"):
        SymbolPath(segments=("bad.path",))


def test_dependency_requires_typed_namespace_symbol_and_scope() -> None:
    dependency = _lookup().dependency
    assert dependency.available_at is ValueScopeKind.ENVIRONMENT
    with pytest.raises(TypeError, match="ValueNamespace"):
        ExpressionDependency(
            namespace="env",  # type: ignore[arg-type]
            symbol=dependency.symbol,
            available_at=dependency.available_at,
        )
    with pytest.raises(TypeError, match="SymbolPath"):
        ExpressionDependency(
            namespace=dependency.namespace,
            symbol="TOKEN",  # type: ignore[arg-type]
            available_at=dependency.available_at,
        )
    with pytest.raises(TypeError, match="ValueScopeKind"):
        ExpressionDependency(
            namespace=dependency.namespace,
            symbol=dependency.symbol,
            available_at="environment",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("namespace", "scope"),
    [
        (ValueNamespace.ENV, ValueScopeKind.RUN),
        (ValueNamespace.PARAMETERS, ValueScopeKind.RUN),
        (ValueNamespace.MATRIX, ValueScopeKind.STEP),
        (ValueNamespace.STEPS, ValueScopeKind.CELL),
    ],
)
def test_dependency_namespace_rejects_unavailable_scopes(
    namespace: ValueNamespace,
    scope: ValueScopeKind,
) -> None:
    with pytest.raises(ValueError, match="unavailable"):
        ExpressionDependency(
            namespace=namespace,
            symbol=SymbolPath(segments=("value",)),
            available_at=scope,
        )


def test_literal_requires_exact_value_and_provenance() -> None:
    literal = LiteralExpr(value=JsonString(value="public"), provenance=_provenance())
    assert expression_dependencies(literal) == ()
    byte_literal = LiteralExpr(value=BytesValue(value=b"raw"), provenance=_provenance())
    assert isinstance(byte_literal.value, BytesValue)
    assert byte_literal.value.value == b"raw"
    with pytest.raises(TypeError, match="exact value"):
        LiteralExpr(value="public", provenance=_provenance())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ValueProvenance"):
        LiteralExpr(value=JsonString(value="public"), provenance="project")  # type: ignore[arg-type]


def test_lookup_requires_typed_dependency_and_provenance() -> None:
    lookup = _lookup()
    assert expression_dependencies(lookup) == (lookup.dependency,)
    with pytest.raises(TypeError, match="ExpressionDependency"):
        LookupExpr(dependency="env.TOKEN", provenance=_provenance())  # type: ignore[arg-type]


def test_file_expression_supports_policy_cap_with_optional_configuration_narrowing() -> None:
    path = LiteralExpr(value=JsonString(value="token.txt"), provenance=_provenance())
    raw = FileExpr(
        path=path,
        conversion=None,
        trim=FileTrim.NONE,
        maximum_bytes=ByteCount(value=1024),
        provenance=_provenance(),
    )
    assert raw.conversion is None
    uncapped_by_configuration = FileExpr(
        path=path,
        conversion=None,
        trim=FileTrim.NONE,
        provenance=_provenance(),
    )
    assert uncapped_by_configuration.maximum_bytes is None
    with pytest.raises(ValueError, match="cannot apply text trimming"):
        FileExpr(
            path=path,
            conversion=None,
            trim=FileTrim.END,
            maximum_bytes=ByteCount(value=1024),
            provenance=_provenance(),
        )


def test_file_path_accepts_text_and_defers_unknown_outputs() -> None:
    text = _literal(JsonString(value="fixture"))
    unknown = _lookup()
    dynamic_text = FallbackExpr(sources=(unknown, text), provenance=_provenance())
    for path in (text, unknown, dynamic_text):
        expression = FileExpr(
            path=path,
            conversion=None,
            trim=FileTrim.NONE,
            maximum_bytes=ByteCount(value=10),
            provenance=_provenance(),
        )
        assert expression.path is path


@pytest.mark.parametrize(
    "path",
    [
        _literal(BytesValue(value=b"fixture")),
        _literal(JsonArray(items=())),
        _literal(JsonObject(members=())),
        ConvertExpr(
            source=_lookup(),
            target=ConversionType.JSON,
            provenance=_provenance(),
        ),
        FallbackExpr(
            sources=(_lookup(), _literal(JsonString(value="fixture")), _raw_file()),
            provenance=_provenance(),
        ),
    ],
)
def test_file_path_rejects_every_statically_incompatible_output(
    path: LiteralExpr | ConvertExpr | FallbackExpr,
) -> None:
    with pytest.raises(ValueError, match="must resolve to text"):
        FileExpr(
            path=path,
            conversion=None,
            trim=FileTrim.NONE,
            maximum_bytes=ByteCount(value=10),
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="value expression"):
        FileExpr(
            path="token.txt",  # type: ignore[arg-type]
            conversion=None,
            trim=FileTrim.NONE,
            maximum_bytes=ByteCount(value=1024),
            provenance=_provenance(),
        )


def test_fallback_optional_and_conversion_are_typed_and_ordered() -> None:
    lookup = _lookup()
    literal = LiteralExpr(value=JsonString(value="public"), provenance=_provenance())
    fallback = FallbackExpr(sources=(lookup, literal), provenance=_provenance())
    optional = OptionalExpr(source=fallback, provenance=_provenance())
    converted = ConvertExpr(
        source=optional,
        target=ConversionType.STRING,
        provenance=_provenance(),
    )
    assert expression_dependencies(converted) == (lookup.dependency,)
    with pytest.raises(ValueError, match="at least one source"):
        FallbackExpr(sources=(), provenance=_provenance())
    with pytest.raises(TypeError, match="immutable tuple"):
        FallbackExpr(sources=[lookup], provenance=_provenance())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="value expressions"):
        FallbackExpr(sources=("env.TOKEN",), provenance=_provenance())  # type: ignore[arg-type]


def test_array_and_object_expressions_compose_nested_runtime_values() -> None:
    lookup = _lookup()
    array = ArrayExpr(
        items=(lookup, _literal(JsonInteger(value=1))),
        provenance=_provenance(),
    )
    optional = OptionalExpr(source=lookup, provenance=_provenance())
    object_expression = ObjectExpr(
        members=(
            ObjectExpressionMember(name="items", expression=array),
            ObjectExpressionMember(name="optional", expression=optional),
        ),
        provenance=_provenance(),
    )
    assert expression_output_kinds(array) == frozenset({ValueKind.ARRAY})
    assert expression_output_kinds(object_expression) == frozenset({ValueKind.OBJECT})
    assert expression_dependencies(object_expression) == (lookup.dependency,)


def test_array_and_object_expressions_reject_ambiguous_shapes() -> None:
    lookup = _lookup()
    member = ObjectExpressionMember(name="value", expression=lookup)
    with pytest.raises(TypeError, match="immutable tuple"):
        ArrayExpr(items=cast("tuple[LookupExpr, ...]", [lookup]), provenance=_provenance())
    with pytest.raises(TypeError, match="value expressions"):
        ArrayExpr(items=cast("tuple[LookupExpr, ...]", (object(),)), provenance=_provenance())
    with pytest.raises(ValueError, match="cannot be optional"):
        ArrayExpr(
            items=(OptionalExpr(source=lookup, provenance=_provenance()),),
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="name must be text"):
        ObjectExpressionMember(name=cast("str", 1), expression=lookup)
    with pytest.raises(TypeError, match="value expression"):
        ObjectExpressionMember(name="value", expression=cast("LookupExpr", object()))
    with pytest.raises(TypeError, match="immutable tuple"):
        ObjectExpr(
            members=cast("tuple[ObjectExpressionMember, ...]", [member]),
            provenance=_provenance(),
        )
    with pytest.raises(ValueError, match="unique"):
        ObjectExpr(members=(member, member), provenance=_provenance())


def test_interpolation_distinguishes_native_whole_value_from_embedded_text() -> None:
    lookup = _lookup()
    segment = ExpressionSegment(expression=lookup)
    whole = InterpolationExpr(
        mode=InterpolationMode.WHOLE_VALUE,
        segments=(segment,),
        provenance=_provenance(),
    )
    embedded = InterpolationExpr(
        mode=InterpolationMode.EMBEDDED_TEXT,
        segments=(LiteralTextSegment(value="Bearer "), segment),
        provenance=_provenance(),
    )
    assert expression_dependencies(whole) == (lookup.dependency,)
    assert expression_dependencies(embedded) == (lookup.dependency,)
    with pytest.raises(ValueError, match="exactly one expression"):
        InterpolationExpr(
            mode=InterpolationMode.WHOLE_VALUE,
            segments=(LiteralTextSegment(value="prefix"), segment),
            provenance=_provenance(),
        )


@pytest.mark.parametrize(
    "expression",
    [
        _lookup(),
        _literal(JsonNull()),
        _literal(JsonBoolean(value=True)),
        _literal(JsonInteger(value=1)),
        _literal(JsonDecimal(value=Decimal("1.5"))),
        _literal(JsonString(value="value")),
        ConvertExpr(
            source=_lookup(),
            target=ConversionType.DURATION,
            provenance=_provenance(),
        ),
        ConvertExpr(
            source=_lookup(),
            target=ConversionType.SIZE,
            provenance=_provenance(),
        ),
        FallbackExpr(
            sources=(_lookup(), _literal(JsonInteger(value=1))),
            provenance=_provenance(),
        ),
    ],
)
def test_embedded_text_accepts_scalar_renderable_and_unknown_outputs(
    expression: LookupExpr | LiteralExpr | ConvertExpr | FallbackExpr,
) -> None:
    interpolation = InterpolationExpr(
        mode=InterpolationMode.EMBEDDED_TEXT,
        segments=(LiteralTextSegment(value="value="), ExpressionSegment(expression=expression)),
        provenance=_provenance(),
    )
    assert expression_output_kinds(interpolation) == frozenset({ValueKind.STRING})


@pytest.mark.parametrize(
    "expression",
    [
        _literal(BytesValue(value=b"value")),
        _literal(JsonArray(items=())),
        _literal(JsonObject(members=())),
        ConvertExpr(
            source=_lookup(),
            target=ConversionType.JSON,
            provenance=_provenance(),
        ),
        ConvertExpr(
            source=_lookup(),
            target=ConversionType.YAML,
            provenance=_provenance(),
        ),
        FallbackExpr(
            sources=(_lookup(), _literal(JsonObject(members=()))),
            provenance=_provenance(),
        ),
    ],
)
def test_embedded_text_rejects_every_statically_non_renderable_output(
    expression: LiteralExpr | ConvertExpr | FallbackExpr,
) -> None:
    with pytest.raises(ValueError, match="scalar-text-renderable"):
        InterpolationExpr(
            mode=InterpolationMode.EMBEDDED_TEXT,
            segments=(ExpressionSegment(expression=expression),),
            provenance=_provenance(),
        )


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (BytesValue(value=b"value"), ValueKind.BYTES),
        (JsonArray(items=()), ValueKind.ARRAY),
        (JsonObject(members=()), ValueKind.OBJECT),
    ],
)
def test_whole_value_interpolation_preserves_native_output(
    value: ExactValue,
    kind: ValueKind,
) -> None:
    interpolation = InterpolationExpr(
        mode=InterpolationMode.WHOLE_VALUE,
        segments=(ExpressionSegment(expression=_literal(value)),),
        provenance=_provenance(),
    )
    assert expression_output_kinds(interpolation) == frozenset({kind})


def test_output_inference_preserves_unknown_and_every_known_fallback_branch() -> None:
    fallback = FallbackExpr(
        sources=(
            _lookup(),
            _literal(JsonString(value="value")),
            _literal(BytesValue(value=b"value")),
        ),
        provenance=_provenance(),
    )
    assert expression_output_kinds(fallback) == frozenset(
        {ValueKind.UNKNOWN, ValueKind.STRING, ValueKind.BYTES}
    )
    assert not expression_outputs_only(fallback, frozenset({ValueKind.STRING}))
    assert expression_outputs_only(_lookup(), frozenset({ValueKind.STRING}))
    with pytest.raises(ValueError, match="at least one expression"):
        InterpolationExpr(
            mode=InterpolationMode.EMBEDDED_TEXT,
            segments=(LiteralTextSegment(value="literal"),),
            provenance=_provenance(),
        )


def test_dependency_collection_is_stable_and_deduplicated() -> None:
    lookup = _lookup()
    fallback = FallbackExpr(sources=(lookup, lookup), provenance=_provenance())
    file_expression = FileExpr(
        path=fallback,
        conversion=ConversionType.STRING,
        trim=FileTrim.BOTH,
        maximum_bytes=ByteCount(value=20),
        provenance=_provenance(),
    )
    assert expression_dependencies(file_expression) == (lookup.dependency,)


def test_file_expression_rejects_untyped_options() -> None:
    path = LiteralExpr(value=JsonString(value="token.txt"), provenance=_provenance())
    with pytest.raises(TypeError, match="ConversionType"):
        FileExpr(
            path=path,
            conversion=cast("ConversionType", "text"),
            trim=FileTrim.NONE,
            maximum_bytes=ByteCount(value=10),
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="FileTrim"):
        FileExpr(
            path=path,
            conversion=ConversionType.STRING,
            trim=cast("FileTrim", "none"),
            maximum_bytes=ByteCount(value=10),
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="ByteCount"):
        FileExpr(
            path=path,
            conversion=ConversionType.STRING,
            trim=FileTrim.NONE,
            maximum_bytes=cast("ByteCount", object()),
            provenance=_provenance(),
        )


def test_optional_conversion_and_segments_reject_untyped_values() -> None:
    lookup = _lookup()
    with pytest.raises(TypeError, match="optional source"):
        OptionalExpr(source=cast("LiteralExpr", object()), provenance=_provenance())
    with pytest.raises(TypeError, match="conversion source"):
        ConvertExpr(
            source=cast("LiteralExpr", object()),
            target=ConversionType.STRING,
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="ConversionType"):
        ConvertExpr(
            source=lookup,
            target=cast("ConversionType", "string"),
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="must be text"):
        LiteralTextSegment(value=cast("str", object()))
    with pytest.raises(TypeError, match="value expression"):
        ExpressionSegment(expression=cast("LiteralExpr", object()))


def test_interpolation_rejects_invalid_container_and_mode_states() -> None:
    segment = ExpressionSegment(expression=_lookup())
    with pytest.raises(TypeError, match="InterpolationMode"):
        InterpolationExpr(
            mode=cast("InterpolationMode", "embedded-text"),
            segments=(segment,),
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        InterpolationExpr(
            mode=InterpolationMode.EMBEDDED_TEXT,
            segments=cast("tuple[ExpressionSegment, ...]", [segment]),
            provenance=_provenance(),
        )
    with pytest.raises(ValueError, match="at least one segment"):
        InterpolationExpr(
            mode=InterpolationMode.EMBEDDED_TEXT,
            segments=(),
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="unsupported segment"):
        InterpolationExpr(
            mode=InterpolationMode.EMBEDDED_TEXT,
            segments=cast("tuple[ExpressionSegment, ...]", (object(),)),
            provenance=_provenance(),
        )


def test_dependency_collection_rejects_values_outside_closed_union() -> None:
    with pytest.raises(TypeError, match="unsupported value expression"):
        expression_dependencies(cast("LiteralExpr", object()))


def test_output_compatibility_rejects_values_outside_its_closed_inputs() -> None:
    with pytest.raises(TypeError, match="requires a value expression"):
        expression_outputs_only(cast("LiteralExpr", object()), frozenset({ValueKind.STRING}))
    with pytest.raises(TypeError, match="frozenset of ValueKind"):
        expression_outputs_only(_lookup(), cast("frozenset[ValueKind]", {ValueKind.STRING}))
    with pytest.raises(TypeError, match="unsupported value expression"):
        expression_output_kinds(cast("LiteralExpr", object()))
