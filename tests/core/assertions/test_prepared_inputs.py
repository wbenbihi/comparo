"""Prepared operand, predicate, and exact-coverage input contracts."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.assertions.inputs import PreparedCapabilityKind
from comparo.core.assertions.inputs import PreparedExpression
from comparo.core.assertions.inputs import PreparedOperandError
from comparo.core.assertions.inputs import PreparedOperandReady
from comparo.core.assertions.inputs import PreparedOperandUnavailable
from comparo.core.assertions.inputs import PreparedPredicate
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.assertions.inputs import _condition_expressions
from comparo.core.assertions.inputs import is_prepared_operand_result
from comparo.core.assertions.inputs import require_exact_expression_coverage
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from comparo.core.selection import select
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.assertions.test_predicates import _match
from tests.core.assertions.test_predicates import _predicate
from tests.core.domain.assertions.test_contracts import _literal
from tests.core.domain.assertions.test_contracts import _regex


def test_prepared_input_invariants_and_operand_results() -> None:
    code = DiagnosticCode(value="operand.missing")
    unavailable = PreparedOperandUnavailable(code=code)
    error = PreparedOperandError(code=code)
    ready = PreparedOperandReady(value=JsonInteger(value=1))
    assert is_prepared_operand_result(unavailable)
    assert is_prepared_operand_result(error)
    assert is_prepared_operand_result(ready)
    assert not is_prepared_operand_result(object())
    with pytest.raises(TypeError):
        PreparedOperandUnavailable(code=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError):
        PreparedOperandError(code=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError):
        PreparedOperandReady(value=cast("object", object()))
    expression = _literal(JsonInteger(value=1))
    with pytest.raises(TypeError):
        PreparedExpression(expression=cast("LiteralExpr", object()), result=ready)
    with pytest.raises(TypeError):
        PreparedExpression(expression=expression, result=cast("object", object()))


def test_prepared_collections_and_exact_coverage_invariants() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    prepared = inputs(compiled, prepared_snapshot)
    expression = prepared.expressions[0]
    with pytest.raises(TypeError):
        msgspec.structs.replace(prepared, rule_fingerprint=cast("object", "bad"))
    with pytest.raises(TypeError):
        msgspec.structs.replace(prepared, environment_execution_id=cast("object", "bad"))
    with pytest.raises(TypeError):
        msgspec.structs.replace(prepared, expressions=cast("tuple[PreparedExpression, ...]", []))
    with pytest.raises(TypeError):
        msgspec.structs.replace(prepared, predicates=cast("tuple[PreparedPredicate, ...]", []))
    with pytest.raises(ValueError, match="expressions must be unique"):
        msgspec.structs.replace(prepared, expressions=(expression, expression))
    with pytest.raises(ValueError, match="exactly cover"):
        require_exact_expression_coverage(
            compiled, msgspec.structs.replace(prepared, expressions=())
        )
    with pytest.raises(TypeError):
        require_exact_expression_coverage(cast("object", object()), prepared)

    selected = select(prepared_snapshot, compiled.selector, control().selection)
    assert type(selected.result) is SelectionReady
    predicate = _predicate(
        _regex(),
        selected.result.matches[0],
        PreparedCapabilityKind.REGEX,
    )
    with pytest.raises(ValueError, match="bindings must be unique"):
        msgspec.structs.replace(prepared, predicates=(predicate, predicate))
    with pytest.raises(ValueError, match="another execution"):
        msgspec.structs.replace(
            predicate,
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=2)),
        )
    with pytest.raises(ValueError, match="require a code"):
        msgspec.structs.replace(predicate, outcome=PreparedPredicateOutcome.ERROR)
    with pytest.raises(ValueError, match="require a code"):
        msgspec.structs.replace(
            predicate,
            outcome=PreparedPredicateOutcome.SATISFIED,
            code=DiagnosticCode(value="predicate.unexpected"),
        )


def test_remaining_prepared_input_type_guards() -> None:
    origin = (TaintOriginId(value="operand.count"),)
    assert (
        type(PreparedOperandReady(value=SensitiveValue(JsonInteger(value=1), origins=origin)).value)
        is SensitiveValue
    )
    with pytest.raises(TypeError, match="unsupported payload"):
        PreparedOperandReady(value=SensitiveValue(Count(value=1), origins=origin))
    match = _match(JsonInteger(value=200))
    base = _predicate(_regex(), match, PreparedCapabilityKind.REGEX)
    invalid_fields = (
        ("kind", "prepared predicate kind must be typed"),
        ("selector_fingerprint", "requires a selector Fingerprint"),
        ("operation_fingerprint", "requires an operation Fingerprint"),
        ("environment_execution_id", "requires an EnvironmentExecutionId"),
        ("location", "requires a concrete location"),
        ("outcome", "outcome must be typed"),
        ("code", "code must be a DiagnosticCode"),
    )
    for field, message in invalid_fields:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(base, **{field: object()})
    with pytest.raises(TypeError, match="unsupported applicability"):
        _condition_expressions(cast("object", object()))
