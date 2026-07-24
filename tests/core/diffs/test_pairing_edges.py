"""Cover pairing dispatch, conservation, and partial-budget terminal paths."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.diffs.pairing import _single_key
from comparo.core.diffs.pairing import pair_values
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.strategies import DuplicateKeyMode
from comparo.core.domain.diffs.strategies import KeyedPairing
from comparo.core.domain.diffs.strategies import OrderedPairing
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.diffs.strategies import UnorderedPairing
from comparo.core.domain.diffs.strategies import UnorderedPairingKind
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from tests.core.diffs.factories import derived
from tests.core.diffs.factories import meter
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _optional_item_selector


def _singular() -> SingularPairing:
    return SingularPairing(identity=_operation(DiffOperationFamily.PAIRING, "singular"))


def _ordered() -> OrderedPairing:
    return OrderedPairing(identity=_operation(DiffOperationFamily.PAIRING, "ordered"))


def _keyed() -> KeyedPairing:
    return KeyedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
        baseline_key=_item_selector(),
        candidate_key=_item_selector(),
        duplicate_keys=DuplicateKeyMode.INCOMPARABLE,
    )


def _unordered(kind: UnorderedPairingKind) -> UnorderedPairing:
    return UnorderedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, kind.value),
        kind=kind,
    )


def test_pairing_dispatch_rejects_unknown_strategies_and_singular_empty_is_empty() -> None:
    assert pair_values((), (), _singular(), meter()) == ()
    with pytest.raises(TypeError, match="unsupported"):
        pair_values((), (), cast("SingularPairing", object()), meter())


def test_keyed_budget_failure_preserves_empty_and_nonempty_partial_pairs() -> None:
    one = (derived(JsonString(value="a"), index=201),)
    with pytest.raises(DiffHaltError) as empty:
        pair_values(one, (), _keyed(), meter(pairs=0))
    assert empty.value.partial_pairs == ()

    two = (
        derived(JsonString(value="a"), index=202),
        derived(JsonString(value="b"), index=203),
    )
    with pytest.raises(DiffHaltError) as partial:
        pair_values(two, (), _keyed(), meter(pairs=1))
    assert len(partial.value.partial_pairs) == 1


def test_unordered_budget_failure_preserves_empty_and_nonempty_partial_pairs() -> None:
    one = (derived(JsonString(value="a"), index=204),)
    strategy = _unordered(UnorderedPairingKind.MULTISET)
    with pytest.raises(DiffHaltError) as empty:
        pair_values(one, (), strategy, meter(pairs=0))
    assert empty.value.partial_pairs == ()

    two = (
        derived(JsonString(value="a"), index=205),
        derived(JsonString(value="b"), index=206),
    )
    with pytest.raises(DiffHaltError) as partial:
        pair_values(two, (), strategy, meter(pairs=1))
    assert len(partial.value.partial_pairs) == 1


def test_keyed_pairing_handles_left_only_duplicate_and_candidate_only_keys() -> None:
    duplicate = (
        derived(JsonString(value="a"), index=207),
        derived(JsonString(value="a"), index=208),
    )
    pairs = pair_values(duplicate, (), _keyed(), meter())
    assert len(pairs) == 2
    assert all(pair.incomparable is not None for pair in pairs)

    candidate = (derived(JsonString(value="new"), candidate=True, index=209),)
    pairs = pair_values((), candidate, _keyed(), meter())
    assert len(pairs) == 1
    assert pairs[0].baseline is None


def test_key_selector_requires_exactly_one_derived_key() -> None:
    value = derived(JsonObject(members=()), index=210)
    with pytest.raises(DiffHaltError) as caught:
        _single_key(value, _optional_item_selector(), _keyed(), meter())
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.pairing.key-cardinality"


def test_unordered_set_ignores_extra_right_duplicates_with_and_without_left_key() -> None:
    left = (derived(JsonString(value="x"), index=211),)
    matched_right = (
        derived(JsonString(value="x"), candidate=True, index=212),
        derived(JsonString(value="x"), candidate=True, index=213),
    )
    pairs = pair_values(left, matched_right, _unordered(UnorderedPairingKind.SET), meter())
    assert len(pairs) == 2
    assert pairs[1].ignored

    right_only = (
        derived(JsonString(value="y"), candidate=True, index=214),
        derived(JsonString(value="y"), candidate=True, index=215),
    )
    pairs = pair_values((), right_only, _unordered(UnorderedPairingKind.SET), meter())
    assert len(pairs) == 2
    assert pairs[1].ignored


def test_unordered_multiset_preserves_every_candidate_only_duplicate() -> None:
    right = (
        derived(JsonInteger(value=1), candidate=True, index=216),
        derived(JsonInteger(value=1), candidate=True, index=217),
    )
    pairs = pair_values((), right, _unordered(UnorderedPairingKind.MULTISET), meter())
    assert len(pairs) == 2
    assert all(pair.baseline is None and not pair.ignored for pair in pairs)


def test_ordered_pairing_accepts_two_empty_sides() -> None:
    assert pair_values((), (), _ordered(), meter()) == ()
