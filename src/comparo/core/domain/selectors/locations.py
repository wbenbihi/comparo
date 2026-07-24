"""Typed concrete selection coordinates with canonical JSON locations."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource

_EXCHANGE_SOURCES = frozenset(
    {
        SelectorSource.EXCHANGE,
        SelectorSource.REQUEST_METHOD,
        SelectorSource.REQUEST_URL,
        SelectorSource.RESPONSE_PROTOCOL,
        SelectorSource.RESPONSE_STATUS,
        SelectorSource.RESPONSE_MEDIA,
        SelectorSource.RESPONSE_TIMINGS,
        SelectorSource.RESPONSE_ENDPOINT,
        SelectorSource.RESPONSE_TLS,
        SelectorSource.STREAM_TIMINGS,
        SelectorSource.STREAM_TERMINATION,
    }
)
_BODY_SOURCES = frozenset({SelectorSource.REQUEST_BODY, SelectorSource.RESPONSE_BODY})
_NATIVE_STREAM_SOURCES = frozenset(
    {
        SelectorSource.STREAM_EVENTS,
        SelectorSource.STREAM_EVENT_DATA_JSON,
        SelectorSource.STREAM_RECORDS,
        SelectorSource.STREAM_CONTROLS,
    }
)
_BODY_LOCATION_VIEWS = frozenset(
    {
        BodyViewKind.HANDLE,
        BodyViewKind.ENTITY_BYTES,
    }
)
_WHOLE_VIEW_KINDS = frozenset(
    {
        BodyViewKind.TEXT,
        BodyViewKind.JSON,
        BodyViewKind.SSE_EVENTS,
        BodyViewKind.NDJSON_RECORDS,
        BodyViewKind.JSON_SEQUENCE_RECORDS,
        BodyViewKind.AUTO,
    }
)


@final
class ExecutionLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One typed Environment-execution field."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    field: StaticFieldPath

    def __post_init__(self) -> None:
        """Require typed coordinates."""
        _require_owner_source(self.environment_execution_id, self.source)
        if self.source is not SelectorSource.EXECUTION:
            raise ValueError("execution location requires the execution selector source")
        _require_field(self.field)


@final
class AttemptLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One typed transport-attempt field."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    attempt_id: AttemptId
    field: StaticFieldPath

    def __post_init__(self) -> None:
        """Require typed coordinates."""
        _require_owner_source(self.environment_execution_id, self.source)
        if self.source is not SelectorSource.ATTEMPT:
            raise ValueError("attempt location requires the attempt selector source")
        _require_field_location(self.attempt_id, AttemptId, self.field)


@final
class ExchangeLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One typed HTTP-exchange field."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    exchange_id: ExchangeId
    field: StaticFieldPath

    def __post_init__(self) -> None:
        """Require typed coordinates."""
        _require_owner_source(self.environment_execution_id, self.source)
        if self.source not in _EXCHANGE_SOURCES:
            raise ValueError("exchange location source is incompatible with exchange fields")
        _require_field_location(self.exchange_id, ExchangeId, self.field)


class HttpMessageSection(DomainStrEnum):
    """Ordered HTTP multimap sections."""

    REQUEST_HEADER = "request-header"
    RESPONSE_INFORMATIONAL_HEADER = "response-informational-header"
    RESPONSE_HEADER = "response-header"
    RESPONSE_TRAILER = "response-trailer"


@final
class HeaderLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered header/trailer occurrence, never a joined value."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    exchange_id: ExchangeId
    section: HttpMessageSection
    occurrence: Count
    name: HeaderName
    informational_response: Count | None = None
    informational_status: int | None = None

    def __post_init__(self) -> None:
        """Require typed coordinates and a lowercase ASCII field name."""
        _require_owner_source(self.environment_execution_id, self.source)
        if type(self.exchange_id) is not ExchangeId:
            raise TypeError("header location requires an ExchangeId")
        if type(self.section) is not HttpMessageSection:
            raise TypeError("header section must be an HttpMessageSection")
        if type(self.occurrence) is not Count:
            raise TypeError("header occurrence must be a Count")
        if type(self.name) is not HeaderName:
            raise TypeError("header location name must be a HeaderName")
        expected_source = {
            HttpMessageSection.REQUEST_HEADER: SelectorSource.REQUEST_HEADERS,
            HttpMessageSection.RESPONSE_INFORMATIONAL_HEADER: SelectorSource.RESPONSE_INFORMATIONAL,
            HttpMessageSection.RESPONSE_HEADER: SelectorSource.RESPONSE_HEADERS,
            HttpMessageSection.RESPONSE_TRAILER: SelectorSource.RESPONSE_TRAILERS,
        }[self.section]
        if self.source is not expected_source:
            raise ValueError("header location source contradicts its HTTP message section")
        _require_informational_header_coordinates(self)


@final
class NamedFieldLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered request query or cookie occurrence with its exact name."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    exchange_id: ExchangeId
    name: QueryName | CookieName
    occurrence: Count

    def __post_init__(self) -> None:
        """Require an exact named plural-field coordinate."""
        _require_owner_source(self.environment_execution_id, self.source)
        if self.source not in {SelectorSource.REQUEST_QUERY, SelectorSource.REQUEST_COOKIES}:
            raise ValueError("named field location requires a query or cookie selector source")
        if type(self.exchange_id) is not ExchangeId:
            raise TypeError("named field location requires an ExchangeId")
        expected_name_type = (
            QueryName if self.source is SelectorSource.REQUEST_QUERY else CookieName
        )
        if type(self.name) is not expected_name_type:
            raise TypeError("named field location name type contradicts its selector source")
        if type(self.occurrence) is not Count:
            raise TypeError("named field location occurrence must be a Count")


@final
class IndexedLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One exact item in an ordered selector source."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    exchange_id: ExchangeId
    index: Count

    def __post_init__(self) -> None:
        """Require the only initially indexed source and exact occurrence coordinates."""
        _require_owner_source(self.environment_execution_id, self.source)
        if self.source is not SelectorSource.RESPONSE_ENCODINGS:
            raise ValueError("indexed location requires the response-encodings selector source")
        if type(self.exchange_id) is not ExchangeId:
            raise TypeError("indexed location requires an ExchangeId")
        if type(self.index) is not Count:
            raise TypeError("indexed location index must be a Count")


@final
class JsonLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One decoded JSON value addressed by canonical JSON Pointer."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    view_id: ViewId
    pointer: JsonPointer
    body_view: BodyViewKind | None = None

    def __post_init__(self) -> None:
        """Require typed view and pointer coordinates."""
        _require_owner_source(self.environment_execution_id, self.source)
        if type(self.view_id) is not ViewId or type(self.pointer) is not JsonPointer:
            raise TypeError("JSON location requires a ViewId and JsonPointer")
        if self.source in _BODY_SOURCES:
            if self.body_view is not BodyViewKind.JSON:
                raise ValueError("body JSON locations require the explicit JSON body view")
        elif self.source is SelectorSource.SCENARIO_STEP:
            if self.body_view is not None:
                raise ValueError("Scenario JSON locations cannot declare an HTTP body view")
        else:
            raise ValueError("JSON location source is not JSON-view compatible")


@final
class ViewLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One complete decoded body view, distinct from a record inside that view."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    view_id: ViewId
    body_view: BodyViewKind

    def __post_init__(self) -> None:
        """Require an exact body source and whole decoded-view identity."""
        _require_owner_source(self.environment_execution_id, self.source)
        if self.source not in _BODY_SOURCES:
            raise ValueError("whole-view location requires a request or response body source")
        if type(self.view_id) is not ViewId:
            raise TypeError("whole-view location requires a ViewId")
        if type(self.body_view) is not BodyViewKind:
            raise TypeError("whole-view location view must be a BodyViewKind")
        if self.body_view not in _WHOLE_VIEW_KINDS:
            raise ValueError("whole-view location requires a decoded body view")


@final
class BodyLocation(msgspec.Struct, frozen=True, kw_only=True):
    """A whole body or non-empty half-open byte range."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    body_id: BodyId
    body_view: BodyViewKind
    start: ByteCount | None = None
    end: ByteCount | None = None

    def __post_init__(self) -> None:
        """Require typed, paired byte-range bounds."""
        _require_owner_source(self.environment_execution_id, self.source)
        if self.source not in _BODY_SOURCES:
            raise ValueError("body location requires a request or response body source")
        if type(self.body_view) is not BodyViewKind:
            raise TypeError("body location view must be a BodyViewKind")
        if self.body_view not in _BODY_LOCATION_VIEWS:
            raise ValueError("body location view requires handle or entity-byte coordinates")
        if type(self.body_id) is not BodyId:
            raise TypeError("body location requires a BodyId")
        if (self.start is None) != (self.end is None):
            raise ValueError("body byte range requires both start and end")
        if self.start is not None and type(self.start) is not ByteCount:
            raise TypeError("body range start must be a ByteCount")
        if self.end is not None and type(self.end) is not ByteCount:
            raise TypeError("body range end must be a ByteCount")
        if self.start is not None and self.end is not None and self.end.value <= self.start.value:
            raise ValueError("body range end must be greater than its start")


@final
class StreamLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One semantic record/event with optional nested JSON provenance."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    view_id: ViewId
    connection_segment: Count
    item_index: Count
    category_index: Count
    nested_pointer: JsonPointer | None = None
    body_view: BodyViewKind | None = None

    def __post_init__(self) -> None:
        """Require exact stream and optional nested coordinates."""
        _require_owner_source(self.environment_execution_id, self.source)
        if type(self.view_id) is not ViewId:
            raise TypeError("stream location requires a ViewId")
        if any(
            type(index) is not Count
            for index in (self.connection_segment, self.item_index, self.category_index)
        ):
            raise TypeError("stream location indices must be Count values")
        if self.nested_pointer is not None and type(self.nested_pointer) is not JsonPointer:
            raise TypeError("nested stream pointer must be a JsonPointer")
        if self.source not in _NATIVE_STREAM_SOURCES:
            raise ValueError("stream location source is not stream compatible")
        if self.nested_pointer is not None and self.source not in {
            SelectorSource.STREAM_EVENTS,
            SelectorSource.STREAM_EVENT_DATA_JSON,
            SelectorSource.STREAM_RECORDS,
        }:
            raise ValueError("only stream event or record locations can identify nested JSON")
        if self.body_view is not None:
            raise ValueError("native stream locations cannot declare an HTTP body view")


@final
class ScenarioLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One typed future Scenario-step field."""

    environment_execution_id: EnvironmentExecutionId
    source: SelectorSource
    step_execution_id: StepExecutionId
    field: StaticFieldPath

    def __post_init__(self) -> None:
        """Require typed coordinates."""
        _require_owner_source(self.environment_execution_id, self.source)
        if self.source is not SelectorSource.SCENARIO_STEP:
            raise ValueError("Scenario location requires the Scenario-step selector source")
        _require_field_location(self.step_execution_id, StepExecutionId, self.field)


type ConcreteLocation = (
    ExecutionLocation
    | AttemptLocation
    | ExchangeLocation
    | HeaderLocation
    | NamedFieldLocation
    | IndexedLocation
    | JsonLocation
    | ViewLocation
    | BodyLocation
    | StreamLocation
    | ScenarioLocation
)


def is_concrete_location(value: object) -> TypeGuard[ConcreteLocation]:
    """Narrow a value to the closed concrete-location union."""
    return type(value) in {
        ExecutionLocation,
        AttemptLocation,
        ExchangeLocation,
        HeaderLocation,
        NamedFieldLocation,
        IndexedLocation,
        JsonLocation,
        ViewLocation,
        BodyLocation,
        StreamLocation,
        ScenarioLocation,
    }


def location_environment_execution_id(location: ConcreteLocation) -> EnvironmentExecutionId:
    """Return the exact Environment execution owning one concrete location."""
    if not is_concrete_location(location):
        raise TypeError("location ownership requires a ConcreteLocation")
    return location.environment_execution_id


def location_selector_source(location: ConcreteLocation) -> SelectorSource:
    """Return the exact compiled selector source represented by one location."""
    if not is_concrete_location(location):
        raise TypeError("location source requires a ConcreteLocation")
    return location.source


def location_body_view(location: ConcreteLocation) -> BodyViewKind | None:
    """Return the exact body view represented by one location, when applicable."""
    if not is_concrete_location(location):
        raise TypeError("location body-view lookup requires a ConcreteLocation")
    if type(location) is JsonLocation:
        return location.body_view
    if type(location) is ViewLocation:
        return location.body_view
    if type(location) is BodyLocation:
        return location.body_view
    if type(location) is StreamLocation:
        return location.body_view
    return None


def location_static_field(location: ConcreteLocation) -> StaticFieldPath | None:
    """Return a concrete static field coordinate when the location carries one."""
    if not is_concrete_location(location):
        raise TypeError("location field lookup requires a ConcreteLocation")
    if type(location) is ExecutionLocation:
        return location.field
    if type(location) is AttemptLocation:
        return location.field
    if type(location) is ExchangeLocation:
        return location.field
    if type(location) is ScenarioLocation:
        return location.field
    return None


def _require_field_location(identifier: object, expected: type[object], field: object) -> None:
    if type(identifier) is not expected:
        raise TypeError("field location identifier has the wrong occurrence type")
    _require_field(field)


def _require_field(field: object) -> None:
    if type(field) is not StaticFieldPath:
        raise TypeError("field location requires a StaticFieldPath")


def _require_owner_source(owner: object, source: object) -> None:
    if type(owner) is not EnvironmentExecutionId:
        raise TypeError("concrete location requires an EnvironmentExecutionId")
    if type(source) is not SelectorSource:
        raise TypeError("concrete location source must be a SelectorSource")


def _require_informational_header_coordinates(location: HeaderLocation) -> None:
    informational = location.section is HttpMessageSection.RESPONSE_INFORMATIONAL_HEADER
    if informational:
        if type(location.informational_response) is not Count:
            raise TypeError("informational header location requires a response occurrence")
        if type(location.informational_status) is not int:
            raise TypeError("informational header location requires an integer status")
        if not 100 <= location.informational_status <= 199:
            raise ValueError("informational header status must be between 100 and 199")
    elif location.informational_response is not None or location.informational_status is not None:
        raise ValueError("non-informational header locations cannot identify a 1xx response")
