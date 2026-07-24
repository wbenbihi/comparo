"""Cover every assertion operator and direct-construction outcome invariant."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

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
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
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
from comparo.core.domain.assertions.operators import is_assertion_operator
from comparo.core.domain.assertions.operators import operator_kind
from comparo.core.domain.assertions.operators import require_operator
from comparo.core.domain.assertions.regex import RegexMode
from comparo.core.domain.assertions.rules import AllConditions
from comparo.core.domain.assertions.rules import AnyConditions
from comparo.core.domain.assertions.rules import PredicateCondition
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import is_applicability_condition
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.sources import BodyViewKind
from tests.core.domain.assertions._invariant_support import _fingerprint
from tests.core.domain.assertions._invariant_support import _literal
from tests.core.domain.assertions._invariant_support import _operators
from tests.core.domain.assertions._invariant_support import _regex
from tests.core.domain.assertions._invariant_support import _selector


def test_every_operator_has_one_matching_versioned_identity() -> None:
    seen: set[AssertionOperatorKind] = set()
    for operator in _operators():
        kind = operator_kind(operator)
        identity = AssertionOperatorIdentity(namespace="comparo.core", kind=kind, version="1")
        require_operator(identity, operator)
        assert is_assertion_operator(operator)
        seen.add(kind)

    assert seen == set(AssertionOperatorKind)
    assert not is_assertion_operator("equals")


def test_operator_identity_and_configuration_reject_invalid_direct_values() -> None:
    with pytest.raises(ValueError, match="namespace"):
        AssertionOperatorIdentity(
            namespace="Invalid Namespace",
            kind=AssertionOperatorKind.EQUALITY,
            version="1",
        )
    with pytest.raises(TypeError):
        AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=cast("AssertionOperatorKind", "equals"),
            version="1",
        )
    with pytest.raises(ValueError, match="positive"):
        AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.EQUALITY,
            version="0",
        )
    with pytest.raises(ValueError, match="decimal"):
        AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.EQUALITY,
            version="one",
        )
    with pytest.raises(ValueError, match="tolerance"):
        ApproximateOperator(expected=_literal())
    with pytest.raises(ValueError, match="exactly one"):
        ContainsOperator(mode=ContainsMode.ONE, expected=(_literal(), _literal(2)))
    with pytest.raises(ValueError, match="unique"):
        KeySetOperator(relation=KeySetRelation.HAS, keys=("id", "id"))
    with pytest.raises(ValueError, match="non-empty"):
        _regex(pattern="")


def test_operator_fields_reject_untyped_direct_construction() -> None:
    with pytest.raises(TypeError, match="equality relation"):
        EqualityOperator(
            relation=cast("EqualityRelation", "equals"),
            expected=_literal(),
        )
    with pytest.raises(TypeError, match="operand"):
        EqualityOperator(
            relation=EqualityRelation.EQUALS,
            expected=cast("LiteralExpr", "bad"),
        )
    with pytest.raises(TypeError, match="membership relation"):
        MembershipOperator(
            relation=cast("MembershipRelation", "in"),
            choices=(_literal(),),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        MembershipOperator(
            relation=MembershipRelation.IN,
            choices=cast("tuple[LiteralExpr, ...]", [_literal()]),
        )
    with pytest.raises(ValueError, match="not be empty"):
        MembershipOperator(relation=MembershipRelation.IN, choices=())
    with pytest.raises(TypeError, match="ValueExpression"):
        MembershipOperator(
            relation=MembershipRelation.IN,
            choices=(cast("LiteralExpr", "bad"),),
        )
    with pytest.raises(TypeError, match="ExactValueType"):
        TypeOperator(expected=cast("ExactValueType", "integer"))
    with pytest.raises(TypeError, match="ordering relation"):
        OrderingOperator(
            relation=cast("OrderingRelation", "greater"),
            expected=_literal(),
        )
    with pytest.raises(TypeError, match="inclusion"):
        BetweenOperator(
            lower=_literal(),
            upper=_literal(),
            include_lower=cast("bool", 1),
            include_upper=False,
        )
    with pytest.raises(TypeError, match="string relation"):
        StringOperator(
            relation=cast("StringRelation", "contains"),
            expected=_literal(),
        )
    with pytest.raises(TypeError, match="RegexMode"):
        msgspec.structs.replace(_regex(), mode=cast("RegexMode", "search"))
    with pytest.raises(TypeError, match="negate"):
        msgspec.structs.replace(_regex(), negate=cast("bool", 0))
    with pytest.raises(TypeError, match="ContainsMode"):
        ContainsOperator(mode=cast("ContainsMode", "one"), expected=(_literal(),))
    with pytest.raises(TypeError, match="KeySetRelation"):
        KeySetOperator(relation=cast("KeySetRelation", "has"), keys=("id",))
    with pytest.raises(ValueError, match="immutable tuple"):
        KeySetOperator(relation=KeySetRelation.HAS, keys=cast("tuple[str, ...]", ["id"]))
    with pytest.raises(TypeError, match="text"):
        KeySetOperator(relation=KeySetRelation.HAS, keys=(cast("str", 1),))
    with pytest.raises(TypeError, match="CountRelation"):
        LengthOperator(
            relation=cast("CountRelation", "equals"),
            expected=Count(value=1),
        )


def test_extended_operator_fields_reject_untyped_direct_construction() -> None:
    remote = CompiledSchema(fingerprint=_fingerprint(), remote_uri="https://example.test/schema")
    with pytest.raises(TypeError, match="fingerprint"):
        CompiledSchema(
            fingerprint=cast("Fingerprint", "bad"),
            remote_uri="https://example.test/schema",
        )
    with pytest.raises(ValueError, match="Schema resource"):
        CompiledSchema(
            fingerprint=_fingerprint(),
            resource=DefinitionRef(
                identity=ResourceIdentity(
                    namespace=NamespaceId(value="demo"),
                    kind=ResourceKind.REQUEST,
                    resource_id=ResourceId(value="wrong"),
                ),
                fingerprint=_fingerprint(),
            ),
        )
    with pytest.raises(ValueError, match="URI"):
        CompiledSchema(fingerprint=_fingerprint(), remote_uri="")
    with pytest.raises(TypeError, match="CompiledSchema"):
        SchemaOperator(
            schema=cast("CompiledSchema", "bad"),
            default_dialect="2020-12",
        )
    with pytest.raises(ValueError, match="dialect"):
        SchemaOperator(schema=remote, default_dialect="")
    with pytest.raises(TypeError, match="format"):
        SchemaOperator(schema=remote, default_dialect="2020-12", assert_formats=cast("bool", 1))
    with pytest.raises(TypeError, match="BodyViewKind"):
        DecodeOperator(view=cast("BodyViewKind", "json"))
    with pytest.raises(TypeError, match="MediaRelation"):
        MediaTypeOperator(
            relation=cast("MediaRelation", "exact"),
            expected=MediaType(type="text", subtype="plain"),
        )
    with pytest.raises(TypeError, match="MediaType"):
        MediaTypeOperator(relation=MediaRelation.EXACT, expected=cast("MediaType", "json"))
    with pytest.raises(TypeError, match="MediaTypeTokenValue"):
        TokenOperator(token=cast("MediaTypeTokenValue", "gzip value"))
    with pytest.raises(TypeError, match="DigestAlgorithm"):
        DigestOperator(algorithm=cast("DigestAlgorithm", "sha256"), expected=_literal())
    with pytest.raises(TypeError, match="ValueExpression"):
        DigestOperator(
            algorithm=DigestAlgorithm.SHA256,
            expected=cast("LiteralExpr", "bad"),
        )
    with pytest.raises(TypeError, match="Count"):
        CardinalityOperator(exact=cast("Count", 1))


def test_operator_helpers_reject_open_union_values() -> None:
    with pytest.raises(TypeError, match="unsupported"):
        operator_kind(cast("AssertionOperator", "equals"))
    with pytest.raises(TypeError, match="identity"):
        require_operator(
            cast("AssertionOperatorIdentity", "bad"),
            EqualityOperator(relation=EqualityRelation.EQUALS, expected=_literal()),
        )
    with pytest.raises(TypeError, match="unsupported"):
        require_operator(
            AssertionOperatorIdentity(
                namespace="comparo.core",
                kind=AssertionOperatorKind.EQUALITY,
                version="1",
            ),
            cast("AssertionOperator", "bad"),
        )


def test_schema_and_cardinality_sources_are_unambiguous() -> None:
    local = DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="demo"),
            kind=ResourceKind.SCHEMA,
            resource_id=ResourceId(value="response"),
        ),
        fingerprint=_fingerprint(),
    )
    assert CompiledSchema(fingerprint=_fingerprint(), resource=local).resource == local
    with pytest.raises(ValueError, match="exactly one"):
        CompiledSchema(fingerprint=_fingerprint())
    with pytest.raises(ValueError, match="range"):
        CardinalityOperator(exact=Count(value=1), minimum=Count(value=1))
    with pytest.raises(ValueError, match="requires"):
        CardinalityOperator()
    with pytest.raises(ValueError, match="exceed"):
        CardinalityOperator(minimum=Count(value=2), maximum=Count(value=1))


def test_structured_applicability_has_no_general_expression_escape_hatch() -> None:
    operator = EqualityOperator(relation=EqualityRelation.EQUALS, expected=_literal())
    condition = PredicateCondition(
        selector=_selector(),
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.EQUALITY,
            version="1",
        ),
        operator=operator,
        quantifier=Quantifier.ONE,
    )
    conjunction = AllConditions(conditions=(condition,))
    disjunction = AnyConditions(conditions=(conjunction,))

    assert is_applicability_condition(disjunction)
    assert not is_applicability_condition(True)
    with pytest.raises(ValueError, match="empty"):
        AllConditions(conditions=())
    with pytest.raises(TypeError, match="immutable tuple"):
        AnyConditions(conditions=cast("tuple[PredicateCondition, ...]", [condition]))
    with pytest.raises(TypeError, match="unsupported"):
        AllConditions(conditions=(cast("PredicateCondition", "bad"),))
    with pytest.raises(TypeError, match="selector"):
        PredicateCondition(
            selector=cast("CompiledSelector", "bad"),
            operator_id=condition.operator_id,
            operator=operator,
            quantifier=Quantifier.ONE,
        )
    with pytest.raises(TypeError, match="quantifier"):
        PredicateCondition(
            selector=_selector(),
            operator_id=condition.operator_id,
            operator=operator,
            quantifier=cast("Quantifier", "one"),
        )
