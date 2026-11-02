"""Focused boundaries for external-analysis budget validation."""

from typing import cast

import pytest

from comparo.core.domain.analysis_budget_validation import validate_analysis_budgets
from comparo.core.domain.primitives import ByteCount
from tests.core.domain.analysis_factories import _request


def test_analysis_budget_input_extents_require_exact_immutable_units() -> None:
    request = _request()
    with pytest.raises(TypeError, match="immutable tuple of ByteCount"):
        validate_analysis_budgets(
            request.budgets,
            analysis_id=request.analysis_id,
            input_bytes=cast("tuple[ByteCount, ...]", [ByteCount(value=12)]),
            timeout=request.timeout,
        )
    with pytest.raises(TypeError, match="immutable tuple of ByteCount"):
        validate_analysis_budgets(
            request.budgets,
            analysis_id=request.analysis_id,
            input_bytes=cast("tuple[ByteCount, ...]", (object(),)),
            timeout=request.timeout,
        )
