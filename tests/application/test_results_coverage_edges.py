"""Boundary coverage for result references and scalar presentation."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.application.results import ResultEntity
from comparo.application.results import ResultReference
from comparo.application.results import result_value_preview
from comparo.core.artifacts import ArtifactArray
from comparo.core.artifacts import ArtifactBoolean
from comparo.core.artifacts import ArtifactDecimal
from comparo.core.artifacts import ArtifactInteger
from comparo.core.artifacts import ArtifactNull
from comparo.core.artifacts import ArtifactObject
from comparo.core.artifacts import ArtifactObjectMember
from comparo.core.artifacts import ArtifactString
from comparo.core.artifacts.values import ArtifactValue


def test_result_reference_rejects_an_untyped_entity() -> None:
    with pytest.raises(TypeError, match="entity must be typed"):
        ResultReference(entity=cast("ResultEntity", "execution"), result_id="execution-1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ArtifactNull(), "null"),
        (ArtifactBoolean(value=True), "true"),
        (ArtifactBoolean(value=False), "false"),
        (ArtifactInteger(value=1234), "1,234"),
        (ArtifactInteger(value=-1234), "-1,234"),
        (ArtifactInteger(value=1 << 100), "≥2^100 (exact integer retained)"),
        (ArtifactDecimal(value="1.25"), "1.25"),
        (ArtifactString(value="hello"), "hello"),
        (ArtifactArray(items=(ArtifactNull(),)), "array · 1 items"),
        (
            ArtifactObject(members=(ArtifactObjectMember(name="key", value=ArtifactNull()),)),
            "object · 1 members",
        ),
    ],
)
def test_result_value_preview_renders_each_exact_value_kind(
    value: ArtifactValue,
    expected: str,
) -> None:
    assert result_value_preview(value, maximum_characters=100) == expected


def test_result_value_preview_enforces_and_applies_its_character_budget() -> None:
    for maximum in (0, -1, cast("int", True)):
        with pytest.raises(ValueError, match="positive integer"):
            result_value_preview(ArtifactNull(), maximum_characters=maximum)

    value = ArtifactString(value="abcdef")
    assert result_value_preview(value, maximum_characters=1) == "…"
    assert result_value_preview(value, maximum_characters=4) == "abc…"


def test_result_reference_accepts_a_typed_entity() -> None:
    reference = ResultReference(entity=ResultEntity.EXECUTION, result_id="execution-1")
    assert reference.entity is ResultEntity.EXECUTION
