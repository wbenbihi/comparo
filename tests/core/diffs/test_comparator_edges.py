"""Cover comparator terminal states and recursive compatibility laws."""

from __future__ import annotations

from decimal import ROUND_FLOOR
from decimal import Decimal
from decimal import Inexact
from decimal import Rounded
from decimal import localcontext
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.diffs import PreparedDiffInputs
from comparo.core.diffs import PreparedExpression
from comparo.core.diffs import PreparedOperandError
from comparo.core.diffs import PreparedOperandReady
from comparo.core.diffs import PreparedOperandUnavailable
from comparo.core.diffs.comparators import _compare
from comparo.core.diffs.comparators import _optional_number
from comparo.core.diffs.comparators import _require_depth
from comparo.core.diffs.comparators import _shape_equal
from comparo.core.diffs.comparators import compare_unit
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import PairUnit
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.strategies import CollectionComparator
from comparo.core.domain.diffs.strategies import CollectionComparisonKind
from comparo.core.domain.diffs.strategies import CompatibilityComparator
from comparo.core.domain.diffs.strategies import CompatibilityKind
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.diffs.strategies import TextPredicateComparator
from comparo.core.domain.diffs.strategies import TextPredicateRelation
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.diffs.factories import derived
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import meter
from tests.core.diffs.factories import rule
from tests.core.domain.diffs._invariant_support import _literal
from tests.core.domain.diffs._invariant_support import _operation


def _identity(name: str):  # type: ignore[no-untyped-def]
    return _operation(DiffOperationFamily.COMPARATOR, name)


def _object(**values: int) -> JsonObject:
    return JsonObject(
        members=tuple(
            JsonObjectMember(name=name, value=JsonInteger(value=value))
            for name, value in values.items()
        )
    )


def _prepared(expression: LiteralExpr, result: object) -> PreparedDiffInputs:
    prepared = PreparedExpression(
        expression=expression,
        result=PreparedOperandReady(value=JsonInteger(value=1)),
    )
    msgspec.structs.force_setattr(prepared, "result", result)
    return PreparedDiffInputs(
        rule_fingerprint=rule().fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        shared_expressions=(prepared,),
    )


def test_pair_diagnostics_and_runtime_types_map_to_incomparable() -> None:
    code = DiagnosticCode(value="diff.pairing.incomparable")
    exact = ExactComparator(identity=_identity("exact"))
    outcome, diagnostic = compare_unit(
        PairUnit(baseline=derived(JsonInteger(value=1)), incomparable=code),
        exact,
        inputs(rule()),
        meter(),
    )
    assert (outcome, diagnostic) == (DiffFactOutcome.INCOMPARABLE, code)

    text = TextPredicateComparator(
        identity=_identity("text-predicate"),
        relation=TextPredicateRelation.CANDIDATE_CONTAINS_BASELINE,
    )
    outcome, diagnostic = compare_unit(
        PairUnit(
            baseline=derived(JsonInteger(value=1)),
            candidate=derived(JsonInteger(value=2), candidate=True),
        ),
        text,
        inputs(rule()),
        meter(),
    )
    assert outcome is DiffFactOutcome.INCOMPARABLE
    assert diagnostic is not None
    assert diagnostic.value == "diff.comparator.runtime-type"


def test_exact_and_unsupported_comparator_dispatch() -> None:
    exact = ExactComparator(identity=_identity("exact"))
    assert _compare(JsonInteger(value=1), JsonInteger(value=1), exact, inputs(rule()), meter())
    with pytest.raises(TypeError, match="unsupported"):
        _compare(
            JsonInteger(value=1),
            JsonInteger(value=1),
            cast("ExactComparator", object()),
            inputs(rule()),
            meter(),
        )


def test_numeric_tolerance_supports_absolute_only_and_relative_only() -> None:
    expression = LiteralExpr(value=JsonInteger(value=2), provenance=_literal().provenance)
    absolute = NumericToleranceComparator(
        identity=_identity("numeric-tolerance"),
        absolute_tolerance=expression,
    )
    prepared = _prepared(expression, PreparedOperandReady(value=JsonInteger(value=2)))
    assert _compare(JsonInteger(value=10), JsonInteger(value=12), absolute, prepared, meter())

    relative_expression = LiteralExpr(
        value=JsonDecimal(value=Decimal("0.1")),
        provenance=_literal().provenance,
    )
    relative = NumericToleranceComparator(
        identity=_identity("numeric-tolerance"),
        relative_tolerance=relative_expression,
    )
    prepared = _prepared(
        relative_expression,
        PreparedOperandReady(value=JsonDecimal(value=Decimal("0.1"))),
    )
    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_FLOOR
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert _compare(JsonInteger(value=100), JsonInteger(value=109), relative, prepared, meter())


def test_compatibility_type_and_non_json_rejection() -> None:
    typed = CompatibilityComparator(identity=_identity("type"), kind=CompatibilityKind.TYPE)
    assert _compare(JsonInteger(value=1), JsonInteger(value=2), typed, inputs(rule()), meter())
    assert not _compare(JsonInteger(value=1), JsonString(value="1"), typed, inputs(rule()), meter())

    shaped = CompatibilityComparator(
        identity=_identity("json-shape"),
        kind=CompatibilityKind.JSON_SHAPE,
    )
    assert _compare(_object(a=1), _object(a=2), shaped, inputs(rule()), meter())
    with pytest.raises(TypeError, match="JSON compatibility"):
        _compare(
            cast("JsonString", derived(JsonInteger(value=1)).origins[0]),
            JsonInteger(value=1),
            shaped,
            inputs(rule()),
            meter(),
        )


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        (JsonInteger(value=1), JsonString(value="1"), False),
        (_object(a=1), _object(a=1, b=2), False),
        (_object(a=1), _object(b=1), False),
        (
            JsonArray(items=(JsonInteger(value=1),)),
            JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))),
            False,
        ),
        (
            JsonArray(items=(JsonInteger(value=1),)),
            JsonArray(items=(JsonInteger(value=2),)),
            True,
        ),
        (_object(a=1), _object(a=2), True),
    ],
)
def test_json_shape_compares_structure_not_scalar_content(
    left: object,
    right: object,
    same: bool,
) -> None:
    assert (
        _shape_equal(
            cast("JsonObject | JsonArray | JsonInteger", left),
            cast("JsonObject | JsonArray | JsonInteger", right),
            meter(),
        )
        is same
    )


@pytest.mark.parametrize(
    ("kind", "left", "right", "same"),
    [
        (CompatibilityKind.SUBSET, _object(a=1), _object(a=1, b=2), True),
        (CompatibilityKind.SUBSET, _object(a=1), _object(b=1), False),
        (
            CompatibilityKind.SUBSET,
            JsonArray(items=(JsonInteger(value=1),)),
            JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))),
            True,
        ),
        (
            CompatibilityKind.SUBSET,
            JsonArray(items=(JsonInteger(value=3),)),
            JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))),
            False,
        ),
        (CompatibilityKind.SUBSET, JsonInteger(value=1), JsonInteger(value=2), False),
        (CompatibilityKind.SUBSET, JsonInteger(value=1), JsonInteger(value=1), True),
        (CompatibilityKind.SUPERSET, _object(a=1, b=2), _object(a=1), True),
    ],
)
def test_json_subset_and_superset_cover_container_and_scalar_paths(
    kind: CompatibilityKind,
    left: object,
    right: object,
    same: bool,
) -> None:
    comparator = CompatibilityComparator(identity=_identity(kind.value), kind=kind)
    assert (
        _compare(
            cast("JsonObject | JsonArray | JsonInteger", left),
            cast("JsonObject | JsonArray | JsonInteger", right),
            comparator,
            inputs(rule()),
            meter(),
        )
        is same
    )


def test_collection_comparator_rejects_non_arrays_and_empty_sets_compare() -> None:
    comparator = CollectionComparator(
        identity=_identity("set"),
        kind=CollectionComparisonKind.SET,
    )
    with pytest.raises(TypeError, match="requires arrays"):
        _compare(
            JsonInteger(value=1),
            JsonInteger(value=1),
            comparator,
            inputs(rule()),
            meter(),
        )
    assert _compare(JsonArray(items=()), JsonArray(items=()), comparator, inputs(rule()), meter())


def test_depth_guard_and_tolerance_terminal_results_are_exact() -> None:
    _require_depth(256)
    with pytest.raises(DiffHaltError) as caught:
        _require_depth(257)
    assert caught.value.code is not None

    expression = LiteralExpr(value=JsonInteger(value=1), provenance=_literal().provenance)
    assert (
        _optional_number(
            _prepared(expression, PreparedOperandReady(value=JsonInteger(value=1))),
            None,
            meter(),
        )
        is None
    )
    terminals = (
        PreparedOperandUnavailable(code=DiagnosticCode(value="diff.tolerance.missing")),
        PreparedOperandError(code=DiagnosticCode(value="diff.tolerance.error")),
        PreparedOperandReady(value=JsonString(value="bad")),
        PreparedOperandReady(value=JsonInteger(value=-1)),
    )
    for result in terminals:
        with pytest.raises(DiffHaltError):
            _optional_number(_prepared(expression, result), expression, meter())

    with pytest.raises(TypeError, match="unsupported prepared"):
        _optional_number(_prepared(expression, object()), expression, meter())
