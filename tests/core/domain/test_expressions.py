"""Tests for the canonical value-expression tree."""

from typing import cast

import pytest

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
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SymbolPath
from comparo.core.domain.expressions import ValueNamespace
from comparo.core.domain.expressions import expression_dependencies
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.values import BytesValue
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


def test_file_expression_requires_explicit_bounded_read_semantics() -> None:
    path = LiteralExpr(value=JsonString(value="token.txt"), provenance=_provenance())
    raw = FileExpr(
        path=path,
        conversion=None,
        trim=FileTrim.NONE,
        maximum_bytes=ByteCount(value=1024),
        provenance=_provenance(),
    )
    assert raw.conversion is None
    with pytest.raises(ValueError, match="cannot apply text trimming"):
        FileExpr(
            path=path,
            conversion=None,
            trim=FileTrim.END,
            maximum_bytes=ByteCount(value=1024),
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
