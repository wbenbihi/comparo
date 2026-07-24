"""Exercise deterministic sizing for every non-JSON selected-value family."""

from __future__ import annotations

from uuid import UUID

import pytest

from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.values import BytesValue
from comparo.core.selection.value_size import selected_value_size
from tests.core.selection.json_evaluator_factories import selection_meter


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (BytesValue(value=b"abc"), 3),
        (HttpFieldTextValue(value="é"), 2),
        (BareQueryFieldValue(), 0),
        (
            HttpFieldItems(items=(MediaTypeTokenValue(value="x"), MediaTypeTokenValue(value="yz"))),
            4,
        ),
        (Count(value=123), 3),
        (ByteCount(value=456), 3),
        (Duration(nanoseconds=789), 3),
        (Ratio(numerator=1, denominator=PositiveCount(value=2)), 3),
        (UtcInstant(unix_nanoseconds=-1), 2),
        (BodyId(value=UUID(int=1)), 36),
        (ViewId(value=UUID(int=2)), 36),
        (
            MediaType(
                type="text",
                subtype="plain",
                parameters=(
                    MediaTypeParameter(
                        name="charset",
                        value=MediaTypeQuotedValue(value='a b"'),
                    ),
                ),
            ),
            27,
        ),
    ],
)
def test_selected_value_size_uses_one_canonical_payload_representation(
    value: SelectedValue,
    expected: int,
) -> None:
    assert selected_value_size(value, selection_meter()) == ByteCount(value=expected)
