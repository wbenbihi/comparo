"""Deterministic directed pairing over independently derived values."""

from __future__ import annotations

from collections import defaultdict

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.item_selection import select_item
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import PairUnit
from comparo.core.diffs.value_support import canonical_key
from comparo.core.diffs.value_support import reveal
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.strategies import DiffPairing
from comparo.core.domain.diffs.strategies import DuplicateKeyMode
from comparo.core.domain.diffs.strategies import KeyedPairing
from comparo.core.domain.diffs.strategies import OrderedPairing
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.diffs.strategies import UnorderedPairing
from comparo.core.domain.diffs.strategies import UnorderedPairingKind

_DUPLICATE = DiagnosticCode(value="diff.pairing.duplicate-key")


def pair_values(
    baseline: tuple[DerivedValue, ...],
    candidate: tuple[DerivedValue, ...],
    strategy: DiffPairing,
    meter: DiffMeter,
) -> tuple[PairUnit, ...]:
    """Pair in stable strategy-defined order, charging before every unit append."""
    output: list[PairUnit] = []
    if type(strategy) is SingularPairing:
        _singular(baseline, candidate, meter, output)
    elif type(strategy) is OrderedPairing:
        _ordered(baseline, candidate, meter, output)
    elif type(strategy) is KeyedPairing:
        try:
            _keyed(baseline, candidate, strategy, meter, output)
        except DiffHaltError as halted:
            if not halted.partial_pairs:
                halted.partial_pairs = tuple(output)
            raise
    elif type(strategy) is UnorderedPairing:
        try:
            _unordered(baseline, candidate, strategy.kind, meter, output)
        except DiffHaltError as halted:
            if not halted.partial_pairs:
                halted.partial_pairs = tuple(output)
            raise
    else:
        raise TypeError("unsupported diff pairing strategy")
    return tuple(output)


def _singular(
    baseline: tuple[DerivedValue, ...],
    candidate: tuple[DerivedValue, ...],
    meter: DiffMeter,
    output: list[PairUnit],
) -> None:
    if len(baseline) > 1 or len(candidate) > 1:
        raise DiffHaltError(code=DiagnosticCode(value="diff.pairing.singular-cardinality"))
    if baseline or candidate:
        _append(
            output,
            meter,
            baseline=baseline[0] if baseline else None,
            candidate=candidate[0] if candidate else None,
        )


def _ordered(
    baseline: tuple[DerivedValue, ...],
    candidate: tuple[DerivedValue, ...],
    meter: DiffMeter,
    output: list[PairUnit],
) -> None:
    for index in range(max(len(baseline), len(candidate))):
        _append(
            output,
            meter,
            baseline=baseline[index] if index < len(baseline) else None,
            candidate=candidate[index] if index < len(candidate) else None,
        )


def _keyed(
    baseline: tuple[DerivedValue, ...],
    candidate: tuple[DerivedValue, ...],
    strategy: KeyedPairing,
    meter: DiffMeter,
    output: list[PairUnit],
) -> None:
    left = _key_groups(baseline, strategy.baseline_key, strategy, meter)
    right = _key_groups(candidate, strategy.candidate_key, strategy, meter)
    duplicates = {key for key, values in left.items() if len(values) > 1} | {
        key for key, values in right.items() if len(values) > 1
    }
    if duplicates and strategy.duplicate_keys is DuplicateKeyMode.ERROR:
        raise DiffHaltError(code=_DUPLICATE)
    handled: set[tuple[object, ...]] = set()
    for value in baseline:
        key = _single_key(value, strategy.baseline_key, strategy, meter)
        if key in handled:
            continue
        handled.add(key)
        if key in duplicates:
            for item in left[key]:
                _append(output, meter, baseline=item, incomparable=_DUPLICATE)
            for item in right.get(key, ()):
                _append(output, meter, candidate=item, incomparable=_DUPLICATE)
        else:
            _append(
                output,
                meter,
                baseline=value,
                candidate=right.get(key, (None,))[0],
            )
    for value in candidate:
        key = _single_key(value, strategy.candidate_key, strategy, meter)
        if key not in handled:
            handled.add(key)
            _append_unhandled_candidate(output, meter, value, right, key, duplicates)


def _append_unhandled_candidate(
    output: list[PairUnit],
    meter: DiffMeter,
    value: DerivedValue,
    right: dict[tuple[object, ...], tuple[DerivedValue, ...]],
    key: tuple[object, ...],
    duplicates: set[tuple[object, ...]],
) -> None:
    if key not in duplicates:
        _append(output, meter, candidate=value)
        return
    for item in right[key]:
        _append(output, meter, candidate=item, incomparable=_DUPLICATE)


def _key_groups(
    values: tuple[DerivedValue, ...],
    selector: object,
    strategy: KeyedPairing,
    meter: DiffMeter,
) -> dict[tuple[object, ...], tuple[DerivedValue, ...]]:
    grouped: dict[tuple[object, ...], list[DerivedValue]] = defaultdict(list)
    for value in values:
        grouped[_single_key(value, selector, strategy, meter)].append(value)
    return {key: tuple(items) for key, items in grouped.items()}


def _single_key(
    value: DerivedValue,
    selector: object,
    strategy: KeyedPairing,
    meter: DiffMeter,
) -> tuple[object, ...]:
    selected = select_item(value, selector, strategy.identity, meter)  # type: ignore[arg-type]
    if len(selected) != 1:
        raise DiffHaltError(code=DiagnosticCode(value="diff.pairing.key-cardinality"))
    return canonical_key(reveal(selected[0].value), meter)


def _unordered(  # noqa: C901
    baseline: tuple[DerivedValue, ...],
    candidate: tuple[DerivedValue, ...],
    kind: UnorderedPairingKind,
    meter: DiffMeter,
    output: list[PairUnit],
) -> None:
    left = _value_groups(baseline, meter)
    right = _value_groups(candidate, meter)
    handled: set[tuple[object, ...]] = set()
    for value in baseline:
        key = canonical_key(reveal(value.value), meter)
        if key in handled:
            continue
        handled.add(key)
        left_items, right_items = left[key], right.get(key, ())
        if kind is UnorderedPairingKind.SET:
            _append(
                output,
                meter,
                baseline=left_items[0],
                candidate=right_items[0] if right_items else None,
            )
            for item in left_items[1:]:
                _append(output, meter, baseline=item, ignored=True)
            for item in right_items[1:]:
                _append(output, meter, candidate=item, ignored=True)
            continue
        for index in range(max(len(left_items), len(right_items))):
            _append(
                output,
                meter,
                baseline=left_items[index] if index < len(left_items) else None,
                candidate=right_items[index] if index < len(right_items) else None,
            )
    for value in candidate:
        key = canonical_key(reveal(value.value), meter)
        if key in handled:
            continue
        handled.add(key)
        right_items = right[key]
        _append(output, meter, candidate=right_items[0])
        if kind is UnorderedPairingKind.SET:
            for item in right_items[1:]:
                _append(output, meter, candidate=item, ignored=True)
        else:
            for item in right_items[1:]:
                _append(output, meter, candidate=item)


def _value_groups(
    values: tuple[DerivedValue, ...],
    meter: DiffMeter,
) -> dict[tuple[object, ...], tuple[DerivedValue, ...]]:
    groups: dict[tuple[object, ...], list[DerivedValue]] = defaultdict(list)
    for value in values:
        groups[canonical_key(reveal(value.value), meter)].append(value)
    return {key: tuple(items) for key, items in groups.items()}


def _append(
    output: list[PairUnit],
    meter: DiffMeter,
    *,
    baseline: DerivedValue | None = None,
    candidate: DerivedValue | None = None,
    incomparable: DiagnosticCode | None = None,
    ignored: bool = False,
) -> None:
    """Charge a pair slot before constructing or retaining the unit."""
    if (failure := meter.charge_pair()) is not None:
        raise DiffHaltError(exhaustion=failure, partial_pairs=tuple(output))
    output.append(
        PairUnit(
            baseline=baseline,
            candidate=candidate,
            incomparable=incomparable,
            ignored=ignored,
        )
    )


__all__ = ("pair_values",)
