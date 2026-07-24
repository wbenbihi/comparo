"""The canonical compiled selector shared by every evaluator."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.contracts import contract_accepts
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.locations import location_body_view
from comparo.core.domain.selectors.locations import location_selector_source
from comparo.core.domain.selectors.locations import location_static_field
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.paths import is_selector_path
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource

_BODY_SOURCES = frozenset({SelectorSource.REQUEST_BODY, SelectorSource.RESPONSE_BODY})
_JSON_VALUE_SOURCES = frozenset(
    {
        SelectorSource.REQUEST_BODY,
        SelectorSource.RESPONSE_BODY,
        SelectorSource.STREAM_EVENTS,
        SelectorSource.STREAM_RECORDS,
        SelectorSource.SCENARIO_STEP,
    }
)
_SCALAR_SOURCES = frozenset(
    {
        SelectorSource.REQUEST_METHOD,
        SelectorSource.REQUEST_URL,
        SelectorSource.RESPONSE_PROTOCOL,
        SelectorSource.RESPONSE_STATUS,
    }
)
_STATIC_FIELD_SOURCES = frozenset(
    {
        SelectorSource.EXECUTION,
        SelectorSource.ATTEMPT,
        SelectorSource.EXCHANGE,
        SelectorSource.RESPONSE_MEDIA,
        SelectorSource.RESPONSE_TIMINGS,
        SelectorSource.RESPONSE_ENDPOINT,
        SelectorSource.RESPONSE_TLS,
        SelectorSource.STREAM_TERMINATION,
        SelectorSource.SCENARIO_STEP,
    }
)
_PLURAL_SOURCES = frozenset(
    {
        SelectorSource.REQUEST_QUERY,
        SelectorSource.REQUEST_HEADERS,
        SelectorSource.REQUEST_COOKIES,
        SelectorSource.RESPONSE_INFORMATIONAL,
        SelectorSource.RESPONSE_HEADERS,
        SelectorSource.RESPONSE_TRAILERS,
        SelectorSource.RESPONSE_ENCODINGS,
        SelectorSource.STREAM_EVENTS,
        SelectorSource.STREAM_RECORDS,
        SelectorSource.STREAM_CONTROLS,
    }
)
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


def _require_selector_semantics(selector: CompiledSelector) -> None:
    """Validate view, path, capability, and output contract consistency."""
    if (selector.source in _BODY_SOURCES) != (selector.body_view is not None):
        raise ValueError("exactly body selectors require an explicit body view")
    _require_path_source_compatibility(selector.source, selector.path, selector.body_view)
    expected = _derived_capabilities(selector.source, selector.path)
    if selector.capabilities != expected:
        raise ValueError("selector capabilities contradict the compiled path and source")
    if selector.output.cardinality is not selector.capabilities.cardinality:
        raise ValueError("selector output cardinality contradicts its capabilities")
    accepted = _whole_source_kinds(selector.source, selector.path, selector.body_view)
    if accepted is not None and not contract_accepts(selector.output, accepted):
        raise ValueError("selector output types contradict its source and view")


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
    if selector.source in _BODY_SOURCES and type(path) is NoPath:
        _require_whole_body_location(location, selector.body_view)
        return
    if type(path) is JsonPointerPath:
        _require_pointer_location(location, path)
        return
    if type(path) is JsonPathExpression:
        if type(location) not in {JsonLocation, StreamLocation}:
            raise ValueError("JSONPath selector requires a JSON-addressable location")
        if type(location) is StreamLocation and location.nested_pointer is None:
            raise ValueError("stream JSONPath result requires an exact nested JSON pointer")
        return
    if type(path) is StaticFieldPath:
        if location_static_field(location) != path:
            raise ValueError("concrete field location does not match the compiled selector path")
        return
    if type(location) is StreamLocation and location.nested_pointer is not None:
        raise ValueError("whole stream-item selector cannot retain a nested JSON coordinate")
    expected_field = next(
        (field for source, field in _NO_PATH_FIELDS if source is selector.source),
        None,
    )
    if expected_field is not None and location_static_field(location) != expected_field:
        raise ValueError("concrete field location does not match the selector source")


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


def _require_path_source_compatibility(
    source: SelectorSource,
    path: SelectorPath,
    body_view: BodyViewKind | None,
) -> None:
    if source in _SCALAR_SOURCES and type(path) is not NoPath:
        raise ValueError("scalar HTTP selector sources require NoPath")
    if type(path) in {JsonPointerPath, JsonPathExpression}:
        if source not in _JSON_VALUE_SOURCES:
            raise ValueError("JSON selector paths require a JSON-compatible source")
        if source in _BODY_SOURCES and body_view is not BodyViewKind.JSON:
            raise ValueError("JSON body selector paths require the explicit JSON view")
    if source in _BODY_SOURCES and type(path) not in {
        NoPath,
        JsonPointerPath,
        JsonPathExpression,
    }:
        raise ValueError("body selectors support whole-view or JSON paths only")
    if type(path) is StaticFieldPath and source not in _STATIC_FIELD_SOURCES:
        raise ValueError("static field selector path is incompatible with its source")


def _derived_capabilities(
    source: SelectorSource,
    path: SelectorPath,
) -> SelectorCapabilities:
    if type(path) is JsonPathExpression:
        if source not in _PLURAL_SOURCES:
            return path.capabilities
        return SelectorCapabilities(
            cardinality=SelectorCardinality.ZERO_OR_MORE,
            access=path.capabilities.access,
            may_duplicate=path.capabilities.may_duplicate,
            uses_descendant=path.capabilities.uses_descendant,
            uses_filter=path.capabilities.uses_filter,
            function_dependencies=path.capabilities.function_dependencies,
        )
    cardinality = (
        SelectorCardinality.ZERO_OR_MORE
        if source in _PLURAL_SOURCES
        else SelectorCardinality.EXACTLY_ONE
    )
    if type(path) is JsonPointerPath and source not in _PLURAL_SOURCES:
        cardinality = SelectorCardinality.ZERO_OR_ONE
    return SelectorCapabilities(
        cardinality=cardinality,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )


def derive_path_capabilities(path: SelectorPath) -> SelectorCapabilities:
    """Derive capabilities for a relative path rooted at exactly one current item."""
    if not is_selector_path(path):
        raise TypeError("selector path has an unsupported type")
    if type(path) is JsonPathExpression:
        return path.capabilities
    cardinality = (
        SelectorCardinality.ZERO_OR_ONE
        if type(path) in {JsonPointerPath, StaticFieldPath}
        else SelectorCardinality.EXACTLY_ONE
    )
    return SelectorCapabilities(
        cardinality=cardinality,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )


def _whole_source_kinds(
    source: SelectorSource,
    path: SelectorPath,
    body_view: BodyViewKind | None,
) -> frozenset[ValueKind] | None:
    if type(path) is not NoPath:
        return JSON_VALUE_KINDS if type(path) in {JsonPointerPath, JsonPathExpression} else None
    scalar = {
        SelectorSource.REQUEST_METHOD: frozenset({ValueKind.STRING}),
        SelectorSource.REQUEST_URL: frozenset({ValueKind.STRING}),
        SelectorSource.RESPONSE_PROTOCOL: frozenset({ValueKind.STRING}),
        SelectorSource.RESPONSE_STATUS: frozenset({ValueKind.INTEGER}),
        SelectorSource.RESPONSE_MEDIA: frozenset({ValueKind.MEDIA_TYPE}),
    }
    if source in scalar:
        return scalar[source]
    if source in {
        SelectorSource.REQUEST_QUERY,
        SelectorSource.REQUEST_HEADERS,
        SelectorSource.REQUEST_COOKIES,
        SelectorSource.RESPONSE_INFORMATIONAL,
        SelectorSource.RESPONSE_HEADERS,
        SelectorSource.RESPONSE_TRAILERS,
        SelectorSource.RESPONSE_ENCODINGS,
    }:
        return frozenset({ValueKind.HTTP_FIELD_VALUE})
    if source in _BODY_SOURCES:
        if body_view is None:  # pragma: no cover - guarded by selector validation
            raise ValueError("body source requires an explicit view")
        return {
            BodyViewKind.HANDLE: frozenset({ValueKind.BODY_HANDLE}),
            BodyViewKind.ENTITY_BYTES: frozenset({ValueKind.BYTES}),
            BodyViewKind.TEXT: frozenset({ValueKind.STRING}),
            BodyViewKind.JSON: JSON_VALUE_KINDS,
            BodyViewKind.SSE_EVENTS: frozenset({ValueKind.ARRAY}),
            BodyViewKind.NDJSON_RECORDS: frozenset({ValueKind.ARRAY}),
            BodyViewKind.JSON_SEQUENCE_RECORDS: frozenset({ValueKind.ARRAY}),
            BodyViewKind.AUTO: frozenset({ValueKind.UNKNOWN}),
        }[body_view]
    if source is SelectorSource.STREAM_EVENTS:
        return frozenset({ValueKind.OBJECT})
    if source in {SelectorSource.STREAM_RECORDS, SelectorSource.SCENARIO_STEP}:
        return JSON_VALUE_KINDS
    return None
