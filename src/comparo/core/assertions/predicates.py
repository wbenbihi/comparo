"""Pure typed built-in assertion predicates with no implicit conversion."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.assertions.bindings import capability_implementation
from comparo.core.assertions.bindings import fingerprints_equal
from comparo.core.assertions.bindings import operands_fingerprint
from comparo.core.assertions.bindings import operation_fingerprint
from comparo.core.assertions.bindings import selected_fingerprint
from comparo.core.assertions.digest import digest_matches
from comparo.core.assertions.digest import validate_digest_expected
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.inputs import PreparedCapabilityKind
from comparo.core.assertions.inputs import PreparedOperandError
from comparo.core.assertions.inputs import PreparedOperandUnavailable
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.assertions.inputs import operator_expressions
from comparo.core.assertions.numeric import approximate_result
from comparo.core.domain.assertions.evidence import AssertionOperatorEvidenceValue
from comparo.core.domain.assertions.evidence import DigestEvidence
from comparo.core.domain.assertions.evidence import SchemaValidationEvidence
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
from comparo.core.domain.assertions.regex import RegexOperator
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.evaluation import semantic_compare
from comparo.core.evaluation import semantic_equal
from comparo.core.evaluation import text_value
from comparo.core.selection.snapshot import SelectionSnapshot

_ACCESS = SensitiveValueAccess(purpose="assertion.evaluate.predicate")


@final
class PredicateSatisfied(msgspec.Struct, frozen=True, kw_only=True):
    """The typed predicate returned true."""

    evidence: AssertionOperatorEvidenceValue | None = None


@final
class PredicateViolated(msgspec.Struct, frozen=True, kw_only=True):
    """The typed predicate returned false."""

    evidence: AssertionOperatorEvidenceValue | None = None


@final
class PredicateUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """A prepared operand or capability was unavailable."""

    code: DiagnosticCode
    reason: AssertionUnavailableReason


@final
class PredicateError(msgspec.Struct, frozen=True, kw_only=True):
    """Runtime operand types or a prepared capability failed."""

    code: DiagnosticCode


type PredicateResult = (
    PredicateSatisfied | PredicateViolated | PredicateUnavailable | PredicateError
)


@final
class PredicateOperands(msgspec.Struct, frozen=True, kw_only=True):
    """Revealed values plus the exact safe origin IDs removed for internal evaluation."""

    values: tuple[object, ...]
    origin_groups: tuple[tuple[TaintOriginId, ...], ...] = ()

    @property
    def origins(self) -> tuple[TaintOriginId, ...]:
        """Return the stable linear union after the caller has prepaid lineage traversal."""
        return stable_taint_origin_union(*self.origin_groups)


def evaluate_predicate(
    match: SelectionMatch,
    operator: AssertionOperator,
    inputs: PreparedAssertionInputs,
    snapshot: SelectionSnapshot,
) -> PredicateResult:
    """Evaluate one built-in operation against one selected value."""
    del snapshot
    actual = _reveal(match.value)
    resolved = predicate_operands(operator, inputs)
    if type(resolved) is PredicateUnavailable:
        return resolved
    if type(resolved) is PredicateError:
        return resolved
    operands = resolved.values
    try:
        outcome = _dispatch(actual, match, operator, operands, inputs)
    except (ArithmeticError, TypeError, ValueError):
        return PredicateError(code=_code("assertion.operand.invalid"))
    if type(outcome) is PredicateUnavailable or type(outcome) is PredicateError:
        return outcome
    origins = _merge_origins(match.taint_origins, resolved.origins)
    if type(outcome) is PredicateSatisfied:
        return _seal_result(outcome, origins)
    if type(outcome) is PredicateViolated:
        return _seal_result(outcome, origins)
    result = PredicateSatisfied() if outcome else PredicateViolated()
    return _seal_result(result, origins)


def predicate_operands(
    operator: AssertionOperator,
    inputs: PreparedAssertionInputs,
) -> PredicateOperands | PredicateUnavailable | PredicateError:
    """Return exact revealed operands or the prepared terminal resolution outcome."""
    values: list[object] = []
    origin_groups: list[tuple[TaintOriginId, ...]] = []
    for expression in operator_expressions(operator):
        prepared = next(item for item in inputs.expressions if item.expression == expression)
        if type(prepared.result) is PreparedOperandUnavailable:
            return PredicateUnavailable(
                code=prepared.result.code,
                reason=AssertionUnavailableReason.OPERAND,
            )
        if type(prepared.result) is PreparedOperandError:
            return PredicateError(code=prepared.result.code)
        ready = prepared.result
        if is_sensitive_value(ready.value):
            origin_groups.append(ready.value.origins)
        values.append(_reveal(ready.value))
    return PredicateOperands(values=tuple(values), origin_groups=tuple(origin_groups))


def _dispatch(  # noqa: C901
    actual: object,
    match: SelectionMatch,
    operator: AssertionOperator,
    operands: tuple[object, ...],
    inputs: PreparedAssertionInputs,
) -> bool | PredicateSatisfied | PredicateViolated | PredicateUnavailable | PredicateError:
    if type(operator) is EqualityOperator:
        equal = semantic_equal(cast("SelectedValue", actual), cast("SelectedValue", operands[0]))
        return equal if operator.relation is EqualityRelation.EQUALS else not equal
    if type(operator) is MembershipOperator:
        found = any(
            semantic_equal(cast("SelectedValue", actual), cast("SelectedValue", choice))
            for choice in operands
        )
        return found if operator.relation is MembershipRelation.IN else not found
    if type(operator) is TypeOperator:
        return _is_type(actual, operator.expected)
    if type(operator) is OrderingOperator:
        relation = semantic_compare(
            cast("SelectedValue", actual), cast("SelectedValue", operands[0])
        )
        if relation is None:
            raise TypeError
        return _ordering(relation, operator.relation)
    if type(operator) is BetweenOperator:
        bounds = semantic_compare(
            cast("SelectedValue", operands[0]), cast("SelectedValue", operands[1])
        )
        if bounds is None or bounds > 0:
            raise ValueError
        lower = semantic_compare(cast("SelectedValue", actual), cast("SelectedValue", operands[0]))
        upper = semantic_compare(cast("SelectedValue", actual), cast("SelectedValue", operands[1]))
        if lower is None or upper is None:
            raise TypeError
        return (lower >= 0 if operator.include_lower else lower > 0) and (
            upper <= 0 if operator.include_upper else upper < 0
        )
    if type(operator) is ApproximateOperator:
        return _approximately(actual, operands, operator)
    if type(operator) is StringOperator:
        return _string(actual, operands[0], operator.relation)
    if type(operator) is ContainsOperator:
        return _contains(actual, operands, operator.mode)
    if type(operator) is KeySetOperator:
        return _keys(actual, operator)
    if type(operator) is LengthOperator:
        return _length(actual, operator)
    if type(operator) is MediaTypeOperator:
        return _media(actual, operator)
    if type(operator) is TokenOperator:
        if type(actual) is not HttpFieldItems:
            raise TypeError
        return operator.token in actual.items
    if type(operator) is DigestOperator:
        if type(actual) not in {BytesValue, BodyId}:
            raise TypeError
        validate_digest_expected(operands[0], operator)
        return _prepared_capability(
            match,
            operator,
            operands,
            PreparedCapabilityKind.DIGEST,
            inputs,
        )
    if type(operator) is RegexOperator:
        prepared = _prepared_capability(
            match,
            operator,
            operands,
            PreparedCapabilityKind.REGEX,
            inputs,
        )
        if type(prepared) is PredicateSatisfied and operator.negate:
            return PredicateViolated()
        if type(prepared) is PredicateViolated and operator.negate:
            return PredicateSatisfied()
        return prepared
    if type(operator) is SchemaOperator:
        return _prepared_capability(
            match,
            operator,
            operands,
            PreparedCapabilityKind.JSON_SCHEMA,
            inputs,
        )
    raise TypeError("operator is not a match predicate")


def _is_type(actual: object, expected: ExactValueType) -> bool:
    mapping = {
        ExactValueType.NULL: JsonNull,
        ExactValueType.BOOLEAN: JsonBoolean,
        ExactValueType.INTEGER: JsonInteger,
        ExactValueType.NUMBER: JsonDecimal,
        ExactValueType.STRING: JsonString,
        ExactValueType.ARRAY: JsonArray,
        ExactValueType.OBJECT: JsonObject,
        ExactValueType.BYTES: BytesValue,
        ExactValueType.DURATION: Duration,
        ExactValueType.SIZE: ByteCount,
        ExactValueType.INSTANT: UtcInstant,
    }
    return type(actual) is mapping[expected]


def _ordering(value: int, relation: OrderingRelation) -> bool:
    return {
        OrderingRelation.LESS: value < 0,
        OrderingRelation.LESS_OR_EQUAL: value <= 0,
        OrderingRelation.GREATER: value > 0,
        OrderingRelation.GREATER_OR_EQUAL: value >= 0,
    }[relation]


def _approximately(
    actual: object, operands: tuple[object, ...], operator: ApproximateOperator
) -> PredicateSatisfied | PredicateViolated:
    satisfied, evidence = approximate_result(actual, operands, operator)
    if satisfied:
        return PredicateSatisfied(evidence=evidence)
    return PredicateViolated(evidence=evidence)


def _string(actual: object, expected: object, relation: StringRelation) -> bool:
    actual_text = text_value(actual)
    expected_text = text_value(expected)
    if actual_text is None or expected_text is None:
        raise TypeError
    return {
        StringRelation.CONTAINS: expected_text in actual_text,
        StringRelation.NOT_CONTAINS: expected_text not in actual_text,
        StringRelation.STARTS_WITH: actual_text.startswith(expected_text),
        StringRelation.ENDS_WITH: actual_text.endswith(expected_text),
    }[relation]


def _contains(actual: object, expected: tuple[object, ...], mode: ContainsMode) -> bool:
    if type(actual) is JsonString:
        checks = tuple(type(item) is JsonString and item.value in actual.value for item in expected)
    elif type(actual) is JsonArray:
        checks = tuple(
            any(
                semantic_equal(cast("SelectedValue", item), cast("SelectedValue", value))
                for value in actual.items
            )
            for item in expected
        )
    elif type(actual) is JsonObject:
        names = {member.name for member in actual.members}
        checks = tuple(type(item) is JsonString and item.value in names for item in expected)
    else:
        raise TypeError
    if mode is ContainsMode.ONE:
        return checks[0]
    return all(checks) if mode is ContainsMode.ALL else any(checks)


def _keys(actual: object, operator: KeySetOperator) -> bool:
    if type(actual) is not JsonObject:
        raise TypeError
    names = {member.name for member in actual.members}
    requested = set(operator.keys)
    if operator.relation is KeySetRelation.HAS:
        return operator.keys[0] in names
    if operator.relation is KeySetRelation.HAS_ALL:
        return requested <= names
    return requested == names


def _length(actual: object, operator: LengthOperator) -> bool:
    if type(actual) is JsonString or type(actual) is BytesValue:
        length = len(actual.value)
    elif type(actual) is JsonArray:
        length = len(actual.items)
    elif type(actual) is JsonObject:
        length = len(actual.members)
    elif type(actual) is HttpFieldItems:
        length = len(actual.items)
    else:
        raise TypeError
    expected = operator.expected.value
    return {
        CountRelation.EQUALS: length == expected,
        CountRelation.AT_LEAST: length >= expected,
        CountRelation.AT_MOST: length <= expected,
    }[operator.relation]


def _media(actual: object, operator: MediaTypeOperator) -> bool:
    if type(actual) is not MediaType:
        raise TypeError
    expected = operator.expected
    type_matches = expected.type == "*" or actual.type == expected.type
    subtype_matches = actual.subtype == expected.subtype
    if operator.relation is MediaRelation.SUFFIX_MATCH:
        suffix = expected.subtype.removeprefix("*+")
        return type_matches and actual.subtype.endswith(f"+{suffix}")
    if not type_matches or not subtype_matches:
        return False
    if operator.relation is MediaRelation.IGNORE_PARAMETERS:
        return True
    actual_params = {item.name: item.value for item in actual.parameters}
    expected_params = {item.name: item.value for item in expected.parameters}
    if operator.relation is MediaRelation.SUBSET_PARAMETERS:
        return all(actual_params.get(name) == value for name, value in expected_params.items())
    return actual_params == expected_params


def _prepared_capability(
    match: SelectionMatch,
    operator: AssertionOperator,
    operands: tuple[object, ...],
    kind: PreparedCapabilityKind,
    inputs: PreparedAssertionInputs,
) -> PredicateSatisfied | PredicateViolated | PredicateUnavailable | PredicateError:
    expected_operation = operation_fingerprint(operator)
    expected_implementation = capability_implementation(operator)
    expected_selected = selected_fingerprint(match)
    expected_operands = operands_fingerprint(operands)
    prepared = next(
        (
            item
            for item in inputs.predicates
            if item.kind is kind
            and item.operation_fingerprint == expected_operation
            and item.implementation == expected_implementation
            and item.location == match.location
            and item.environment_execution_id == inputs.environment_execution_id
            and fingerprints_equal(item.selected_fingerprint, expected_selected)
            and fingerprints_equal(item.operands_fingerprint, expected_operands)
        ),
        None,
    )
    if prepared is None:
        return PredicateUnavailable(
            code=_code(f"assertion.capability.{kind.value}.unavailable"),
            reason=AssertionUnavailableReason.CAPABILITY,
        )
    if prepared.outcome is PreparedPredicateOutcome.UNAVAILABLE:
        return PredicateUnavailable(
            code=cast("DiagnosticCode", prepared.code),
            reason=AssertionUnavailableReason.CAPABILITY,
        )
    if prepared.outcome is PreparedPredicateOutcome.ERROR:
        return PredicateError(code=cast("DiagnosticCode", prepared.code))
    evidence = prepared.evidence
    revealed_evidence = _reveal(evidence)
    if type(operator) is SchemaOperator and type(revealed_evidence) is not SchemaValidationEvidence:
        raise TypeError
    if type(operator) is DigestOperator and type(revealed_evidence) is not DigestEvidence:
        raise TypeError
    if type(operator) is DigestOperator:
        digest_satisfied = digest_matches(
            cast("DigestEvidence", revealed_evidence), operands[0], operator
        )
        if digest_satisfied != (prepared.outcome is PreparedPredicateOutcome.SATISFIED):
            return PredicateError(code=_code("assertion.capability.digest.invalid"))
    if prepared.outcome is PreparedPredicateOutcome.SATISFIED:
        return PredicateSatisfied(evidence=evidence)
    return PredicateViolated(evidence=evidence)


def _reveal(value: object) -> object:
    if is_sensitive_value(value):
        return value.reveal(access=_ACCESS)
    return value


def _code(value: str) -> DiagnosticCode:
    return DiagnosticCode(value=value)


def _merge_origins(
    first: tuple[TaintOriginId, ...],
    second: tuple[TaintOriginId, ...],
) -> tuple[TaintOriginId, ...]:
    return stable_taint_origin_union(first, second)


def _seal_result(
    result: PredicateSatisfied | PredicateViolated,
    origins: tuple[TaintOriginId, ...],
) -> PredicateSatisfied | PredicateViolated:
    evidence = result.evidence
    if evidence is None or not origins:
        return result
    if is_sensitive_value(evidence):
        if evidence.origins != origins:
            raise ValueError("prepared evidence taint differs from predicate dependencies")
        return result
    sealed = SensitiveValue(evidence, origins=origins)
    return msgspec.structs.replace(result, evidence=sealed)
