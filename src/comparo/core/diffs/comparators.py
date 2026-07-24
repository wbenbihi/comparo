"""Pure directed comparator semantics with no decoding or conversion."""

from __future__ import annotations

from fractions import Fraction

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.exact_numbers import decimal_fraction
from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.diffs.operands import resolve_operand
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import PairUnit
from comparo.core.diffs.value_support import canonical_key
from comparo.core.diffs.value_support import merge_taint_origins
from comparo.core.diffs.value_support import require_sort_work
from comparo.core.diffs.value_support import require_work
from comparo.core.diffs.value_support import reveal
from comparo.core.diffs.value_support import value_size
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.strategies import CollectionComparator
from comparo.core.domain.diffs.strategies import CollectionComparisonKind
from comparo.core.domain.diffs.strategies import CompatibilityComparator
from comparo.core.domain.diffs.strategies import CompatibilityKind
from comparo.core.domain.diffs.strategies import DiffComparator
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import IgnoreComparator
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.diffs.strategies import TextPredicateComparator
from comparo.core.domain.diffs.strategies import TextPredicateRelation
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import selected_value_kind
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value
from comparo.core.evaluation import numeric_value
from comparo.core.evaluation import semantic_equal
from comparo.core.evaluation import text_value


def compare_unit(
    unit: PairUnit,
    comparator: DiffComparator,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
) -> tuple[DiffFactOutcome, DiagnosticCode | None]:
    """Resolve one pair without confusing absence, ignore, or incomparability."""
    if unit.incomparable is not None:
        return DiffFactOutcome.INCOMPARABLE, unit.incomparable
    if unit.ignored:
        return DiffFactOutcome.IGNORED, None
    if type(comparator) is IgnoreComparator:
        return DiffFactOutcome.IGNORED, None
    if unit.baseline is None:
        return DiffFactOutcome.ADDED, None
    if unit.candidate is None:
        return DiffFactOutcome.REMOVED, None
    left, right = reveal(unit.baseline.value), reveal(unit.candidate.value)
    value_size(left, meter)
    value_size(right, meter)
    try:
        same = _compare(left, right, comparator, inputs, meter)
    except (TypeError, ValueError) as error:
        del error
        return (
            DiffFactOutcome.INCOMPARABLE,
            DiagnosticCode(value="diff.comparator.runtime-type"),
        )
    return (DiffFactOutcome.SAME if same else DiffFactOutcome.CHANGED), None


def _compare(
    left: SelectedValue,
    right: SelectedValue,
    comparator: DiffComparator,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
) -> bool:
    if type(comparator) is ExactComparator:
        return canonical_key(left, meter) == canonical_key(right, meter)
    if type(comparator) is NumericToleranceComparator:
        baseline = decimal_fraction(numeric_value(left), meter)
        candidate = decimal_fraction(numeric_value(right), meter)
        absolute = _optional_number(inputs, comparator.absolute_tolerance, meter)
        relative = _optional_number(inputs, comparator.relative_tolerance, meter)
        delta = abs(candidate - baseline)
        effective = max(
            value
            for value in (
                absolute,
                None if relative is None else relative * max(abs(baseline), abs(candidate)),
            )
            if value is not None
        )
        return delta <= effective
    if type(comparator) is TextPredicateComparator:
        baseline_text, candidate_text = text_value(left), text_value(right)
        if baseline_text is None or candidate_text is None:
            raise TypeError("text comparator requires text")
        require_work(
            meter,
            max(1, (len(baseline_text) + 1) * (len(candidate_text) + 1)),
        )
        return {
            TextPredicateRelation.BASELINE_CONTAINS_CANDIDATE: candidate_text in baseline_text,
            TextPredicateRelation.CANDIDATE_CONTAINS_BASELINE: baseline_text in candidate_text,
            TextPredicateRelation.CANDIDATE_STARTS_WITH_BASELINE: candidate_text.startswith(
                baseline_text
            ),
            TextPredicateRelation.CANDIDATE_ENDS_WITH_BASELINE: candidate_text.endswith(
                baseline_text
            ),
        }[comparator.relation]
    if type(comparator) is CompatibilityComparator:
        return _compatible(left, right, comparator.kind, meter)
    if type(comparator) is CollectionComparator:
        if type(left) is not JsonArray or type(right) is not JsonArray:
            raise TypeError("collection comparator requires arrays")
        return _collections(left, right, comparator.kind, meter)
    raise TypeError("unsupported diff comparator")


def _compatible(
    baseline: SelectedValue,
    candidate: SelectedValue,
    kind: CompatibilityKind,
    meter: DiffMeter,
) -> bool:
    if kind is CompatibilityKind.TYPE:
        return selected_value_kind(baseline) is selected_value_kind(candidate)
    if not is_json_value(baseline) or not is_json_value(candidate):
        raise TypeError("JSON compatibility requires JSON values")
    if kind is CompatibilityKind.JSON_SHAPE:
        return _shape_equal(baseline, candidate, meter)
    if kind is CompatibilityKind.SUBSET:
        return _json_subset(baseline, candidate, meter)
    return _json_subset(candidate, baseline, meter)


def _shape_equal(left: JsonValue, right: JsonValue, meter: DiffMeter) -> bool:
    pending: list[tuple[JsonValue, JsonValue, int]] = [(left, right, 0)]
    while pending:
        current_left, current_right, depth = pending.pop()
        _require_depth(depth)
        require_work(meter)
        if selected_value_kind(current_left) is not selected_value_kind(current_right):
            return False
        if type(current_left) is JsonObject and type(current_right) is JsonObject:
            require_work(meter, max(1, len(current_right.members)))
            right_members = {member.name: member.value for member in current_right.members}
            if len(current_left.members) != len(right_members):
                return False
            for member in current_left.members:
                match = right_members.get(member.name)
                if match is None:
                    return False
                pending.append((member.value, match, depth + 1))
        elif type(current_left) is JsonArray and type(current_right) is JsonArray:
            if len(current_left.items) != len(current_right.items):
                return False
            pending.extend(
                (a, b, depth + 1)
                for a, b in zip(current_left.items, current_right.items, strict=True)
            )
    return True


def _json_subset(subset: JsonValue, superset: JsonValue, meter: DiffMeter) -> bool:
    pending: list[tuple[JsonValue, JsonValue, int]] = [(subset, superset, 0)]
    while pending:
        current_subset, current_superset, depth = pending.pop()
        _require_depth(depth)
        require_work(meter)
        if type(current_subset) is JsonObject and type(current_superset) is JsonObject:
            require_work(meter, max(1, len(current_superset.members)))
            right = {member.name: member.value for member in current_superset.members}
            for member in current_subset.members:
                match = right.get(member.name)
                if match is None:
                    return False
                pending.append((member.value, match, depth + 1))
        elif type(current_subset) is JsonArray and type(current_superset) is JsonArray:
            right_keys = {canonical_key(item, meter) for item in current_superset.items}
            if any(canonical_key(item, meter) not in right_keys for item in current_subset.items):
                return False
        elif not semantic_equal(current_subset, current_superset):
            return False
    return True


def _collections(
    baseline: JsonArray,
    candidate: JsonArray,
    kind: CollectionComparisonKind,
    meter: DiffMeter,
) -> bool:
    if kind is CollectionComparisonKind.ORDERED:
        return canonical_key(baseline, meter) == canonical_key(candidate, meter)
    require_work(meter, max(1, len(baseline.items) + len(candidate.items)))
    left = [canonical_key(item, meter) for item in baseline.items]
    right = [canonical_key(item, meter) for item in candidate.items]
    if kind is CollectionComparisonKind.SET:
        return set(left) == set(right)
    require_sort_work(meter, len(left))
    require_sort_work(meter, len(right))
    return sorted(left) == sorted(right)


def _require_depth(depth: int) -> None:
    if depth > 256:
        raise DiffHaltError(code=DiagnosticCode(value="diff.value.depth-unsupported"))


def _optional_number(
    inputs: PreparedDiffInputs,
    expression: ValueExpression | None,
    meter: DiffMeter,
) -> Fraction | None:
    if expression is None:
        return None
    value = resolve_operand(inputs, expression, DiffSide.BOTH).value
    try:
        tolerance = decimal_fraction(numeric_value(value), meter)
    except TypeError as error:
        raise DiffHaltError(code=DiagnosticCode(value="diff.comparator.tolerance-type")) from error
    if tolerance < 0:
        raise DiffHaltError(code=DiagnosticCode(value="diff.comparator.tolerance-negative"))
    return tolerance


def comparator_dependency_taint(
    comparator: DiffComparator,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
) -> tuple[TaintOriginId, ...]:
    """Return safe operand lineage for a comparator that actually consumed operands."""
    if type(comparator) is not NumericToleranceComparator:
        return ()
    groups: list[tuple[TaintOriginId, ...]] = []
    for expression in (comparator.absolute_tolerance, comparator.relative_tolerance):
        if expression is None:
            continue
        groups.append(resolve_operand(inputs, expression, DiffSide.BOTH).taint_origins)
    return merge_taint_origins(meter, *groups)


__all__ = ("comparator_dependency_taint", "compare_unit")
