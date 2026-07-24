"""Secret literal expressions must never retain an open serializable payload."""

from collections.abc import Callable
from typing import cast

import msgspec
import pytest

from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import expression_dependencies
from comparo.core.domain.expressions import expression_output_kinds
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.primitives import Duration
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonString


def _provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def _sealed_literal() -> SensitiveLiteralExpr:
    return SensitiveLiteralExpr(
        value=SensitiveValue(
            JsonString(value="literal-secret-sentinel"),
            origins=(TaintOriginId(value="secret/literal"),),
        ),
        provenance=_provenance(),
    )


def test_sensitive_literal_is_a_typed_dependency_free_expression() -> None:
    expression = _sealed_literal()
    assert is_value_expression(expression)
    assert expression_dependencies(expression) == ()
    assert expression_output_kinds(expression) == frozenset({ValueKind.STRING})


@pytest.mark.parametrize(
    "serialize",
    [msgspec.json.encode, msgspec.msgpack.encode, msgspec.to_builtins],
)
def test_sensitive_literal_fails_closed_at_serialization(
    serialize: Callable[[object], object],
) -> None:
    expression = _sealed_literal()
    with pytest.raises(TypeError, match="unsupported") as caught:
        serialize(expression)
    assert "literal-secret-sentinel" not in str(caught.value)
    assert "literal-secret-sentinel" not in repr(expression)


def test_literal_variants_reject_the_wrong_containment_boundary() -> None:
    sealed = _sealed_literal().value
    with pytest.raises(TypeError, match="exact value"):
        LiteralExpr(
            value=cast("ExactValue", sealed),
            provenance=_provenance(),
        )
    with pytest.raises(TypeError, match="sealed exact value"):
        SensitiveLiteralExpr(
            value=cast(
                "SensitiveValue[ExactValue]",
                SensitiveValue(
                    Duration(nanoseconds=1),
                    origins=(TaintOriginId(value="wrong/type"),),
                ),
            ),
            provenance=_provenance(),
        )
