"""Cover private diff runtime lineage, pair, and typed halt invariants."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import PairUnit
from comparo.core.diffs.runtime import merged_origins
from comparo.core.diffs.value_support import merge_taint_origins
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from tests.core.diffs.factories import derived
from tests.core.diffs.factories import meter
from tests.core.domain.diffs._invariant_support import _operation

_CODE = DiagnosticCode(value="diff.runtime.test")


def _exhaustion() -> BudgetExceeded:
    failure = meter(derived_limit=0).charge_derived(1)
    assert failure is not None
    return failure


def test_derived_value_accepts_public_sensitive_and_originless_reducer_values() -> None:
    source = derived(JsonInteger(value=1))
    assert source.taint_origins == ()
    origin = TaintOriginId(value="diff-runtime-secret")
    sensitive = msgspec.structs.replace(
        source,
        value=SensitiveValue(JsonInteger(value=1), origins=(origin,)),
    )
    assert sensitive.taint_origins == (origin,)

    identity = _operation(DiffOperationFamily.REDUCER, "count")
    empty = DerivedValue(
        value=JsonInteger(value=0),
        selector=source.selector,
        origins=(),
        operations=(identity,),
        empty_reducer_identity=True,
    )
    assert empty.empty_reducer_identity


def test_derived_value_rejects_open_value_selector_and_lineage_variants() -> None:
    source = derived(JsonInteger(value=1))
    with pytest.raises(TypeError, match="unsupported payload"):
        msgspec.structs.replace(source, value=object())
    invalid_sensitive = SensitiveValue(
        "not-a-selected-value",
        origins=(TaintOriginId(value="diff-runtime-secret"),),
    )
    with pytest.raises(TypeError, match=r"sensitive.*unsupported payload"):
        msgspec.structs.replace(source, value=cast("object", invalid_sensitive))
    with pytest.raises(TypeError, match="CompiledSelector"):
        msgspec.structs.replace(
            source,
            selector=cast("CompiledSelector", object()),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        msgspec.structs.replace(source, origins=cast("tuple[object, ...]", []))
    with pytest.raises(TypeError, match="concrete locations"):
        msgspec.structs.replace(source, origins=(object(),))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(source, origins=(source.origins[0], source.origins[0]))
    with pytest.raises(TypeError, match="operation trace"):
        msgspec.structs.replace(source, operations=cast("tuple[object, ...]", []))
    with pytest.raises(TypeError, match="operation trace"):
        msgspec.structs.replace(source, operations=(object(),))
    with pytest.raises(TypeError, match="marker"):
        msgspec.structs.replace(source, empty_reducer_identity=cast("bool", 1))


def test_derived_value_requires_exact_originless_reducer_identity_relation() -> None:
    source = derived(JsonInteger(value=1))
    with pytest.raises(ValueError, match="exactly originless"):
        msgspec.structs.replace(source, origins=())
    with pytest.raises(ValueError, match="exactly originless"):
        msgspec.structs.replace(source, empty_reducer_identity=True)
    with pytest.raises(ValueError, match="terminal reducer"):
        DerivedValue(
            value=source.value,
            selector=source.selector,
            origins=(),
            operations=(_operation(DiffOperationFamily.TRANSFORM, "value-type"),),
            empty_reducer_identity=True,
        )


def test_pair_unit_requires_one_typed_side_and_exclusive_terminal_markers() -> None:
    left = derived(JsonInteger(value=1))
    right = derived(JsonInteger(value=2), candidate=True)
    assert PairUnit(baseline=left, candidate=right).baseline is left
    with pytest.raises(TypeError, match="baseline"):
        PairUnit(baseline=cast("DerivedValue", object()))
    with pytest.raises(TypeError, match="candidate"):
        PairUnit(candidate=cast("DerivedValue", object()))
    with pytest.raises(ValueError, match="at least one"):
        PairUnit()
    with pytest.raises(TypeError, match="DiagnosticCode"):
        PairUnit(baseline=left, incomparable=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError, match="ignored marker"):
        PairUnit(baseline=left, ignored=cast("bool", 1))
    with pytest.raises(ValueError, match="both ignored and incomparable"):
        PairUnit(baseline=left, incomparable=_CODE, ignored=True)


def test_diff_halt_requires_one_typed_cause_and_valid_expected_incomplete_state() -> None:
    exhaustion = _exhaustion()
    with pytest.raises(ValueError, match="exactly"):
        DiffHaltError()
    with pytest.raises(ValueError, match="exactly"):
        DiffHaltError(code=_CODE, exhaustion=exhaustion)
    with pytest.raises(TypeError, match="cause must be typed"):
        DiffHaltError(
            code=_CODE,
            incomplete_cause=cast("DiffIncompleteCause", "unavailable"),
        )
    with pytest.raises(ValueError, match="diagnostic code"):
        DiffHaltError(
            exhaustion=exhaustion,
            incomplete_cause=DiffIncompleteCause.UNAVAILABLE,
        )
    with pytest.raises(ValueError, match="exact exhaustion"):
        DiffHaltError(
            code=_CODE,
            incomplete_cause=DiffIncompleteCause.BUDGET_EXCEEDED,
        )

    expected = DiffHaltError(
        code=_CODE,
        incomplete_cause=DiffIncompleteCause.UNAVAILABLE,
    )
    assert expected.code == _CODE
    assert expected.incomplete_cause is DiffIncompleteCause.UNAVAILABLE
    budget = DiffHaltError(exhaustion=exhaustion)
    assert budget.exhaustion is exhaustion


def test_diff_halt_rejects_untyped_partial_progress_and_side() -> None:
    value = derived(JsonInteger(value=1))
    pair = PairUnit(baseline=value)
    with pytest.raises(TypeError, match="partial derived"):
        DiffHaltError(code=_CODE, partial_derived=cast("tuple[DerivedValue, ...]", []))
    with pytest.raises(TypeError, match="partial derived"):
        DiffHaltError(code=_CODE, partial_derived=(cast("DerivedValue", object()),))
    with pytest.raises(TypeError, match="partial pair"):
        DiffHaltError(code=_CODE, partial_pairs=cast("tuple[PairUnit, ...]", [pair]))
    with pytest.raises(TypeError, match="partial pair"):
        DiffHaltError(code=_CODE, partial_pairs=(cast("PairUnit", object()),))
    with pytest.raises(TypeError, match="side"):
        DiffHaltError(code=_CODE, side=cast("DiffSide", "both"))


def test_diff_halt_prepend_preserves_cause_and_orders_partial_progress() -> None:
    first = derived(JsonInteger(value=1), index=501)
    second = derived(JsonInteger(value=2), index=502)
    halted = DiffHaltError(code=_CODE, partial_derived=(second,))
    halted.prepend_derived((first,), DiffSide.CANDIDATE)
    assert halted.partial_derived == (first, second)
    assert halted.partial_pairs == ()
    assert halted.side is DiffSide.CANDIDATE
    assert halted.code == _CODE


def test_lineage_merges_preserve_stable_first_seen_uniqueness() -> None:
    first = derived(JsonInteger(value=1), index=503)
    second = derived(JsonInteger(value=2), index=504)
    combined = msgspec.structs.replace(first, origins=(*first.origins, *second.origins))
    assert merged_origins(first, combined, second) == (*first.origins, *second.origins)

    first_origin = TaintOriginId(value="diff-runtime-first")
    second_origin = TaintOriginId(value="diff-runtime-second")
    first_sensitive = msgspec.structs.replace(
        first,
        value=SensitiveValue(JsonInteger(value=1), origins=(first_origin,)),
    )
    combined_sensitive = msgspec.structs.replace(
        combined,
        value=SensitiveValue(
            JsonInteger(value=1),
            origins=(first_origin, second_origin),
        ),
    )
    assert merge_taint_origins(
        meter(),
        first.taint_origins,
        first_sensitive.taint_origins,
        combined_sensitive.taint_origins,
    ) == (
        first_origin,
        second_origin,
    )
