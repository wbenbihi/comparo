"""Selector source catalog and statically proven execution capabilities."""

from __future__ import annotations

import re
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum

_FUNCTION_PATTERN = re.compile(r"[a-z][a-z0-9-]*")


class SelectorSource(DomainStrEnum):
    """Closed typed observation sources shared by every selector consumer."""

    EXECUTION = "execution"
    ATTEMPT = "attempt"
    EXCHANGE = "exchange"
    REQUEST_METHOD = "request.method"
    REQUEST_URL = "request.url"
    REQUEST_QUERY = "request.query"
    REQUEST_HEADERS = "request.headers"
    REQUEST_COOKIES = "request.cookies"
    REQUEST_BODY = "request.body"
    RESPONSE_PROTOCOL = "response.protocol"
    RESPONSE_STATUS = "response.status"
    RESPONSE_INFORMATIONAL = "response.informational"
    RESPONSE_HEADERS = "response.headers"
    RESPONSE_TRAILERS = "response.trailers"
    RESPONSE_MEDIA = "response.media"
    RESPONSE_ENCODINGS = "response.encodings"
    RESPONSE_BODY = "response.body"
    RESPONSE_TIMINGS = "response.timings"
    RESPONSE_ENDPOINT = "response.endpoint"
    RESPONSE_TLS = "response.tls"
    STREAM_EVENTS = "stream.events"
    STREAM_EVENT_DATA_JSON = "stream.events.data-json"
    STREAM_RECORDS = "stream.records"
    STREAM_CONTROLS = "stream.controls"
    STREAM_TERMINATION = "stream.termination"
    SCENARIO_STEP = "scenario.step"


class BodyViewKind(DomainStrEnum):
    """Explicit representations; ``auto`` is media-driven, never content sniffing."""

    HANDLE = "handle"
    ENTITY_BYTES = "entity-bytes"
    TEXT = "text"
    JSON = "json"
    SSE_EVENTS = "sse-events"
    NDJSON_RECORDS = "ndjson-records"
    JSON_SEQUENCE_RECORDS = "json-sequence-records"
    AUTO = "auto"


class SelectorCardinality(DomainStrEnum):
    """Cardinality proven by static compilation."""

    EXACTLY_ONE = "exactly-one"
    ZERO_OR_ONE = "zero-or-one"
    ZERO_OR_MORE = "zero-or-more"


class SelectionAccess(DomainStrEnum):
    """The strongest storage capability required by selection."""

    INCREMENTAL = "incremental"
    INDEXED = "indexed"
    MATERIALIZED = "materialized"


@final
class SelectorCapabilities(msgspec.Struct, frozen=True, kw_only=True):
    """Static cardinality and execution requirements for one compiled selector."""

    cardinality: SelectorCardinality
    access: SelectionAccess
    may_duplicate: bool
    uses_descendant: bool
    uses_filter: bool
    function_dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require exact flags and unique, versioned-catalog function names."""
        if type(self.cardinality) is not SelectorCardinality:
            raise TypeError("selector cardinality must be a SelectorCardinality")
        if type(self.access) is not SelectionAccess:
            raise TypeError("selector access must be a SelectionAccess")
        for flag in (self.may_duplicate, self.uses_descendant, self.uses_filter):
            if type(flag) is not bool:
                raise TypeError("selector capability flags must be bool values")
        if type(self.function_dependencies) is not tuple:
            raise TypeError("selector functions must be an immutable tuple")
        if any(
            type(name) is not str or _FUNCTION_PATTERN.fullmatch(name) is None
            for name in self.function_dependencies
        ):
            raise ValueError("selector function dependencies contain an invalid name")
        if len(set(self.function_dependencies)) != len(self.function_dependencies):
            raise ValueError("selector function dependencies must be unique")
