"""The canonical compiled selector shared by every evaluator."""

from __future__ import annotations

from typing import TypeGuard
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.locations import location_body_view
from comparo.core.domain.selectors.locations import location_selector_source
from comparo.core.domain.selectors.locations import location_static_field
from comparo.core.domain.selectors.occurrences import ObservationOccurrenceSelector
from comparo.core.domain.selectors.occurrences import is_observation_occurrence_selector
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.paths import is_selector_path
from comparo.core.domain.selectors.selector_semantics import BODY_SELECTOR_SOURCES
from comparo.core.domain.selectors.selector_semantics import TERMINAL_OCCURRENCE
from comparo.core.domain.selectors.selector_semantics import (
    derive_path_capabilities as derive_path_capabilities,
)
from comparo.core.domain.selectors.selector_semantics import validate_selector_semantics
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource

_NO_PATH_FIELDS = (
    (SelectorSource.EXECUTION, StaticFieldPath(segments=("execution",))),
    (SelectorSource.ATTEMPT, StaticFieldPath(segments=("attempt",))),
    (SelectorSource.EXCHANGE, StaticFieldPath(segments=("exchange",))),
    (SelectorSource.REQUEST_METHOD, StaticFieldPath(segments=("request", "method"))),
    (SelectorSource.REQUEST_URL, StaticFieldPath(segments=("request", "url"))),
    (SelectorSource.RESPONSE_PROTOCOL, StaticFieldPath(segments=("response", "protocol"))),
    (SelectorSource.RESPONSE_STATUS, StaticFieldPath(segments=("response", "status"))),
    (SelectorSource.RESPONSE_MEDIA, StaticFieldPath(segments=("response", "media"))),
    (SelectorSource.RESPONSE_TIMINGS, StaticFieldPath(segments=("response", "timings"))),
    (SelectorSource.RESPONSE_ENDPOINT, StaticFieldPath(segments=("response", "endpoint"))),
    (SelectorSource.RESPONSE_TLS, StaticFieldPath(segments=("response", "tls"))),
    (SelectorSource.STREAM_TERMINATION, StaticFieldPath(segments=("stream", "termination"))),
    (SelectorSource.SCENARIO_STEP, StaticFieldPath(segments=("scenario", "step"))),
)


@final
class CompiledSelector(msgspec.Struct, frozen=True, kw_only=True):
    """One statically valid selector; static invalidity is a compile diagnostic."""

    fingerprint: Fingerprint
    source: SelectorSource
    path: SelectorPath
    capabilities: SelectorCapabilities
    output: ValueContract
    body_view: BodyViewKind | None = None
    occurrence: ObservationOccurrenceSelector = TERMINAL_OCCURRENCE

    def __post_init__(self) -> None:
        """Require typed metadata and explicit views only for body sources."""
        _require_selector_components(self)
        _require_selector_semantics(self)


def _require_selector_components(selector: CompiledSelector) -> None:
    """Validate direct selector field types before semantic derivation."""
    if type(selector.fingerprint) is not Fingerprint:
        raise TypeError("compiled selector fingerprint must be a Fingerprint")
    if type(selector.source) is not SelectorSource:
        raise TypeError("compiled selector source must be a SelectorSource")
    if not is_selector_path(selector.path):
        raise TypeError("compiled selector path has an unsupported type")
    if type(selector.capabilities) is not SelectorCapabilities:
        raise TypeError("compiled selector capabilities must be SelectorCapabilities")
    if type(selector.output) is not ValueContract:
        raise TypeError("compiled selector output must be a ValueContract")
    if selector.body_view is not None and type(selector.body_view) is not BodyViewKind:
        raise TypeError("selector body view must be a BodyViewKind")
    if not is_observation_occurrence_selector(selector.occurrence):
        raise TypeError("compiled selector occurrence has an unsupported type")


def _require_selector_semantics(selector: CompiledSelector) -> None:
    """Validate view, path, capability, and output contract consistency."""
    validate_selector_semantics(
        selector.source,
        selector.path,
        selector.body_view,
        selector.occurrence,
        selector.capabilities,
        selector.output,
    )


def is_singular_selector(selector: object) -> TypeGuard[CompiledSelector]:
    """Return whether static compilation proves at most one match."""
    return type(selector) is CompiledSelector and (
        selector.capabilities.cardinality is not SelectorCardinality.ZERO_OR_MORE
    )


def require_location_matches_selector(
    location: ConcreteLocation,
    selector: CompiledSelector,
) -> None:
    """Bind one runtime location to the exact compiled source and body view."""
    if not is_concrete_location(location):
        raise TypeError("selector location must be a ConcreteLocation")
    if type(selector) is not CompiledSelector:
        raise TypeError("location validation requires a CompiledSelector")
    if location_selector_source(location) is not selector.source:
        raise ValueError("concrete location source does not match the compiled selector")
    if location_body_view(location) is not selector.body_view:
        raise ValueError("concrete location body view does not match the compiled selector")
    _require_location_path(location, selector)


def _require_location_path(
    location: ConcreteLocation,
    selector: CompiledSelector,
) -> None:
    path = selector.path
    if selector.source in BODY_SELECTOR_SOURCES and type(path) is NoPath:
        _require_whole_body_location(location, selector.body_view)
        return
    if type(path) is JsonPointerPath:
        _require_pointer_location(location, path)
        return
    if type(path) is JsonPathExpression:
        _require_jsonpath_location(location)
        return
    if type(path) is StaticFieldPath:
        if location_static_field(location) != path:
            raise ValueError("concrete field location does not match the compiled selector path")
        return
    if type(path) is HttpFieldPath:
        _require_http_field_location(location, path)
        return
    if type(location) is StreamLocation and location.nested_pointer is not None:
        raise ValueError("whole stream-item selector cannot retain a nested JSON coordinate")
    expected_field = next(
        (field for source, field in _NO_PATH_FIELDS if source is selector.source),
        None,
    )
    if expected_field is not None and location_static_field(location) != expected_field:
        raise ValueError("concrete field location does not match the selector source")


def _require_jsonpath_location(location: ConcreteLocation) -> None:
    if type(location) not in {JsonLocation, StreamLocation}:
        raise ValueError("JSONPath selector requires a JSON-addressable location")
    if type(location) is StreamLocation and location.nested_pointer is None:
        raise ValueError("stream JSONPath result requires an exact nested JSON pointer")


def _require_whole_body_location(
    location: ConcreteLocation,
    body_view: BodyViewKind | None,
) -> None:
    expected_type = (
        BodyLocation
        if body_view in {BodyViewKind.HANDLE, BodyViewKind.ENTITY_BYTES}
        else ViewLocation
    )
    if type(location) is not expected_type:
        raise ValueError("whole body selector requires a whole body or decoded-view location")


def _require_pointer_location(
    location: ConcreteLocation,
    path: JsonPointerPath,
) -> None:
    if type(location) is JsonLocation:
        if location.pointer != path.pointer:
            raise ValueError("JSON location pointer does not match the compiled selector")
        return
    if type(location) is StreamLocation and location.nested_pointer == path.pointer:
        return
    raise ValueError("JSON Pointer selector requires its exact concrete pointer location")


def _require_http_field_location(
    location: ConcreteLocation,
    path: HttpFieldPath,
) -> None:
    if type(location) not in {HeaderLocation, NamedFieldLocation}:
        raise ValueError("HTTP field selector requires a named field location")
    field_location = cast("HeaderLocation | NamedFieldLocation", location)
    if field_location.name.value != path.name:
        raise ValueError("HTTP field location name contradicts its selector path")
    if path.occurrence is not None and field_location.occurrence != path.occurrence:
        raise ValueError("HTTP field location occurrence contradicts its selector path")
