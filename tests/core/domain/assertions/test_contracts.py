"""Exercise static assertion selector/operator compatibility."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.assertions.contracts import require_assertion_operator_contract
from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import CompiledSchema
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.extended_operators import DigestAlgorithm
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import MediaRelation
from comparo.core.domain.assertions.extended_operators import MediaTypeOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.extended_operators import TokenOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import AssertionOperator
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
from comparo.core.domain.assertions.regex import RegexCompilerIdentity
from comparo.core.domain.assertions.regex import RegexDialect
from comparo.core.domain.assertions.regex import RegexMode
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.expressions import ConversionType
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="b" * 64))


def _literal(value: ExactValue) -> LiteralExpr:
    return LiteralExpr(
        value=value,
        provenance=ValueProvenance(
            entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),),
        ),
    )


def _contract(kind: ValueKind) -> ValueContract:
    return ValueContract(kinds=(kind,), cardinality=SelectorCardinality.EXACTLY_ONE)


def _converted(target: ConversionType) -> ConvertExpr:
    source = _literal(JsonString(value="1s"))
    return ConvertExpr(source=source, target=target, provenance=source.provenance)


def _regex() -> RegexOperator:
    return RegexOperator(
        pattern="x+",
        mode=RegexMode.FULL,
        dialect=RegexDialect.RE2,
        compiler=RegexCompilerIdentity(namespace="comparo.core", name="re2", version="1"),
        compiled_fingerprint=_fingerprint(),
        negate=False,
    )


def _schema() -> SchemaOperator:
    return SchemaOperator(
        schema=CompiledSchema(
            fingerprint=_fingerprint(),
            remote_uri=NamedCapabilityTarget(
                kind=CapabilityTargetKind.SCHEMA_SOURCE,
                identifier="https://schemas.example.test/value.json",
            ),
        ),
        default_dialect=NamedCapabilityTarget(
            kind=CapabilityTargetKind.SCHEMA_SOURCE,
            identifier="https://json-schema.org/draft/2020-12/schema",
        ),
    )


@pytest.mark.parametrize(
    ("kind", "operator"),
    [
        (
            ValueKind.STRING,
            EqualityOperator(
                relation=EqualityRelation.EQUALS,
                expected=_literal(JsonString(value="x")),
            ),
        ),
        (
            ValueKind.STRING,
            MembershipOperator(
                relation=MembershipRelation.IN,
                choices=(_literal(JsonString(value="x")),),
            ),
        ),
        (ValueKind.STRING, TypeOperator(expected=ExactValueType.STRING)),
        (
            ValueKind.INTEGER,
            OrderingOperator(
                relation=OrderingRelation.GREATER,
                expected=_literal(JsonInteger(value=0)),
            ),
        ),
        (
            ValueKind.INTEGER,
            BetweenOperator(
                lower=_literal(JsonInteger(value=0)),
                upper=_literal(JsonInteger(value=2)),
                include_lower=True,
                include_upper=True,
            ),
        ),
        (
            ValueKind.NUMBER,
            ApproximateOperator(
                expected=_literal(JsonInteger(value=1)),
                absolute_tolerance=_literal(JsonInteger(value=0)),
            ),
        ),
        (
            ValueKind.STRING,
            StringOperator(
                relation=StringRelation.CONTAINS,
                expected=_literal(JsonString(value="x")),
            ),
        ),
        (ValueKind.STRING, _regex()),
        (
            ValueKind.ARRAY,
            ContainsOperator(
                mode=ContainsMode.ONE,
                expected=(_literal(JsonString(value="x")),),
            ),
        ),
        (
            ValueKind.OBJECT,
            KeySetOperator(relation=KeySetRelation.HAS, keys=("id",)),
        ),
        (
            ValueKind.BYTES,
            LengthOperator(relation=CountRelation.AT_LEAST, expected=Count(value=1)),
        ),
        (ValueKind.OBJECT, _schema()),
        (ValueKind.BYTES, DecodeOperator(view=BodyViewKind.JSON)),
        (
            ValueKind.MEDIA_TYPE,
            MediaTypeOperator(
                relation=MediaRelation.EXACT,
                expected=MediaType(type="application", subtype="json"),
            ),
        ),
        (
            ValueKind.HTTP_FIELD_ITEMS,
            TokenOperator(token=MediaTypeTokenValue(value="gzip")),
        ),
        (
            ValueKind.BYTES,
            DigestOperator(
                algorithm=DigestAlgorithm.SHA256,
                expected=_literal(BytesValue(value=b"digest")),
            ),
        ),
        (ValueKind.STRING, CardinalityOperator(exact=Count(value=1))),
    ],
)
def test_every_assertion_operator_has_a_static_input_contract(
    kind: ValueKind,
    operator: AssertionOperator,
) -> None:
    require_assertion_operator_contract(_contract(kind), operator)


@pytest.mark.parametrize(
    "operator",
    [
        OrderingOperator(
            relation=OrderingRelation.GREATER,
            expected=_literal(JsonInteger(value=0)),
        ),
        BetweenOperator(
            lower=_literal(JsonInteger(value=0)),
            upper=_literal(JsonInteger(value=2)),
            include_lower=True,
            include_upper=False,
        ),
        ApproximateOperator(
            expected=_literal(JsonInteger(value=1)),
            relative_tolerance=_literal(JsonInteger(value=0)),
        ),
        StringOperator(
            relation=StringRelation.STARTS_WITH,
            expected=_literal(JsonString(value="x")),
        ),
        _regex(),
        ContainsOperator(
            mode=ContainsMode.ONE,
            expected=(_literal(JsonString(value="x")),),
        ),
        KeySetOperator(relation=KeySetRelation.HAS, keys=("id",)),
        LengthOperator(relation=CountRelation.EQUALS, expected=Count(value=1)),
        DecodeOperator(view=BodyViewKind.JSON),
        MediaTypeOperator(
            relation=MediaRelation.EXACT,
            expected=MediaType(type="application", subtype="json"),
        ),
        TokenOperator(token=MediaTypeTokenValue(value="gzip")),
        DigestOperator(
            algorithm=DigestAlgorithm.SHA256,
            expected=_literal(BytesValue(value=b"digest")),
        ),
    ],
)
def test_known_incompatible_selector_types_are_rejected(operator: AssertionOperator) -> None:
    with pytest.raises(ValueError, match="cannot consume"):
        require_assertion_operator_contract(_contract(ValueKind.BOOLEAN), operator)


def test_static_operands_cannot_contradict_selector_or_operator_types() -> None:
    require_assertion_operator_contract(
        _contract(ValueKind.UNKNOWN),
        EqualityOperator(
            relation=EqualityRelation.EQUALS,
            expected=_literal(JsonString(value="dynamic")),
        ),
    )
    with pytest.raises(ValueError, match="cannot consume"):
        require_assertion_operator_contract(_contract(ValueKind.BYTES), _schema())
    with pytest.raises(ValueError, match="equality operand"):
        require_assertion_operator_contract(
            _contract(ValueKind.STRING),
            EqualityOperator(
                relation=EqualityRelation.EQUALS,
                expected=_literal(JsonInteger(value=1)),
            ),
        )
    with pytest.raises(ValueError, match="ordering operand"):
        require_assertion_operator_contract(
            _contract(ValueKind.INTEGER),
            OrderingOperator(
                relation=OrderingRelation.GREATER,
                expected=_literal(JsonString(value="one")),
            ),
        )
    with pytest.raises(ValueError, match="ordering operand contradicts"):
        require_assertion_operator_contract(
            _contract(ValueKind.INTEGER),
            OrderingOperator(
                relation=OrderingRelation.GREATER,
                expected=_converted(ConversionType.DURATION),
            ),
        )
    with pytest.raises(ValueError, match="between operand"):
        require_assertion_operator_contract(
            _contract(ValueKind.INTEGER),
            BetweenOperator(
                lower=_literal(JsonInteger(value=0)),
                upper=_literal(JsonString(value="two")),
                include_lower=True,
                include_upper=True,
            ),
        )
    with pytest.raises(ValueError, match="between operand contradicts"):
        require_assertion_operator_contract(
            _contract(ValueKind.INTEGER),
            BetweenOperator(
                lower=_literal(JsonInteger(value=0)),
                upper=_converted(ConversionType.DURATION),
                include_lower=True,
                include_upper=True,
            ),
        )
    with pytest.raises(ValueError, match="approximate operand"):
        require_assertion_operator_contract(
            _contract(ValueKind.NUMBER),
            ApproximateOperator(
                expected=_literal(JsonString(value="one")),
                absolute_tolerance=_literal(JsonInteger(value=0)),
            ),
        )
    with pytest.raises(ValueError, match="approximate tolerance"):
        require_assertion_operator_contract(
            _contract(ValueKind.NUMBER),
            ApproximateOperator(
                expected=_literal(JsonInteger(value=1)),
                absolute_tolerance=_literal(JsonString(value="near")),
            ),
        )
    with pytest.raises(ValueError, match="text operand"):
        require_assertion_operator_contract(
            _contract(ValueKind.STRING),
            StringOperator(
                relation=StringRelation.CONTAINS,
                expected=_literal(JsonInteger(value=1)),
            ),
        )
    with pytest.raises(ValueError, match="digest operand"):
        require_assertion_operator_contract(
            _contract(ValueKind.BYTES),
            DigestOperator(
                algorithm=DigestAlgorithm.SHA256,
                expected=_literal(JsonInteger(value=1)),
            ),
        )


def test_negative_tolerance_literals_are_rejected_without_guessing_dynamic_values() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        ApproximateOperator(
            expected=_literal(JsonInteger(value=1)),
            absolute_tolerance=_literal(JsonInteger(value=-1)),
        )
    with pytest.raises(TypeError, match="ValueContract"):
        require_assertion_operator_contract(
            cast("ValueContract", "bad"),
            TypeOperator(expected=ExactValueType.STRING),
        )
    with pytest.raises(TypeError, match="unsupported"):
        require_assertion_operator_contract(
            _contract(ValueKind.STRING),
            cast("AssertionOperator", "bad"),
        )
