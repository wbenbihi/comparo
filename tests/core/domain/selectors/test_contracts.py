"""Exercise static value contracts and observation-independent item selection."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import msgspec
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
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.contracts import contract_accepts
from comparo.core.domain.selectors.contracts import contract_can_feed
from comparo.core.domain.selectors.contracts import contracts_overlap
from comparo.core.domain.selectors.contracts import expression_accepts
from comparo.core.domain.selectors.contracts import expression_kinds
from comparo.core.domain.selectors.contracts import is_statically_negative_number
from comparo.core.domain.selectors.contracts import is_value_contract
from comparo.core.domain.selectors.contracts import scalar_contract
from comparo.core.domain.selectors.contracts import with_cardinality
from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.items import ItemEqualityPredicate
from comparo.core.domain.selectors.items import ItemPredicateIdentity
from comparo.core.domain.selectors.items import ItemPredicateKind
from comparo.core.domain.selectors.items import ItemTypePredicate
from comparo.core.domain.selectors.items import is_item_predicate
from comparo.core.domain.selectors.items import is_singular_item_selector
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.model import derive_path_capabilities
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="a" * 64))


def _provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),))


def _literal(value: ExactValue) -> LiteralExpr:
    return LiteralExpr(value=value, provenance=_provenance())


def _lookup() -> LookupExpr:
    return LookupExpr(
        dependency=ExpressionDependency(
            namespace=ValueNamespace.VARIABLES,
            symbol=SymbolPath(segments=("value",)),
            available_at=ValueScopeKind.RUN,
        ),
        provenance=_provenance(),
    )


def _contract(
    kind: ValueKind,
    cardinality: SelectorCardinality = SelectorCardinality.EXACTLY_ONE,
) -> ValueContract:
    return ValueContract(kinds=(kind,), cardinality=cardinality)


def _item_selector(
    *,
    input_kind: ValueKind = ValueKind.OBJECT,
    output_kind: ValueKind = ValueKind.STRING,
    path: SelectorPath | None = None,
) -> CompiledItemSelector:
    actual_path = JsonPointerPath(pointer=JsonPointer(value="/id")) if path is None else path
    capabilities = derive_path_capabilities(actual_path)
    return CompiledItemSelector(
        fingerprint=_fingerprint(),
        input=_contract(input_kind),
        path=actual_path,
        capabilities=capabilities,
        output=_contract(output_kind, capabilities.cardinality),
    )


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (JsonNull(), ValueKind.NULL),
        (JsonBoolean(value=True), ValueKind.BOOLEAN),
        (JsonInteger(value=1), ValueKind.INTEGER),
        (JsonDecimal(value=Decimal("1.5")), ValueKind.NUMBER),
        (JsonString(value="x"), ValueKind.STRING),
        (JsonArray(items=()), ValueKind.ARRAY),
        (JsonObject(members=()), ValueKind.OBJECT),
        (BytesValue(value=b"x"), ValueKind.BYTES),
    ],
)
def test_literal_expression_contracts_are_exact(value: ExactValue, kind: ValueKind) -> None:
    assert expression_kinds(_literal(value)) == frozenset({kind})


def test_value_contract_rejects_ambiguous_shapes() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ValueContract(kinds=(), cardinality=SelectorCardinality.EXACTLY_ONE)
    with pytest.raises(TypeError, match="ValueKind"):
        ValueContract(
            kinds=(cast("ValueKind", "string"),),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        )
    with pytest.raises(ValueError, match="unique"):
        ValueContract(
            kinds=(ValueKind.STRING, ValueKind.STRING),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        )
    with pytest.raises(ValueError, match="cannot be combined"):
        ValueContract(
            kinds=(ValueKind.UNKNOWN, ValueKind.STRING),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        )
    with pytest.raises(TypeError, match="cardinality"):
        ValueContract(
            kinds=(ValueKind.STRING,),
            cardinality=cast("SelectorCardinality", "one"),
        )


def test_contract_compatibility_is_conservative_only_for_unknowns() -> None:
    integer = _contract(ValueKind.INTEGER)
    number = _contract(ValueKind.NUMBER)
    text = _contract(ValueKind.STRING)
    unknown = _contract(ValueKind.UNKNOWN)
    assert contract_accepts(integer, frozenset({ValueKind.NUMBER}))
    assert contract_accepts(unknown, frozenset({ValueKind.STRING}))
    assert not contract_accepts(text, frozenset({ValueKind.NUMBER}))
    assert contracts_overlap(integer, number)
    assert contracts_overlap(unknown, text)
    assert not contracts_overlap(text, number)
    assert contract_can_feed(integer, number)
    assert contract_can_feed(text, unknown)
    assert contract_can_feed(unknown, text)
    assert not contract_can_feed(text, number)
    with pytest.raises(TypeError):
        contract_accepts(cast("ValueContract", "bad"), frozenset({ValueKind.STRING}))
    with pytest.raises(TypeError):
        contracts_overlap(cast("ValueContract", "bad"), text)
    with pytest.raises(TypeError):
        contract_can_feed(text, cast("ValueContract", "bad"))
    with pytest.raises(TypeError):
        with_cardinality(cast("ValueContract", "bad"), SelectorCardinality.EXACTLY_ONE)
    assert is_value_contract(scalar_contract(ValueKind.STRING))
    assert not is_value_contract("string")


@pytest.mark.parametrize(
    ("conversion", "expected"),
    [
        (ConversionType.STRING, frozenset({ValueKind.STRING})),
        (ConversionType.BYTES, frozenset({ValueKind.BYTES})),
        (ConversionType.INTEGER, frozenset({ValueKind.INTEGER})),
        (ConversionType.NUMBER, frozenset({ValueKind.NUMBER})),
        (ConversionType.BOOLEAN, frozenset({ValueKind.BOOLEAN})),
        (ConversionType.JSON, JSON_VALUE_KINDS),
        (ConversionType.YAML, JSON_VALUE_KINDS),
        (ConversionType.DURATION, frozenset({ValueKind.DURATION})),
        (ConversionType.SIZE, frozenset({ValueKind.SIZE})),
    ],
)
def test_explicit_conversions_produce_static_types(
    conversion: ConversionType,
    expected: frozenset[ValueKind],
) -> None:
    expression = ConvertExpr(source=_lookup(), target=conversion, provenance=_provenance())
    assert expression_kinds(expression) == expected


def test_file_lookup_fallback_optional_and_interpolation_contracts() -> None:
    raw_file = FileExpr(
        path=_literal(JsonString(value="fixture")),
        conversion=None,
        trim=FileTrim.NONE,
        maximum_bytes=ByteCount(value=10),
        provenance=_provenance(),
    )
    text_file = msgspec.structs.replace(raw_file, conversion=ConversionType.STRING)
    assert expression_kinds(_lookup()) == frozenset({ValueKind.UNKNOWN})
    assert expression_kinds(raw_file) == frozenset({ValueKind.BYTES})
    assert expression_kinds(text_file) == frozenset({ValueKind.STRING})
    fallback = FallbackExpr(
        sources=(_literal(JsonString(value="x")), _literal(JsonInteger(value=1))),
        provenance=_provenance(),
    )
    assert expression_kinds(fallback) == frozenset({ValueKind.STRING, ValueKind.INTEGER})
    assert expression_kinds(
        msgspec.structs.replace(fallback, sources=(_lookup(), _literal(JsonString(value="x"))))
    ) == frozenset({ValueKind.UNKNOWN})
    assert expression_kinds(
        OptionalExpr(source=fallback, provenance=_provenance())
    ) == expression_kinds(fallback)
    embedded = InterpolationExpr(
        mode=InterpolationMode.EMBEDDED_TEXT,
        segments=(LiteralTextSegment(value="x="), ExpressionSegment(expression=_lookup())),
        provenance=_provenance(),
    )
    whole = InterpolationExpr(
        mode=InterpolationMode.WHOLE_VALUE,
        segments=(ExpressionSegment(expression=_literal(JsonInteger(value=1))),),
        provenance=_provenance(),
    )
    assert expression_kinds(embedded) == frozenset({ValueKind.STRING})
    assert expression_kinds(whole) == frozenset({ValueKind.INTEGER})
    assert expression_accepts(_lookup(), frozenset({ValueKind.STRING}))
    assert expression_accepts(_literal(JsonInteger(value=1)), frozenset({ValueKind.NUMBER}))
    assert not expression_accepts(_literal(JsonString(value="x")), frozenset({ValueKind.NUMBER}))


def test_negative_literal_detection_is_exact_and_does_not_guess_dynamic_values() -> None:
    assert is_statically_negative_number(_literal(JsonInteger(value=-1)))
    assert is_statically_negative_number(_literal(JsonDecimal(value=Decimal("-0.5"))))
    assert not is_statically_negative_number(_literal(JsonInteger(value=0)))
    assert not is_statically_negative_number(_lookup())


def test_item_selector_is_relative_typed_and_cardinality_checked() -> None:
    selector = _item_selector()
    assert is_singular_item_selector(selector)
    with pytest.raises(ValueError, match="exactly one"):
        msgspec.structs.replace(
            selector,
            input=_contract(ValueKind.OBJECT, SelectorCardinality.ZERO_OR_MORE),
        )
    with pytest.raises(ValueError, match="object or array"):
        msgspec.structs.replace(selector, input=_contract(ValueKind.STRING))
    with pytest.raises(ValueError, match="capabilities"):
        msgspec.structs.replace(
            selector,
            capabilities=SelectorCapabilities(
                cardinality=SelectorCardinality.EXACTLY_ONE,
                access=SelectionAccess.INCREMENTAL,
                may_duplicate=False,
                uses_descendant=False,
                uses_filter=False,
            ),
        )
    with pytest.raises(ValueError, match="output cardinality"):
        msgspec.structs.replace(selector, output=_contract(ValueKind.STRING))
    with pytest.raises(TypeError, match="path"):
        derive_path_capabilities(cast("NoPath", "bad"))
    assert derive_path_capabilities(NoPath()).cardinality is SelectorCardinality.EXACTLY_ONE
    assert (
        derive_path_capabilities(StaticFieldPath(segments=("id",))).cardinality
        is SelectorCardinality.ZERO_OR_ONE
    )


def test_item_selector_components_are_strictly_typed() -> None:
    selector = _item_selector()
    replacements = (
        ("fingerprint", cast("Fingerprint", "bad")),
        ("input", cast("ValueContract", "bad")),
        ("output", cast("ValueContract", "bad")),
        ("path", cast("SelectorPath", "bad")),
        ("capabilities", cast("SelectorCapabilities", "bad")),
    )
    for field, value in replacements:
        with pytest.raises(TypeError):
            msgspec.structs.replace(selector, **{field: value})


def test_compiled_selector_output_contract_is_bound_to_source_and_view() -> None:
    capabilities = derive_path_capabilities(NoPath())
    selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_STATUS,
        path=NoPath(),
        capabilities=capabilities,
        output=_contract(ValueKind.INTEGER),
    )
    with pytest.raises(TypeError, match="output"):
        msgspec.structs.replace(selector, output=cast("ValueContract", "bad"))
    with pytest.raises(ValueError, match="output cardinality"):
        msgspec.structs.replace(
            selector,
            output=_contract(ValueKind.INTEGER, SelectorCardinality.ZERO_OR_ONE),
        )
    with pytest.raises(ValueError, match="source and view"):
        msgspec.structs.replace(selector, output=_contract(ValueKind.STRING))
    body = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_BODY,
        path=NoPath(),
        capabilities=capabilities,
        output=_contract(ValueKind.STRING),
        body_view=BodyViewKind.TEXT,
    )
    assert body.output.kinds == (ValueKind.STRING,)


def test_item_predicates_bind_identity_operator_and_static_operand_types() -> None:
    selector = _item_selector()
    identity = ItemPredicateIdentity(
        namespace="comparo.core",
        kind=ItemPredicateKind.EQUALS,
        version="1",
    )
    predicate = CompiledItemPredicate(
        identity=identity,
        selector=selector,
        operator=ItemEqualityPredicate(expected=_literal(JsonString(value="x"))),
    )
    assert is_item_predicate(predicate)
    assert not is_item_predicate("predicate")
    with pytest.raises(ValueError, match="operand"):
        msgspec.structs.replace(
            predicate,
            operator=ItemEqualityPredicate(expected=_literal(JsonInteger(value=1))),
        )
    with pytest.raises(ValueError, match="does not match"):
        msgspec.structs.replace(
            predicate,
            identity=msgspec.structs.replace(identity, kind=ItemPredicateKind.IS_TYPE),
        )
    typed = CompiledItemPredicate(
        identity=msgspec.structs.replace(identity, kind=ItemPredicateKind.IS_TYPE),
        selector=selector,
        operator=ItemTypePredicate(expected=ValueKind.STRING),
    )
    assert typed.operator.expected is ValueKind.STRING
    with pytest.raises(ValueError, match="unknown"):
        ItemTypePredicate(expected=ValueKind.UNKNOWN)
    with pytest.raises(ValueError, match="version 1"):
        msgspec.structs.replace(
            predicate,
            identity=msgspec.structs.replace(identity, namespace="community.example"),
        )


def test_item_predicate_components_are_closed_and_strictly_typed() -> None:
    identity = ItemPredicateIdentity(
        namespace="comparo.core",
        kind=ItemPredicateKind.EQUALS,
        version="1",
    )
    with pytest.raises(ValueError, match="namespace"):
        msgspec.structs.replace(identity, namespace="Bad.Namespace")
    with pytest.raises(TypeError, match="kind"):
        msgspec.structs.replace(identity, kind=cast("ItemPredicateKind", "equals"))
    with pytest.raises(ValueError, match="positive"):
        msgspec.structs.replace(identity, version="0")
    with pytest.raises(TypeError, match="ValueExpression"):
        ItemEqualityPredicate(expected=cast("LiteralExpr", "bad"))
    with pytest.raises(TypeError, match="ValueKind"):
        ItemTypePredicate(expected=cast("ValueKind", "string"))
    predicate = CompiledItemPredicate(
        identity=identity,
        selector=_item_selector(),
        operator=ItemEqualityPredicate(expected=_literal(JsonString(value="x"))),
    )
    with pytest.raises(TypeError, match="identity"):
        msgspec.structs.replace(
            predicate,
            identity=cast("ItemPredicateIdentity", "bad"),
        )
    with pytest.raises(TypeError, match="selector"):
        msgspec.structs.replace(
            predicate,
            selector=cast("CompiledItemSelector", "bad"),
        )
    with pytest.raises(TypeError, match="operator"):
        msgspec.structs.replace(
            predicate,
            operator=cast("ItemEqualityPredicate", "bad"),
        )


def test_plural_item_selection_is_explicit() -> None:
    capabilities = SelectorCapabilities(
        cardinality=SelectorCardinality.ZERO_OR_MORE,
        access=SelectionAccess.INDEXED,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )
    selector = CompiledItemSelector(
        fingerprint=_fingerprint(),
        input=_contract(ValueKind.ARRAY),
        path=JsonPathExpression(
            canonical="$[*]",
            fingerprint=_fingerprint(),
            capabilities=capabilities,
        ),
        capabilities=capabilities,
        output=_contract(ValueKind.STRING, SelectorCardinality.ZERO_OR_MORE),
    )
    assert not is_singular_item_selector(selector)


def test_no_path_item_selection_preserves_the_complete_input_contract() -> None:
    capabilities = derive_path_capabilities(NoPath())
    input_contract = _contract(ValueKind.OBJECT)
    selector = CompiledItemSelector(
        fingerprint=_fingerprint(),
        input=input_contract,
        path=NoPath(),
        capabilities=capabilities,
        output=input_contract,
    )
    assert selector.output == selector.input
    with pytest.raises(ValueError, match="must preserve"):
        msgspec.structs.replace(
            selector,
            output=_contract(ValueKind.STRING),
        )
