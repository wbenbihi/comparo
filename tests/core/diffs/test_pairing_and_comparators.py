"""Pairing conservation and directed comparator semantics."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from comparo.core.diffs import PreparedDiffInputs
from comparo.core.diffs import PreparedExpression
from comparo.core.diffs import PreparedOperandReady
from comparo.core.diffs.comparators import compare_unit
from comparo.core.diffs.pairing import pair_values
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import PairUnit
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.strategies import CollectionComparator
from comparo.core.domain.diffs.strategies import CollectionComparisonKind
from comparo.core.domain.diffs.strategies import CompatibilityComparator
from comparo.core.domain.diffs.strategies import CompatibilityKind
from comparo.core.domain.diffs.strategies import DuplicateKeyMode
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import IgnoreComparator
from comparo.core.domain.diffs.strategies import KeyedPairing
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.diffs.strategies import OrderedPairing
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.diffs.strategies import TextPredicateComparator
from comparo.core.domain.diffs.strategies import TextPredicateRelation
from comparo.core.domain.diffs.strategies import UnorderedPairing
from comparo.core.domain.diffs.strategies import UnorderedPairingKind
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
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _literal
from tests.core.domain.diffs._invariant_support import _operation


def test_ordered_pairing_charges_before_each_unit_and_preserves_partial_pairs() -> None:
    values = (
        derived(JsonInteger(value=1), index=101),
        derived(JsonInteger(value=2), index=102),
    )
    with pytest.raises(DiffHaltError) as caught:
        pair_values(
            values,
            values,
            OrderedPairing(identity=_operation(DiffOperationFamily.PAIRING, "ordered")),
            meter(pairs=1),
        )
    assert caught.value.exhaustion is not None
    assert len(caught.value.partial_pairs) == 1
    assert caught.value.partial_pairs[0].baseline == values[0]


def test_singular_and_ordered_pairing_preserve_one_sided_direction() -> None:
    left = derived(JsonString(value="left"))
    singular = SingularPairing(identity=_operation(DiffOperationFamily.PAIRING, "singular"))
    assert pair_values((left,), (), singular, meter())[0].candidate is None
    with pytest.raises(DiffHaltError):
        pair_values((left, left), (), singular, meter())
    ordered = OrderedPairing(identity=_operation(DiffOperationFamily.PAIRING, "ordered"))
    pairs = pair_values((), (left, left), ordered, meter())
    assert all(pair.baseline is None for pair in pairs)


def test_unordered_set_consumes_duplicate_multiplicity_as_explicit_ignores() -> None:
    left = (
        derived(JsonString(value="x"), index=110),
        derived(JsonString(value="x"), index=111),
    )
    right = (derived(JsonString(value="x"), candidate=True, index=112),)
    strategy = UnorderedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "set"),
        kind=UnorderedPairingKind.SET,
    )
    pairs = pair_values(left, right, strategy, meter())

    assert len(pairs) == 2
    assert pairs[0].baseline == left[0]
    assert pairs[0].candidate == right[0]
    assert pairs[1].baseline == left[1]
    assert pairs[1].ignored
    outcomes = tuple(
        compare_unit(
            pair,
            ExactComparator(identity=_operation(DiffOperationFamily.COMPARATOR, "exact")),
            inputs(rule()),
            meter(),
        )[0]
        for pair in pairs
    )
    assert outcomes == (DiffFactOutcome.SAME, DiffFactOutcome.IGNORED)


def test_unordered_multiset_preserves_multiplicity_as_removed_and_added() -> None:
    left = (
        derived(JsonString(value="x"), index=120),
        derived(JsonString(value="x"), index=121),
    )
    right = (
        derived(JsonString(value="x"), candidate=True, index=122),
        derived(JsonString(value="y"), candidate=True, index=123),
    )
    strategy = UnorderedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "multiset"),
        kind=UnorderedPairingKind.MULTISET,
    )
    pairs = pair_values(left, right, strategy, meter())
    assert len(pairs) == 3
    assert sum(pair.candidate is None for pair in pairs) == 1
    assert sum(pair.baseline is None for pair in pairs) == 1


def test_keyed_pairing_never_falls_back_positionally_and_marks_duplicates() -> None:
    duplicate = (
        derived(JsonString(value="a"), index=130),
        derived(JsonString(value="a"), index=131),
    )
    right = (derived(JsonString(value="a"), candidate=True, index=132),)
    incomparable = KeyedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
        baseline_key=_item_selector(),
        candidate_key=_item_selector(),
        duplicate_keys=DuplicateKeyMode.INCOMPARABLE,
    )
    pairs = pair_values(duplicate, right, incomparable, meter())
    assert len(pairs) == 3
    assert all(pair.incomparable is not None for pair in pairs)
    error = KeyedPairing(
        identity=incomparable.identity,
        baseline_key=incomparable.baseline_key,
        candidate_key=incomparable.candidate_key,
        duplicate_keys=DuplicateKeyMode.ERROR,
    )
    with pytest.raises(DiffHaltError) as caught:
        pair_values(duplicate, right, error, meter())
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.pairing.duplicate-key"


def test_keyed_pairing_conserves_candidate_only_duplicate_groups() -> None:
    right = (
        derived(JsonString(value="a"), candidate=True, index=133),
        derived(JsonString(value="a"), candidate=True, index=134),
    )
    strategy = KeyedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
        baseline_key=_item_selector(),
        candidate_key=_item_selector(),
        duplicate_keys=DuplicateKeyMode.INCOMPARABLE,
    )
    pairs = pair_values((), right, strategy, meter())
    assert tuple(pair.candidate for pair in pairs) == right
    assert all(pair.baseline is None and pair.incomparable is not None for pair in pairs)


def test_exact_ignore_absence_and_pairing_diagnostics_are_distinct() -> None:
    left = derived(JsonInteger(value=1))
    right = derived(JsonInteger(value=1), candidate=True)
    prepared = inputs(rule())
    exact = ExactComparator(identity=_operation(DiffOperationFamily.COMPARATOR, "exact"))
    ignored = IgnoreComparator(identity=_operation(DiffOperationFamily.COMPARATOR, "ignore"))
    assert (
        compare_unit(PairUnit(baseline=left, candidate=right), exact, prepared, meter())[0]
        is DiffFactOutcome.SAME
    )
    assert (
        compare_unit(PairUnit(candidate=right), exact, prepared, meter())[0]
        is DiffFactOutcome.ADDED
    )
    assert (
        compare_unit(PairUnit(baseline=left), exact, prepared, meter())[0]
        is DiffFactOutcome.REMOVED
    )
    assert (
        compare_unit(PairUnit(baseline=left), ignored, prepared, meter())[0]
        is DiffFactOutcome.IGNORED
    )


@pytest.mark.parametrize(
    ("relation", "baseline", "candidate", "same"),
    [
        (TextPredicateRelation.BASELINE_CONTAINS_CANDIDATE, "hello", "ell", True),
        (TextPredicateRelation.CANDIDATE_CONTAINS_BASELINE, "ell", "hello", True),
        (TextPredicateRelation.CANDIDATE_STARTS_WITH_BASELINE, "he", "hello", True),
        (TextPredicateRelation.CANDIDATE_ENDS_WITH_BASELINE, "lo", "hello", True),
        (TextPredicateRelation.CANDIDATE_ENDS_WITH_BASELINE, "he", "hello", False),
    ],
)
def test_text_predicates_are_explicitly_directed(
    relation: TextPredicateRelation,
    baseline: str,
    candidate: str,
    same: bool,
) -> None:
    comparator = TextPredicateComparator(
        identity=_operation(DiffOperationFamily.COMPARATOR, "text-predicate"),
        relation=relation,
    )
    outcome, _ = compare_unit(
        PairUnit(
            baseline=derived(JsonString(value=baseline)),
            candidate=derived(JsonString(value=candidate), candidate=True),
        ),
        comparator,
        inputs(rule()),
        meter(),
    )
    assert (outcome is DiffFactOutcome.SAME) is same


def test_subset_and_superset_are_baseline_to_candidate_relations() -> None:
    small = JsonObject(members=(JsonObjectMember(name="id", value=JsonInteger(value=1)),))
    large = JsonObject(
        members=(
            JsonObjectMember(name="id", value=JsonInteger(value=1)),
            JsonObjectMember(name="name", value=JsonString(value="Ada")),
        )
    )
    for kind, baseline, candidate in (
        (CompatibilityKind.SUBSET, small, large),
        (CompatibilityKind.SUPERSET, large, small),
    ):
        comparator = CompatibilityComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, kind.value),
            kind=kind,
        )
        outcome, _ = compare_unit(
            PairUnit(
                baseline=derived(baseline),
                candidate=derived(candidate, candidate=True),
            ),
            comparator,
            inputs(rule()),
            meter(),
        )
        assert outcome is DiffFactOutcome.SAME


@pytest.mark.parametrize(
    ("kind", "candidate", "same"),
    [
        (CollectionComparisonKind.ORDERED, (2, 1), False),
        (CollectionComparisonKind.SET, (2, 1, 1), True),
        (CollectionComparisonKind.MULTISET, (2, 1, 1), False),
    ],
)
def test_collection_comparators_make_order_and_multiplicity_explicit(
    kind: CollectionComparisonKind,
    candidate: tuple[int, ...],
    same: bool,
) -> None:
    comparator = CollectionComparator(
        identity=_operation(DiffOperationFamily.COMPARATOR, kind.value),
        kind=kind,
    )
    outcome, _ = compare_unit(
        PairUnit(
            baseline=derived(JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2)))),
            candidate=derived(
                JsonArray(items=tuple(JsonInteger(value=value) for value in candidate)),
                candidate=True,
            ),
        ),
        comparator,
        inputs(rule()),
        meter(),
    )
    assert (outcome is DiffFactOutcome.SAME) is same


def test_numeric_tolerance_supports_absolute_and_relative_values() -> None:
    absolute = LiteralExpr(value=JsonInteger(value=2), provenance=_literal().provenance)
    relative = LiteralExpr(
        value=JsonDecimal(value=Decimal("0.1")), provenance=_literal().provenance
    )
    comparator = NumericToleranceComparator(
        identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
        absolute_tolerance=absolute,
        relative_tolerance=relative,
    )
    prepared = PreparedDiffInputs(
        rule_fingerprint=rule().fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        shared_expressions=(
            PreparedExpression(
                expression=absolute,
                result=PreparedOperandReady(value=JsonInteger(value=1)),
            ),
            PreparedExpression(
                expression=relative,
                result=PreparedOperandReady(value=JsonDecimal(value=Decimal("0.1"))),
            ),
        ),
    )
    outcome, _ = compare_unit(
        PairUnit(
            baseline=derived(JsonInteger(value=100)),
            candidate=derived(JsonInteger(value=109), candidate=True),
        ),
        comparator,
        prepared,
        meter(),
    )
    assert outcome is DiffFactOutcome.SAME
