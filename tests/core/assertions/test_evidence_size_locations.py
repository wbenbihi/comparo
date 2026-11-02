"""Cover the closed concrete-location evidence sizing vocabulary."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from comparo.core.assertions.evidence_size import location_size
from comparo.core.assertions.evidence_size import selected_value_size
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import AttemptLocation
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import ExecutionLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import HttpMessageSection
from comparo.core.domain.selectors.locations import IndexedLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.locations import ScenarioLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonDecimal

_EXECUTION = EnvironmentExecutionId(value=UUID(int=1))
_ATTEMPT = AttemptId(value=UUID(int=2))
_EXCHANGE = ExchangeId(value=UUID(int=3))
_VIEW = ViewId(value=UUID(int=4))
_BODY = BodyId(value=UUID(int=5))


@pytest.mark.parametrize(
    "location",
    [
        ExecutionLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.EXECUTION,
            field=StaticFieldPath(segments=("execution",)),
        ),
        AttemptLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.ATTEMPT,
            attempt_id=_ATTEMPT,
            field=StaticFieldPath(segments=("attempt",)),
        ),
        ExchangeLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.RESPONSE_STATUS,
            exchange_id=_EXCHANGE,
            field=StaticFieldPath(segments=("response", "status")),
        ),
        HeaderLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.RESPONSE_INFORMATIONAL,
            exchange_id=_EXCHANGE,
            section=HttpMessageSection.RESPONSE_INFORMATIONAL_HEADER,
            occurrence=Count(value=0),
            name=HeaderName(value="link"),
            informational_response=Count(value=0),
            informational_status=103,
        ),
        HeaderLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.RESPONSE_HEADERS,
            exchange_id=_EXCHANGE,
            section=HttpMessageSection.RESPONSE_HEADER,
            occurrence=Count(value=0),
            name=HeaderName(value="content-type"),
        ),
        NamedFieldLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.REQUEST_QUERY,
            exchange_id=_EXCHANGE,
            name=QueryName(value="page"),
            occurrence=Count(value=0),
        ),
        IndexedLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.RESPONSE_ENCODINGS,
            exchange_id=_EXCHANGE,
            index=Count(value=0),
        ),
        JsonLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.RESPONSE_BODY,
            view_id=_VIEW,
            pointer=JsonPointer(value="/id"),
            body_view=BodyViewKind.JSON,
        ),
        JsonLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.SCENARIO_STEP,
            view_id=_VIEW,
            pointer=JsonPointer(value=""),
        ),
        ViewLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.RESPONSE_BODY,
            view_id=_VIEW,
            body_view=BodyViewKind.TEXT,
        ),
        BodyLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.RESPONSE_BODY,
            body_id=_BODY,
            body_view=BodyViewKind.ENTITY_BYTES,
            start=ByteCount(value=0),
            end=ByteCount(value=4),
        ),
        BodyLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.RESPONSE_BODY,
            body_id=_BODY,
            body_view=BodyViewKind.HANDLE,
        ),
        StreamLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.STREAM_EVENTS,
            view_id=_VIEW,
            connection_segment=Count(value=0),
            item_index=Count(value=1),
            category_index=Count(value=1),
            nested_pointer=JsonPointer(value="/data"),
        ),
        StreamLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.STREAM_CONTROLS,
            view_id=_VIEW,
            connection_segment=Count(value=0),
            item_index=Count(value=1),
            category_index=Count(value=1),
        ),
        ScenarioLocation(
            environment_execution_id=_EXECUTION,
            source=SelectorSource.SCENARIO_STEP,
            step_execution_id=StepExecutionId(value=UUID(int=6)),
            field=StaticFieldPath(segments=("scenario", "step")),
        ),
    ],
)
def test_every_concrete_location_has_a_positive_structural_size(
    location: ConcreteLocation,
) -> None:
    assert location_size(location) > 0


def test_identity_and_token_media_selected_values_are_sized() -> None:
    media = MediaType(
        type="application",
        subtype="json",
        parameters=(
            MediaTypeParameter(
                name="charset",
                value=MediaTypeTokenValue(value="utf-8"),
            ),
        ),
    )

    assert selected_value_size(_BODY) == 16
    assert selected_value_size(_VIEW) == 16
    assert selected_value_size(media) > 0
    assert selected_value_size(JsonDecimal(value=Decimal("1.1"))) > 0
