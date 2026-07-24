"""Adversarial containment tests for nested SSE raw-field provenance."""

from __future__ import annotations

import pytest
from tests.core.domain.http.stream_factories import location

from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.stream_items import ByteRange
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseFieldName
from comparo.core.domain.http.stream_items import SseFieldOccurrence
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount


def _field(
    start: int,
    end: int,
    line: int,
    *,
    name: SseFieldName = SseFieldName.DATA,
) -> SseFieldOccurrence:
    return SseFieldOccurrence(
        name=name,
        byte_range=ByteRange(
            start=ByteCount(value=start),
            end_exclusive=ByteCount(value=end),
        ),
        line=PositiveCount(value=line),
    )


def _event(*fields: SseFieldOccurrence) -> SseEvent:
    return SseEvent(
        data=BodyText(value="data"),
        event_type=BodyText(value="message"),
        last_event_id=BodyText(value="id"),
        fields=fields,
        location=location(start=0, end=12),
    )


def test_sse_raw_fields_must_fit_their_exact_event_extent() -> None:
    with pytest.raises(ValueError, match="fit its event location"):
        SseEvent(
            data=BodyText(value="data"),
            event_type=BodyText(value="message"),
            last_event_id=BodyText(value="id"),
            fields=(_field(100, 104, 1),),
            location=location(start=0, end=4),
        )
    with pytest.raises(ValueError, match="fit its event location"):
        _event(_field(0, 13, 1))
    with pytest.raises(ValueError, match="non-empty"):
        _event(_field(2, 2, 1))


def test_sse_raw_fields_are_byte_ordered_non_overlapping_and_line_ordered() -> None:
    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        _event(_field(4, 8, 1), _field(2, 3, 2))
    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        _event(_field(0, 5, 1), _field(4, 8, 2))
    with pytest.raises(ValueError, match="strictly increasing"):
        _event(_field(0, 3, 1), _field(4, 8, 1))
    with pytest.raises(ValueError, match="strictly increasing"):
        _event(_field(0, 3, 2), _field(4, 8, 1))


def test_sse_field_lines_cannot_precede_event_and_valid_repetitions_are_retained() -> None:
    with pytest.raises(ValueError, match="cannot precede"):
        SseEvent(
            data=BodyText(value="data"),
            event_type=BodyText(value="message"),
            last_event_id=BodyText(value="id"),
            fields=(_field(1, 4, 1),),
            location=location(item=2, start=0, end=8),
        )

    repeated_data = _event(
        _field(0, 3, 1),
        _field(4, 8, 2),
        _field(9, 12, 3, name=SseFieldName.ID),
    )
    assert tuple(field.name for field in repeated_data.fields) == (
        SseFieldName.DATA,
        SseFieldName.DATA,
        SseFieldName.ID,
    )
