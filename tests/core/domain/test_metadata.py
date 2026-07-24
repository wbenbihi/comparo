"""Tests for human-facing metadata."""

import pytest

from comparo.core.domain.metadata import Metadata


def test_metadata_accepts_absent_text_and_ordered_tags() -> None:
    metadata = Metadata(tags=("smoke", "ci"))
    assert metadata.name is None
    assert metadata.tags == ("smoke", "ci")


@pytest.mark.parametrize("field", ["name", "description"])
def test_metadata_rejects_blank_optional_text(field: str) -> None:
    with pytest.raises(ValueError, match="absent or non-blank"):
        Metadata(**{field: " "})  # type: ignore[arg-type]


def test_metadata_rejects_mutable_blank_and_duplicate_tags() -> None:
    with pytest.raises(TypeError):
        Metadata(tags=["smoke"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-blank text"):
        Metadata(tags=("",))
    with pytest.raises(ValueError, match="unique"):
        Metadata(tags=("smoke", "smoke"))
