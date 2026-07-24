"""Typed concrete selection coordinates with canonical JSON locations."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeGuard
from typing import final

import msgspec

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


@final
class ExecutionLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One typed Environment-execution field."""

    execution_id: EnvironmentExecutionId
    field: StaticFieldPath

    def __post_init__(self) -> None:
        """Require typed coordinates."""
        _require_field_location(self.execution_id, EnvironmentExecutionId, self.field)


@final
class AttemptLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One typed transport-attempt field."""

    attempt_id: AttemptId
    field: StaticFieldPath

    def __post_init__(self) -> None:
        """Require typed coordinates."""
        _require_field_location(self.attempt_id, AttemptId, self.field)


@final
class ExchangeLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One typed HTTP-exchange field."""

    exchange_id: ExchangeId
    field: StaticFieldPath

    def __post_init__(self) -> None:
        """Require typed coordinates."""
        _require_field_location(self.exchange_id, ExchangeId, self.field)


class HttpMessageSection(StrEnum):
    """Ordered HTTP multimap sections."""

    REQUEST_HEADER = "request-header"
    RESPONSE_INFORMATIONAL_HEADER = "response-informational-header"
    RESPONSE_HEADER = "response-header"
    RESPONSE_TRAILER = "response-trailer"


@final
class HeaderLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered header/trailer occurrence, never a joined value."""

    exchange_id: ExchangeId
    section: HttpMessageSection
    occurrence: Count
    canonical_name: str

    def __post_init__(self) -> None:
        """Require typed coordinates and a lowercase ASCII field name."""
        if type(self.exchange_id) is not ExchangeId:
            raise TypeError("header location requires an ExchangeId")
        if type(self.section) is not HttpMessageSection:
            raise TypeError("header section must be an HttpMessageSection")
        if type(self.occurrence) is not Count:
            raise TypeError("header occurrence must be a Count")
        if (
            type(self.canonical_name) is not str
            or not self.canonical_name
            or not self.canonical_name.isascii()
            or self.canonical_name.lower() != self.canonical_name
        ):
            raise ValueError("header name must be non-empty lowercase ASCII text")


@final
class JsonLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One decoded JSON value addressed by canonical JSON Pointer."""

    view_id: ViewId
    pointer: JsonPointer

    def __post_init__(self) -> None:
        """Require typed view and pointer coordinates."""
        if type(self.view_id) is not ViewId or type(self.pointer) is not JsonPointer:
            raise TypeError("JSON location requires a ViewId and JsonPointer")


@final
class BodyLocation(msgspec.Struct, frozen=True, kw_only=True):
    """A whole body or non-empty half-open byte range."""

    body_id: BodyId
    start: ByteCount | None = None
    end: ByteCount | None = None

    def __post_init__(self) -> None:
        """Require typed, paired byte-range bounds."""
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

    view_id: ViewId
    connection_segment: Count
    record_index: Count
    nested_pointer: JsonPointer | None = None

    def __post_init__(self) -> None:
        """Require exact stream and optional nested coordinates."""
        if type(self.view_id) is not ViewId:
            raise TypeError("stream location requires a ViewId")
        if type(self.connection_segment) is not Count or type(self.record_index) is not Count:
            raise TypeError("stream location indices must be Count values")
        if self.nested_pointer is not None and type(self.nested_pointer) is not JsonPointer:
            raise TypeError("nested stream pointer must be a JsonPointer")


@final
class ScenarioLocation(msgspec.Struct, frozen=True, kw_only=True):
    """One typed future Scenario-step field."""

    step_execution_id: StepExecutionId
    field: StaticFieldPath

    def __post_init__(self) -> None:
        """Require typed coordinates."""
        _require_field_location(self.step_execution_id, StepExecutionId, self.field)


type ConcreteLocation = (
    ExecutionLocation
    | AttemptLocation
    | ExchangeLocation
    | HeaderLocation
    | JsonLocation
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
        JsonLocation,
        BodyLocation,
        StreamLocation,
        ScenarioLocation,
    }


def _require_field_location(identifier: object, expected: type[object], field: object) -> None:
    if type(identifier) is not expected:
        raise TypeError("field location identifier has the wrong occurrence type")
    if type(field) is not StaticFieldPath:
        raise TypeError("field location requires a StaticFieldPath")
