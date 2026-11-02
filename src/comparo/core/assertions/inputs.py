"""Immutable prepared operands and pure capability outcomes for assertions."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.assertions.bindings import capability_implementation
from comparo.core.assertions.bindings import operation_fingerprint
from comparo.core.domain.assertions.evidence import DigestEvidence
from comparo.core.domain.assertions.evidence import SchemaValidationEvidence
from comparo.core.domain.assertions.extended_operators import DigestOperator
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.operators import BetweenOperator
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import MembershipOperator
from comparo.core.domain.assertions.operators import OrderingOperator
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.assertions.rules import AllConditions
from comparo.core.domain.assertions.rules import AnyConditions
from comparo.core.domain.assertions.rules import ApplicabilityCondition
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import PredicateCondition
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import expression_output_kinds
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.model import require_location_matches_selector
from comparo.core.domain.selectors.values import selected_value_kind_type
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import is_resolved_value
from comparo.core.domain.values import is_resolved_value_type

_EVIDENCE_ACCESS = SensitiveValueAccess(purpose="assertion.input.validate")
type PreparedOperatorEvidence = (
    SchemaValidationEvidence
    | DigestEvidence
    | SensitiveValue[SchemaValidationEvidence | DigestEvidence]
)


@final
class PreparedOperandReady(msgspec.Struct, frozen=True, kw_only=True):
    """One already-resolved live operand, possibly sealed as sensitive."""

    value: ResolvedValue | SensitiveValue[ResolvedValue]

    def __post_init__(self) -> None:
        """Require the closed resolution value domain."""
        if is_sensitive_value(self.value):
            if not is_resolved_value_type(self.value.payload_type):
                raise TypeError("prepared sensitive operand has an unsupported payload")
        elif not is_resolved_value(self.value):
            raise TypeError("prepared operand has an unsupported value")


@final
class PreparedOperandUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """A required expression was absent after scoped resolution."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a safe value-free code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("prepared operand unavailability requires a DiagnosticCode")


@final
class PreparedOperandError(msgspec.Struct, frozen=True, kw_only=True):
    """A required expression failed resolution or runtime conversion."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a safe value-free code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("prepared operand error requires a DiagnosticCode")


type PreparedOperandResult = (
    PreparedOperandReady | PreparedOperandUnavailable | PreparedOperandError
)


@final
class PreparedExpression(msgspec.Struct, frozen=True, kw_only=True):
    """One expression paired with its exact pre-resolved outcome."""

    expression: ValueExpression
    result: PreparedOperandResult

    def __post_init__(self) -> None:
        """Reject uncompiled expressions and open result variants."""
        if not is_value_expression(self.expression):
            raise TypeError("prepared expression requires a ValueExpression")
        if type(self.result) not in {
            PreparedOperandReady,
            PreparedOperandUnavailable,
            PreparedOperandError,
        }:
            raise TypeError("prepared expression has an unsupported result")
        if type(self.result) is PreparedOperandReady:
            _require_expression_result_kind(self.expression, self.result)


def _require_expression_result_kind(
    expression: ValueExpression,
    result: PreparedOperandReady,
) -> None:
    """Reject prepared values contradicting the expression's canonical static output."""
    payload_type = (
        result.value.payload_type if is_sensitive_value(result.value) else type(result.value)
    )
    actual = selected_value_kind_type(payload_type)
    if actual is None:  # pragma: no cover - PreparedOperandReady invariant
        raise TypeError("prepared expression result has no canonical runtime kind")
    expected = expression_output_kinds(expression)
    if ValueKind.UNKNOWN in expected:
        return
    compatible = actual in expected or (
        actual is ValueKind.INTEGER and ValueKind.NUMBER in expected
    )
    if not compatible:
        raise ValueError("prepared expression result contradicts its static output kind")


class PreparedCapabilityKind(DomainStrEnum):
    """Pure capabilities intentionally prepared outside this engine."""

    REGEX = "regex"
    JSON_SCHEMA = "json-schema"
    DIGEST = "digest"


class PreparedPredicateOutcome(DomainStrEnum):
    """Closed result of a pinned external pure predicate implementation."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@final
class PreparedPredicate(msgspec.Struct, frozen=True, kw_only=True):
    """Exact execution-, operation-, implementation-, input-, and location-bound result."""

    kind: PreparedCapabilityKind
    selector_fingerprint: Fingerprint
    operation_fingerprint: Fingerprint
    implementation: NamedCapabilityTarget
    selected_fingerprint: Fingerprint | SensitiveValue[Fingerprint]
    operands_fingerprint: Fingerprint | SensitiveValue[Fingerprint]
    environment_execution_id: EnvironmentExecutionId
    location: ConcreteLocation
    outcome: PreparedPredicateOutcome
    code: DiagnosticCode | None = None
    evidence: PreparedOperatorEvidence | None = None

    def __post_init__(self) -> None:
        """Bind terminal failures exactly to safe diagnostics."""
        _validate_prepared_identity(self)
        _validate_prepared_outcome(self)


def _validate_prepared_identity(value: PreparedPredicate) -> None:
    """Validate the complete immutable identity of one prepared predicate."""
    if type(value.kind) is not PreparedCapabilityKind:
        raise TypeError("prepared predicate kind must be typed")
    if type(value.selector_fingerprint) is not Fingerprint:
        raise TypeError("prepared predicate requires a selector Fingerprint")
    if type(value.operation_fingerprint) is not Fingerprint:
        raise TypeError("prepared predicate requires an operation Fingerprint")
    if (
        type(value.implementation) is not NamedCapabilityTarget
        or value.implementation.kind is not CapabilityTargetKind.PROVIDER
    ):
        raise ValueError("prepared predicate requires an exact provider identity")
    _require_binding_fingerprint(value.selected_fingerprint, "selected")
    _require_binding_fingerprint(value.operands_fingerprint, "operands")
    if type(value.environment_execution_id) is not EnvironmentExecutionId:
        raise TypeError("prepared predicate requires an EnvironmentExecutionId")
    if not is_concrete_location(value.location):
        raise TypeError("prepared predicate requires a concrete location")
    if value.location.environment_execution_id != value.environment_execution_id:
        raise ValueError("prepared predicate location belongs to another execution")


def _validate_prepared_outcome(value: PreparedPredicate) -> None:
    """Validate terminal diagnostics and kind-specific structured evidence."""
    if type(value.outcome) is not PreparedPredicateOutcome:
        raise TypeError("prepared predicate outcome must be typed")
    if value.code is not None and type(value.code) is not DiagnosticCode:
        raise TypeError("prepared predicate code must be a DiagnosticCode")
    failed = value.outcome in {
        PreparedPredicateOutcome.UNAVAILABLE,
        PreparedPredicateOutcome.ERROR,
    }
    if failed != (value.code is not None):
        raise ValueError("exactly unavailable or error prepared predicates require a code")
    if failed and value.evidence is not None:
        raise ValueError("failed prepared predicates cannot carry operator evidence")
    _validate_evidence_sensitivity(value, failed=failed)
    if value.kind is PreparedCapabilityKind.JSON_SCHEMA:
        _validate_schema_evidence(value, failed=failed)
    elif value.kind is PreparedCapabilityKind.DIGEST:
        if not failed and type(_revealed_evidence(value.evidence)) is not DigestEvidence:
            raise TypeError("terminal digest predicates require digest evidence")
    elif value.evidence is not None:
        raise ValueError("regex predicates cannot carry operator evidence")


def _validate_schema_evidence(value: PreparedPredicate, *, failed: bool) -> None:
    evidence = _revealed_evidence(value.evidence)
    if not failed and type(evidence) is not SchemaValidationEvidence:
        raise TypeError("terminal schema predicates require schema evidence")
    if type(evidence) is SchemaValidationEvidence:
        violated = value.outcome is PreparedPredicateOutcome.VIOLATED
        if violated != (evidence.violation_count.value > 0):
            raise ValueError("schema outcome contradicts its violation evidence")


def _validate_evidence_sensitivity(value: PreparedPredicate, *, failed: bool) -> None:
    if failed or value.kind is PreparedCapabilityKind.REGEX:
        return
    expected = _binding_origins(value)
    if bool(expected) != is_sensitive_value(value.evidence):
        raise ValueError("prepared operator evidence sensitivity contradicts its bindings")
    if is_sensitive_value(value.evidence) and value.evidence.origins != expected:
        raise ValueError("prepared operator evidence taint origins contradict its bindings")


def _binding_origins(value: PreparedPredicate) -> tuple[TaintOriginId, ...]:
    groups = tuple(
        fingerprint.origins
        for fingerprint in (value.selected_fingerprint, value.operands_fingerprint)
        if is_sensitive_value(fingerprint)
    )
    return stable_taint_origin_union(*groups)


def _revealed_evidence(value: PreparedOperatorEvidence | None) -> object | None:
    return value.reveal(access=_EVIDENCE_ACCESS) if is_sensitive_value(value) else value


@final
class PreparedAssertionInputs(msgspec.Struct, frozen=True, kw_only=True):
    """Closed values and capability facts consumed by one assertion rule."""

    rule_fingerprint: Fingerprint
    environment_execution_id: EnvironmentExecutionId
    expressions: tuple[PreparedExpression, ...] = ()
    predicates: tuple[PreparedPredicate, ...] = ()

    def __post_init__(self) -> None:
        """Require unique exact bindings for one rule application."""
        if type(self.rule_fingerprint) is not Fingerprint:
            raise TypeError("prepared assertion inputs require a rule Fingerprint")
        if type(self.environment_execution_id) is not EnvironmentExecutionId:
            raise TypeError("prepared assertion inputs require an EnvironmentExecutionId")
        if type(self.expressions) is not tuple or any(
            type(item) is not PreparedExpression for item in self.expressions
        ):
            raise TypeError("prepared expressions must be an immutable typed tuple")
        if type(self.predicates) is not tuple or any(
            type(item) is not PreparedPredicate for item in self.predicates
        ):
            raise TypeError("prepared predicates must be an immutable typed tuple")
        expression_values = tuple(item.expression for item in self.expressions)
        if any(value in expression_values[:index] for index, value in enumerate(expression_values)):
            raise ValueError("prepared expressions must be unique")
        predicate_keys = tuple(
            (
                item.kind,
                item.selector_fingerprint,
                item.operation_fingerprint,
                item.implementation,
                item.location,
            )
            for item in self.predicates
        )
        if len(set(predicate_keys)) != len(predicate_keys):
            raise ValueError("prepared predicate bindings must be unique")
        if any(
            item.environment_execution_id != self.environment_execution_id
            for item in self.predicates
        ):
            raise ValueError("prepared predicates must belong to their input execution")


def required_expressions(rule: CompiledAssertionRule) -> tuple[ValueExpression, ...]:
    """Return ordered unique expressions required by a compiled rule tree."""
    if type(rule) is not CompiledAssertionRule:
        raise TypeError("required expressions need a CompiledAssertionRule")
    found = (*operator_expressions(rule.operator), *_condition_expressions(rule.applicability))
    return tuple(value for index, value in enumerate(found) if value not in found[:index])


def require_exact_expression_coverage(
    rule: CompiledAssertionRule,
    inputs: PreparedAssertionInputs,
) -> None:
    """Reject missing, unused, or reordered prepared operand bindings."""
    required = required_expressions(rule)
    supplied = tuple(item.expression for item in inputs.expressions)
    if supplied != required:
        raise ValueError("prepared expressions must exactly cover rule expressions in stable order")


def require_known_predicate_bindings(
    rule: CompiledAssertionRule,
    inputs: PreparedAssertionInputs,
) -> None:
    """Reject prepared capabilities for operators absent from the exact compiled rule tree."""
    if type(rule) is not CompiledAssertionRule or type(inputs) is not PreparedAssertionInputs:
        raise TypeError("prepared predicate validation requires a compiled rule and inputs")
    allowed = tuple(
        contract
        for operator, selector in _rule_operator_selectors(rule)
        if (contract := _capability_binding(operator, selector)) is not None
    )
    for item in inputs.predicates:
        key = (
            item.kind,
            item.selector_fingerprint,
            item.operation_fingerprint,
            item.implementation,
        )
        selector = next(
            (candidate for contract, candidate in allowed if contract == key),
            None,
        )
        if selector is None:
            raise ValueError("prepared predicates contain a foreign or unused capability binding")
        require_location_matches_selector(item.location, selector)


def operator_expressions(operator: AssertionOperator) -> tuple[ValueExpression, ...]:
    """Return the stable operand order for one built-in operator."""
    if type(operator) is EqualityOperator:
        return (operator.expected,)
    if type(operator) is OrderingOperator:
        return (operator.expected,)
    if type(operator) is StringOperator:
        return (operator.expected,)
    if type(operator) is DigestOperator:
        return (operator.expected,)
    if type(operator) is MembershipOperator:
        return operator.choices
    if type(operator) is BetweenOperator:
        return operator.lower, operator.upper
    if type(operator) is ApproximateOperator:
        return (
            operator.expected,
            *(
                value
                for value in (operator.absolute_tolerance, operator.relative_tolerance)
                if value is not None
            ),
        )
    if type(operator) is ContainsOperator:
        return operator.expected
    return ()


def is_prepared_operand_result(value: object) -> TypeGuard[PreparedOperandResult]:
    """Narrow arbitrary values to prepared operand outcomes."""
    return type(value) in {PreparedOperandReady, PreparedOperandUnavailable, PreparedOperandError}


def _condition_expressions(
    condition: ApplicabilityCondition | None,
) -> tuple[ValueExpression, ...]:
    if condition is None:
        return ()
    if type(condition) is PredicateCondition:
        return operator_expressions(condition.operator)
    if type(condition) in {AllConditions, AnyConditions}:
        return tuple(
            value for child in condition.conditions for value in _condition_expressions(child)
        )
    raise TypeError("unsupported applicability condition")


def _rule_operator_selectors(
    rule: CompiledAssertionRule,
) -> tuple[tuple[AssertionOperator, CompiledSelector], ...]:
    return ((rule.operator, rule.selector), *_condition_operator_selectors(rule.applicability))


def _condition_operator_selectors(
    condition: ApplicabilityCondition | None,
) -> tuple[tuple[AssertionOperator, CompiledSelector], ...]:
    if condition is None:
        return ()
    if type(condition) is PredicateCondition:
        return ((condition.operator, condition.selector),)
    if type(condition) in {AllConditions, AnyConditions}:
        return tuple(
            item for child in condition.conditions for item in _condition_operator_selectors(child)
        )
    raise TypeError("unsupported applicability condition")


def _capability_binding(
    operator: AssertionOperator,
    selector: CompiledSelector,
) -> (
    tuple[
        tuple[PreparedCapabilityKind, Fingerprint, Fingerprint, NamedCapabilityTarget],
        CompiledSelector,
    ]
    | None
):
    kind = {
        RegexOperator: PreparedCapabilityKind.REGEX,
        SchemaOperator: PreparedCapabilityKind.JSON_SCHEMA,
        DigestOperator: PreparedCapabilityKind.DIGEST,
    }.get(type(operator))
    if kind is None:
        return None
    return (
        kind,
        selector.fingerprint,
        operation_fingerprint(operator),
        capability_implementation(operator),
    ), selector


def _require_binding_fingerprint(
    value: Fingerprint | SensitiveValue[Fingerprint],
    label: str,
) -> None:
    if type(value) is Fingerprint:
        return
    if not is_sensitive_value(value) or value.payload_type is not Fingerprint:
        raise TypeError(f"prepared predicate {label} binding requires a Fingerprint")
