"""Cover exact diff operand discovery, frame binding, and terminal payload contracts."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.diffs import PreparedDiffInputs
from comparo.core.diffs import PreparedExpression
from comparo.core.diffs import PreparedOperandError
from comparo.core.diffs import PreparedOperandReady
from comparo.core.diffs import PreparedOperandUnavailable
from comparo.core.diffs.inputs import RequiredDiffExpressions
from comparo.core.diffs.inputs import _transform_expressions
from comparo.core.diffs.inputs import _unique
from comparo.core.diffs.inputs import is_prepared_operand_result
from comparo.core.diffs.inputs import prepared_result
from comparo.core.diffs.inputs import require_exact_expression_coverage
from comparo.core.diffs.inputs import required_expressions
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import CollectionFilter
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import ValueTypeTransform
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.diffs.strategies import EmptyInputMode
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import ItemEqualityPredicate
from comparo.core.domain.selectors.items import ItemPredicateIdentity
from comparo.core.domain.selectors.items import ItemPredicateKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolvedValue
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import rule
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _literal
from tests.core.domain.diffs._invariant_support import _operation

_CODE = DiagnosticCode(value="diff.prepared.test")


def _integer_literal(value: int) -> LiteralExpr:
    return msgspec.structs.replace(_literal(), value=JsonInteger(value=value))


def _filter(expression: LiteralExpr) -> CollectionFilter:
    predicate = CompiledItemPredicate(
        identity=ItemPredicateIdentity(
            namespace="comparo.core",
            kind=ItemPredicateKind.EQUALS,
            version="1",
        ),
        selector=_item_selector(output_kind=ValueKind.INTEGER),
        operator=ItemEqualityPredicate(expected=expression),
    )
    return CollectionFilter(
        identity=_operation(DiffOperationFamily.TRANSFORM, "collection-filter"),
        predicate=predicate,
    )


def _numeric_rule(
    baseline_expression: LiteralExpr,
    candidate_expression: LiteralExpr,
    absolute: LiteralExpr,
    relative: LiteralExpr,
) -> CompiledDiffRule:
    base = rule()
    baseline = msgspec.structs.replace(
        base.pipeline.baseline,
        transforms=(_filter(baseline_expression),),
    )
    candidate = msgspec.structs.replace(
        base.pipeline.candidate,
        transforms=(_filter(candidate_expression),),
    )
    comparator = NumericToleranceComparator(
        identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
        absolute_tolerance=absolute,
        relative_tolerance=relative,
    )
    return msgspec.structs.replace(
        base,
        pipeline=DiffPipeline(
            baseline=baseline,
            candidate=candidate,
            pairing=base.pipeline.pairing,
            comparator=comparator,
        ),
    )


def _separator_rule(separator: LiteralExpr) -> CompiledDiffRule:
    base = rule()
    transform = ValueTypeTransform(identity=_operation(DiffOperationFamily.TRANSFORM, "value-type"))
    reducer = Reducer(
        identity=_operation(DiffOperationFamily.REDUCER, "concatenate"),
        kind=ReducerKind.CONCATENATE,
        empty=EmptyInputMode.RETURN_EMPTY_TEXT,
        separator=separator,
    )
    side = SidePipeline(
        selector=base.pipeline.baseline.selector,
        transforms=(transform,),
        reducer=reducer,
    )
    return msgspec.structs.replace(
        base,
        pipeline=DiffPipeline(
            baseline=side,
            candidate=side,
            pairing=base.pipeline.pairing,
            comparator=base.pipeline.comparator,
        ),
    )


def test_prepared_operand_terminal_payloads_are_closed_and_sensitive_safe() -> None:
    public = PreparedOperandReady(value=JsonInteger(value=1))
    origin = TaintOriginId(value="diff-prepared-secret")
    sensitive = PreparedOperandReady(
        value=SensitiveValue(JsonString(value="secret"), origins=(origin,))
    )
    assert public.value == JsonInteger(value=1)
    assert type(sensitive.value) is SensitiveValue

    with pytest.raises(TypeError, match="unsupported payload"):
        PreparedOperandReady(value=cast("ResolvedValue", Count(value=1)))
    invalid_sensitive = SensitiveValue(Count(value=1), origins=(origin,))
    with pytest.raises(TypeError, match=r"sensitive.*unsupported payload"):
        PreparedOperandReady(value=cast("SensitiveValue[ResolvedValue]", invalid_sensitive))
    for terminal in (PreparedOperandUnavailable, PreparedOperandError):
        assert terminal(code=_CODE).code == _CODE
        with pytest.raises(TypeError, match="DiagnosticCode"):
            terminal(code=cast("DiagnosticCode", "bad"))


def test_prepared_expression_rejects_open_expression_and_result_variants() -> None:
    expression = _integer_literal(1)
    prepared = PreparedExpression(
        expression=expression,
        result=PreparedOperandReady(value=JsonInteger(value=1)),
    )
    assert prepared.expression == expression
    with pytest.raises(TypeError, match="ValueExpression"):
        msgspec.structs.replace(prepared, expression=cast("LiteralExpr", object()))
    with pytest.raises(TypeError, match="unsupported result"):
        msgspec.structs.replace(
            prepared,
            result=cast("PreparedOperandReady", object()),
        )
    with pytest.raises(ValueError, match="contradicts expression output"):
        PreparedExpression(
            expression=expression,
            result=PreparedOperandReady(value=JsonString(value="wrong-type")),
        )


def test_prepared_input_frames_reject_wrong_identity_collections_and_duplicates() -> None:
    valid = inputs(rule())
    replacements = (
        ("rule_fingerprint", object(), "Fingerprint"),
        ("comparison_id", object(), "ComparisonId"),
        ("baseline_execution_id", object(), "baseline frame"),
        ("candidate_execution_id", object(), "candidate frame"),
    )
    for field, value, message in replacements:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(valid, **{field: value})
    with pytest.raises(ValueError, match="distinct"):
        msgspec.structs.replace(
            valid,
            candidate_execution_id=valid.baseline_execution_id,
        )

    expression = _integer_literal(1)
    prepared = PreparedExpression(
        expression=expression,
        result=PreparedOperandReady(value=JsonInteger(value=1)),
    )
    with pytest.raises(TypeError, match="immutable typed tuples"):
        msgspec.structs.replace(valid, baseline_expressions=[prepared])
    with pytest.raises(TypeError, match="immutable typed tuples"):
        msgspec.structs.replace(valid, baseline_expressions=(object(),))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(valid, baseline_expressions=(prepared, prepared))


def test_required_expression_frames_are_closed_unique_tuples() -> None:
    expression = _integer_literal(1)
    assert RequiredDiffExpressions(baseline=(expression,)).baseline == (expression,)
    with pytest.raises(TypeError, match="immutable typed tuples"):
        RequiredDiffExpressions(baseline=cast("tuple[LiteralExpr, ...]", [expression]))
    with pytest.raises(TypeError, match="immutable typed tuples"):
        RequiredDiffExpressions(baseline=(cast("LiteralExpr", object()),))
    with pytest.raises(ValueError, match="unique"):
        RequiredDiffExpressions(baseline=(expression, expression))


def test_expression_discovery_preserves_side_and_shared_order_without_duplicates() -> None:
    baseline = _integer_literal(1)
    candidate = _integer_literal(2)
    absolute = _integer_literal(3)
    relative = _integer_literal(4)
    compiled = _numeric_rule(baseline, candidate, absolute, relative)
    required = required_expressions(compiled)
    assert required.baseline == (baseline,)
    assert required.candidate == (candidate,)
    assert required.shared == (absolute, relative)

    separator = _literal("|")
    separator_required = required_expressions(_separator_rule(separator))
    assert separator_required.baseline == (separator,)
    assert separator_required.candidate == (separator,)
    assert separator_required.shared == ()
    with pytest.raises(TypeError, match="CompiledDiffRule"):
        required_expressions(cast("CompiledDiffRule", object()))


def test_exact_expression_coverage_requires_rule_fingerprint_and_stable_frames() -> None:
    compiled = rule()
    prepared = inputs(compiled)
    require_exact_expression_coverage(compiled, prepared)
    with pytest.raises(TypeError, match="PreparedDiffInputs"):
        require_exact_expression_coverage(
            compiled,
            cast("PreparedDiffInputs", object()),
        )
    foreign = msgspec.structs.replace(
        prepared,
        rule_fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="f" * 64),
        ),
    )
    with pytest.raises(ValueError, match="different compiled rule"):
        require_exact_expression_coverage(compiled, foreign)

    extra_expression = _integer_literal(1)
    extra = msgspec.structs.replace(
        prepared,
        baseline_expressions=(
            PreparedExpression(
                expression=extra_expression,
                result=PreparedOperandReady(value=JsonInteger(value=1)),
            ),
        ),
    )
    with pytest.raises(ValueError, match="exactly cover"):
        require_exact_expression_coverage(compiled, extra)


def test_prepared_result_uses_exact_authored_frame() -> None:
    expression = _integer_literal(1)
    baseline = PreparedOperandReady(value=JsonInteger(value=1))
    candidate = PreparedOperandUnavailable(code=_CODE)
    shared = PreparedOperandError(code=_CODE)

    def item(
        result: PreparedOperandReady | PreparedOperandUnavailable | PreparedOperandError,
    ) -> PreparedExpression:
        return PreparedExpression(expression=expression, result=result)

    base = inputs(rule())
    prepared = msgspec.structs.replace(
        base,
        baseline_expressions=(item(baseline),),
        candidate_expressions=(item(candidate),),
        shared_expressions=(item(shared),),
    )
    assert prepared_result(prepared, expression, DiffSide.BASELINE) is baseline
    assert prepared_result(prepared, expression, DiffSide.CANDIDATE) is candidate
    assert prepared_result(prepared, expression, DiffSide.BOTH) is shared


def test_input_helpers_close_known_terminal_transform_and_unique_variants() -> None:
    expression = _integer_literal(1)
    transform = _filter(expression)
    assert _transform_expressions(transform) == (expression,)
    type_predicate = msgspec.structs.replace(transform.predicate)
    msgspec.structs.force_setattr(
        type_predicate,
        "operator",
        object(),
    )
    assert (
        _transform_expressions(msgspec.structs.replace(transform, predicate=type_predicate)) == ()
    )
    assert (
        _transform_expressions(
            ValueTypeTransform(identity=_operation(DiffOperationFamily.TRANSFORM, "value-type"))
        )
        == ()
    )
    assert _unique([expression, expression, _integer_literal(2)]) == (
        expression,
        _integer_literal(2),
    )
    for value in (
        PreparedOperandReady(value=JsonInteger(value=1)),
        PreparedOperandUnavailable(code=_CODE),
        PreparedOperandError(code=_CODE),
    ):
        assert is_prepared_operand_result(value)
    assert not is_prepared_operand_result(object())


def test_prepared_inputs_accept_distinct_typed_execution_frames() -> None:
    prepared = inputs(rule())
    assert prepared.baseline_execution_id == EnvironmentExecutionId(
        value=prepared.baseline_execution_id.value
    )
    assert prepared.baseline_execution_id != prepared.candidate_execution_id
