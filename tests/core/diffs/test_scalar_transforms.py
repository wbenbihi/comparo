"""Exercise scalar diff transforms, retention, and type-specific terminal states."""

from __future__ import annotations

from hashlib import sha256
from hashlib import sha512
from typing import cast

import pytest

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.transforms import _digest as _hash_value
from comparo.core.diffs.transforms import _length
from comparo.core.diffs.transforms import _scalar_transform
from comparo.core.diffs.transforms import apply_transforms
from comparo.core.domain.diffs.operations import CaseMode
from comparo.core.domain.diffs.operations import DiffDigestAlgorithm
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffTransform
from comparo.core.domain.diffs.operations import DigestTransform
from comparo.core.domain.diffs.operations import Exclusion
from comparo.core.domain.diffs.operations import JsonShapeArrayMode
from comparo.core.domain.diffs.operations import JsonShapeTransform
from comparo.core.domain.diffs.operations import LengthTransform
from comparo.core.domain.diffs.operations import LengthUnit
from comparo.core.domain.diffs.operations import NewlineMode
from comparo.core.domain.diffs.operations import Projection
from comparo.core.domain.diffs.operations import TextNormalization
from comparo.core.domain.diffs.operations import TrimMode
from comparo.core.domain.diffs.operations import ValueTypeTransform
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.diffs.factories import derived
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import meter
from tests.core.diffs.factories import rule
from tests.core.domain.diffs._invariant_support import _operation

_SIDE = DiffSide.BASELINE


def _projection(pointer: str = "/id") -> Projection:
    return Projection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "project"),
        pointers=(JsonPointer(value=pointer),),
    )


def _exclusion(pointer: str = "/drop") -> Exclusion:
    return Exclusion(
        identity=_operation(DiffOperationFamily.TRANSFORM, "exclude"),
        pointers=(JsonPointer(value=pointer),),
    )


def _normalize(
    trim: TrimMode,
    newlines: NewlineMode,
    case: CaseMode,
) -> TextNormalization:
    return TextNormalization(
        identity=_operation(DiffOperationFamily.TRANSFORM, "normalize-text"),
        trim=trim,
        newlines=newlines,
        case=case,
    )


def _length_transform(unit: LengthUnit) -> LengthTransform:
    return LengthTransform(
        identity=_operation(DiffOperationFamily.TRANSFORM, "length"),
        unit=unit,
    )


def _digest(algorithm: DiffDigestAlgorithm) -> DigestTransform:
    return DigestTransform(
        identity=_operation(DiffOperationFamily.TRANSFORM, "digest"),
        algorithm=algorithm,
    )


def _apply(
    value: SelectedValue,
    *transforms: DiffTransform,
    diff_meter: DiffMeter | None = None,
) -> tuple[DerivedValue, ...]:
    return apply_transforms(
        (derived(value),),
        transforms,
        inputs(rule()),
        meter() if diff_meter is None else diff_meter,
        _SIDE,
    )


def test_no_transform_retains_and_recharges_every_input_value() -> None:
    source = derived(JsonInteger(value=1))
    retained = apply_transforms((source,), (), inputs(rule()), meter(), _SIDE)
    assert len(retained) == 1
    assert retained[0].value == source.value
    assert retained[0].operations == source.operations

    with pytest.raises(DiffHaltError) as caught:
        apply_transforms((source, source), (), inputs(rule()), meter(derived_limit=1), _SIDE)
    assert caught.value.side is _SIDE
    assert len(caught.value.partial_derived) == 1


def test_transform_chain_preserves_order_and_binds_terminal_side() -> None:
    transforms = (
        _normalize(TrimMode.BOTH, NewlineMode.PRESERVE, CaseMode.LOWERCASE_UNICODE),
        _length_transform(LengthUnit.UNICODE_SCALARS),
    )
    assert _apply(JsonString(value=" A "), *transforms)[0].value == JsonInteger(value=1)
    with pytest.raises(DiffHaltError) as caught:
        _apply(JsonInteger(value=1), transforms[0])
    assert caught.value.side is _SIDE


def test_projection_and_exclusion_require_json_and_explicit_non_root_results() -> None:
    value = JsonObject(
        members=(
            JsonObjectMember(name="id", value=JsonInteger(value=1)),
            JsonObjectMember(name="drop", value=JsonInteger(value=2)),
        )
    )
    projected = cast("JsonObject", _apply(value, _projection())[0].value)
    assert tuple(member.name for member in projected.members) == ("id",)
    excluded = cast("JsonObject", _apply(value, _exclusion())[0].value)
    assert tuple(member.name for member in excluded.members) == ("id",)

    for transform, code in (
        (_projection(), "diff.project.type"),
        (_exclusion(), "diff.exclude.type"),
    ):
        with pytest.raises(DiffHaltError) as caught:
            _apply(Count(value=1), transform)
        assert caught.value.code is not None
        assert caught.value.code.value == code
    with pytest.raises(DiffHaltError) as root:
        _apply(value, _exclusion(""))
    assert root.value.code is not None
    assert root.value.code.value == "diff.exclude.root-unsupported"


def test_projection_preflights_the_exact_retained_output_not_the_full_source() -> None:
    value = JsonObject(
        members=(
            JsonObjectMember(name="id", value=JsonInteger(value=1)),
            JsonObjectMember(name="large", value=JsonString(value="x" * 1_000)),
        )
    )
    result = _apply(value, _projection(), diff_meter=meter(derived_bytes=8))
    assert result[0].value == JsonObject(
        members=(JsonObjectMember(name="id", value=JsonInteger(value=1)),)
    )


@pytest.mark.parametrize(
    ("trim", "expected"),
    [
        (TrimMode.NONE, "  StraSSe  "),
        (TrimMode.START, "StraSSe  "),
        (TrimMode.END, "  StraSSe"),
        (TrimMode.BOTH, "StraSSe"),
    ],
)
def test_text_trim_modes_are_explicit(trim: TrimMode, expected: str) -> None:
    result = _apply(
        JsonString(value="  StraSSe  "),
        _normalize(trim, NewlineMode.PRESERVE, CaseMode.PRESERVE),
    )[0]
    assert result.value == JsonString(value=expected)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (CaseMode.PRESERVE, "Straße"),
        (CaseMode.LOWERCASE_UNICODE, "straße"),
        (CaseMode.CASEFOLD_UNICODE, "strasse"),
    ],
)
def test_text_case_modes_are_unicode_defined(case: CaseMode, expected: str) -> None:
    result = _apply(
        JsonString(value="Straße"),
        _normalize(TrimMode.NONE, NewlineMode.PRESERVE, case),
    )[0]
    assert result.value == JsonString(value=expected)


def test_newline_lf_mode_normalizes_crlf_and_cr() -> None:
    result = _apply(
        JsonString(value="a\r\nb\rc"),
        _normalize(TrimMode.NONE, NewlineMode.LF, CaseMode.PRESERVE),
    )[0]
    assert result.value == JsonString(value="a\nb\nc")


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (JsonString(value="aé"), LengthUnit.UNICODE_SCALARS, 2),
        (BytesValue(value=b"abc"), LengthUnit.BYTES, 3),
        (JsonArray(items=(JsonInteger(value=1),)), LengthUnit.ITEMS, 1),
        (
            JsonObject(members=(JsonObjectMember(name="x", value=JsonInteger(value=1)),)),
            LengthUnit.MEMBERS,
            1,
        ),
    ],
)
def test_length_units_are_type_specific(
    value: SelectedValue,
    unit: LengthUnit,
    expected: int,
) -> None:
    assert _apply(value, _length_transform(unit))[0].value == JsonInteger(value=expected)


def test_length_rejects_mismatched_value_and_unit() -> None:
    with pytest.raises(DiffHaltError) as caught:
        _apply(JsonInteger(value=1), _length_transform(LengthUnit.ITEMS))
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.length.type"


def test_value_type_uses_builtin_names_and_closed_selected_value_fallback() -> None:
    transform = ValueTypeTransform(identity=_operation(DiffOperationFamily.TRANSFORM, "value-type"))
    assert _apply(JsonInteger(value=1), transform)[0].value == JsonString(value="integer")
    assert _apply(Count(value=1), transform)[0].value == JsonString(value="count")


def test_json_shape_requires_json_and_delegates_array_mode() -> None:
    transform = JsonShapeTransform(
        identity=_operation(DiffOperationFamily.TRANSFORM, "json-shape"),
        arrays=JsonShapeArrayMode.UNION,
    )
    result = _apply(JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))), transform)[0]
    assert type(result.value) is JsonObject
    with pytest.raises(DiffHaltError) as caught:
        _apply(BytesValue(value=b"x"), transform)
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.shape.type"


def test_digest_supports_text_and_bytes_with_both_algorithms() -> None:
    assert _apply(JsonString(value="abc"), _digest(DiffDigestAlgorithm.SHA256))[0].value == (
        BytesValue(value=sha256(b"abc").digest())
    )
    assert _apply(BytesValue(value=b"abc"), _digest(DiffDigestAlgorithm.SHA512))[0].value == (
        BytesValue(value=sha512(b"abc").digest())
    )
    with pytest.raises(DiffHaltError) as caught:
        _apply(JsonInteger(value=1), _digest(DiffDigestAlgorithm.SHA256))
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.digest.type"


def test_scalar_dispatch_rejects_forged_transform_and_preserves_partial_output() -> None:
    with pytest.raises(TypeError, match="unsupported scalar"):
        _scalar_transform(
            derived(JsonInteger(value=1)),
            cast("ValueTypeTransform", object()),
            meter(),
        )
    transform = ValueTypeTransform(identity=_operation(DiffOperationFamily.TRANSFORM, "value-type"))
    values = (derived(JsonInteger(value=1)), derived(JsonInteger(value=2)))
    with pytest.raises(DiffHaltError) as caught:
        apply_transforms(values, (transform,), inputs(rule()), meter(derived_limit=1), _SIDE)
    assert len(caught.value.partial_derived) == 1
    assert caught.value.side is _SIDE


def test_length_and_digest_helpers_reject_cross_type_calls() -> None:
    assert _length(JsonString(value="x"), LengthUnit.UNICODE_SCALARS) == 1
    with pytest.raises(DiffHaltError):
        _length(JsonString(value="x"), LengthUnit.BYTES)
    assert (
        _hash_value(BytesValue(value=b"x"), DiffDigestAlgorithm.SHA256, meter())
        == sha256(b"x").digest()
    )
    assert (
        _hash_value(JsonString(value="x"), DiffDigestAlgorithm.SHA256, meter())
        == sha256(b"x").digest()
    )
    with pytest.raises(DiffHaltError):
        _hash_value(JsonInteger(value=1), DiffDigestAlgorithm.SHA256, meter())
