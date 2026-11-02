"""Closed prepared operands consumed by the pure diff evaluator."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import CollectionFilter
from comparo.core.domain.diffs.operations import DiffTransform
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import expression_output_kinds
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import contract_accepts_runtime_kind
from comparo.core.domain.selectors.items import ItemEqualityPredicate
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.values import selected_value_kind_type
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import is_resolved_value
from comparo.core.domain.values import is_resolved_value_type


@final
class PreparedOperandReady(msgspec.Struct, frozen=True, kw_only=True):
    """An already-resolved operand, sealed when it is sensitive."""

    value: ResolvedValue | SensitiveValue[ResolvedValue]

    def __post_init__(self) -> None:
        """Require the closed resolved-value vocabulary."""
        if is_sensitive_value(self.value):
            if not is_resolved_value_type(self.value.payload_type):
                raise TypeError("prepared sensitive diff operand has an unsupported payload")
        elif not is_resolved_value(self.value):
            raise TypeError("prepared diff operand has an unsupported payload")


@final
class PreparedOperandUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """A required operand was unavailable before pure evaluation."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a safe diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("prepared diff operand unavailability requires a DiagnosticCode")


@final
class PreparedOperandError(msgspec.Struct, frozen=True, kw_only=True):
    """A required operand failed resolution before pure evaluation."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a safe diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("prepared diff operand error requires a DiagnosticCode")


type PreparedOperandResult = (
    PreparedOperandReady | PreparedOperandUnavailable | PreparedOperandError
)


@final
class PreparedExpression(msgspec.Struct, frozen=True, kw_only=True):
    """One exact compiled expression and its prepared terminal result."""

    expression: ValueExpression
    result: PreparedOperandResult

    def __post_init__(self) -> None:
        """Require an exact expression and closed prepared result."""
        if not is_value_expression(self.expression):
            raise TypeError("prepared diff expression requires a ValueExpression")
        if not is_prepared_operand_result(self.result):
            raise TypeError("prepared diff expression has an unsupported result")
        if type(self.result) is PreparedOperandReady:
            actual = (
                selected_value_kind_type(self.result.value.payload_type)
                if is_sensitive_value(self.result.value)
                else selected_value_kind_type(type(self.result.value))
            )
            expected = expression_output_kinds(self.expression)
            contract = ValueContract(
                kinds=tuple(sorted(expected, key=lambda kind: kind.value)),
                cardinality=SelectorCardinality.EXACTLY_ONE,
            )
            if actual is None or not contract_accepts_runtime_kind(contract, actual):
                raise ValueError("prepared diff operand contradicts expression output contract")


@final
class PreparedDiffInputs(msgspec.Struct, frozen=True, kw_only=True):
    """Exact side-scoped operands bound to one compiled diff fingerprint.

    An expression may resolve differently in the baseline and candidate execution frames.
    Keeping the scopes separate prevents equality of authored expressions from collapsing
    distinct prepared values.
    """

    rule_fingerprint: Fingerprint
    comparison_id: ComparisonId
    baseline_execution_id: EnvironmentExecutionId
    candidate_execution_id: EnvironmentExecutionId
    baseline_expressions: tuple[PreparedExpression, ...] = ()
    candidate_expressions: tuple[PreparedExpression, ...] = ()
    shared_expressions: tuple[PreparedExpression, ...] = ()

    def __post_init__(self) -> None:
        """Bind a unique ordered operand set to one rule fingerprint."""
        if type(self.rule_fingerprint) is not Fingerprint:
            raise TypeError("prepared diff inputs require a rule Fingerprint")
        if type(self.comparison_id) is not ComparisonId:
            raise TypeError("prepared diff inputs require a ComparisonId")
        if type(self.baseline_execution_id) is not EnvironmentExecutionId:
            raise TypeError("prepared baseline frame requires an EnvironmentExecutionId")
        if type(self.candidate_execution_id) is not EnvironmentExecutionId:
            raise TypeError("prepared candidate frame requires an EnvironmentExecutionId")
        if self.baseline_execution_id == self.candidate_execution_id:
            raise ValueError("prepared diff side frames must be distinct")
        for expressions in (
            self.baseline_expressions,
            self.candidate_expressions,
            self.shared_expressions,
        ):
            if type(expressions) is not tuple or any(
                type(item) is not PreparedExpression for item in expressions
            ):
                raise TypeError("prepared diff expressions must be immutable typed tuples")
            values = tuple(item.expression for item in expressions)
            if any(value in values[:index] for index, value in enumerate(values)):
                raise ValueError("prepared diff expressions must be unique within each scope")


@final
class RequiredDiffExpressions(msgspec.Struct, frozen=True, kw_only=True):
    """Stable expression addresses split by their evaluation frame."""

    baseline: tuple[ValueExpression, ...] = ()
    candidate: tuple[ValueExpression, ...] = ()
    shared: tuple[ValueExpression, ...] = ()

    def __post_init__(self) -> None:
        """Require a unique exact sequence within every frame."""
        for values in (self.baseline, self.candidate, self.shared):
            if type(values) is not tuple or any(not is_value_expression(value) for value in values):
                raise TypeError("required diff expressions must be immutable typed tuples")
            if any(value in values[:index] for index, value in enumerate(values)):
                raise ValueError("required diff expressions must be unique within each scope")


def required_expressions(rule: CompiledDiffRule) -> RequiredDiffExpressions:
    """Collect every runtime operand in stable pipeline order."""
    if type(rule) is not CompiledDiffRule:
        raise TypeError("diff expression discovery requires a CompiledDiffRule")
    side_values: list[tuple[ValueExpression, ...]] = []
    for side in (rule.pipeline.baseline, rule.pipeline.candidate):
        found: list[ValueExpression] = []
        for transform in side.transforms:
            found.extend(_transform_expressions(transform))
        if type(side.reducer) is Reducer and side.reducer.separator is not None:
            found.append(side.reducer.separator)
        side_values.append(_unique(found))
    shared: list[ValueExpression] = []
    comparator = rule.pipeline.comparator
    if type(comparator) is NumericToleranceComparator:
        shared.extend(
            value
            for value in (
                comparator.absolute_tolerance,
                comparator.relative_tolerance,
            )
            if value is not None
        )
    return RequiredDiffExpressions(
        baseline=side_values[0],
        candidate=side_values[1],
        shared=_unique(shared),
    )


def require_exact_expression_coverage(
    rule: CompiledDiffRule,
    inputs: PreparedDiffInputs,
) -> None:
    """Reject operands belonging to another rule or an incomplete prepared snapshot."""
    if type(inputs) is not PreparedDiffInputs:
        raise TypeError("diff evaluation requires PreparedDiffInputs")
    if inputs.rule_fingerprint != rule.fingerprint:
        raise ValueError("prepared diff inputs belong to a different compiled rule")
    required = required_expressions(rule)
    supplied = RequiredDiffExpressions(
        baseline=tuple(item.expression for item in inputs.baseline_expressions),
        candidate=tuple(item.expression for item in inputs.candidate_expressions),
        shared=tuple(item.expression for item in inputs.shared_expressions),
    )
    if supplied != required:
        raise ValueError("prepared diff expressions must exactly cover rule expressions")


def prepared_result(
    inputs: PreparedDiffInputs,
    expression: ValueExpression,
    side: DiffSide,
) -> PreparedOperandResult:
    """Return the exact prepared result from the authored evaluation frame."""
    field = {
        DiffSide.BASELINE: inputs.baseline_expressions,
        DiffSide.CANDIDATE: inputs.candidate_expressions,
        DiffSide.BOTH: inputs.shared_expressions,
    }[side]
    return next(item.result for item in field if item.expression == expression)


def is_prepared_operand_result(value: object) -> TypeGuard[PreparedOperandResult]:
    return type(value) in {
        PreparedOperandReady,
        PreparedOperandUnavailable,
        PreparedOperandError,
    }


def _transform_expressions(transform: DiffTransform) -> tuple[ValueExpression, ...]:
    if type(transform) is CollectionFilter:
        operator = transform.predicate.operator
        return (operator.expected,) if type(operator) is ItemEqualityPredicate else ()
    return ()


def _unique(values: list[ValueExpression]) -> tuple[ValueExpression, ...]:
    return tuple(value for index, value in enumerate(values) if value not in values[:index])


__all__ = (
    "PreparedDiffInputs",
    "PreparedExpression",
    "PreparedOperandError",
    "PreparedOperandReady",
    "PreparedOperandUnavailable",
    "RequiredDiffExpressions",
    "prepared_result",
    "require_exact_expression_coverage",
    "required_expressions",
)
