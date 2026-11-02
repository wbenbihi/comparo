"""Source, path, occurrence, capability, and output selector semantics."""

from __future__ import annotations

from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.contracts import contract_accepts
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.http_fields import HttpFieldProjection
from comparo.core.domain.selectors.occurrences import AllAttemptsOccurrence
from comparo.core.domain.selectors.occurrences import AllExchangesOccurrence
from comparo.core.domain.selectors.occurrences import AttemptOccurrence
from comparo.core.domain.selectors.occurrences import ExchangeOccurrence
from comparo.core.domain.selectors.occurrences import ObservationOccurrenceSelector
from comparo.core.domain.selectors.occurrences import TerminalOccurrence
from comparo.core.domain.selectors.occurrences import is_observation_occurrence_selector
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.paths import is_selector_path
from comparo.core.domain.selectors.sources import STREAM_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.static_fields import STATIC_FIELD_REQUIRED_SOURCES
from comparo.core.domain.selectors.static_fields import static_field_kind

BODY_SELECTOR_SOURCES = frozenset({SelectorSource.REQUEST_BODY, SelectorSource.RESPONSE_BODY})
TERMINAL_OCCURRENCE = TerminalOccurrence()
HTTP_FIELD_SOURCES = frozenset(
    {
        SelectorSource.REQUEST_QUERY,
        SelectorSource.REQUEST_HEADERS,
        SelectorSource.REQUEST_COOKIES,
        SelectorSource.RESPONSE_INFORMATIONAL,
        SelectorSource.RESPONSE_HEADERS,
        SelectorSource.RESPONSE_TRAILERS,
    }
)
HEADER_FIELD_SOURCES = frozenset(
    {
        SelectorSource.REQUEST_HEADERS,
        SelectorSource.RESPONSE_INFORMATIONAL,
        SelectorSource.RESPONSE_HEADERS,
        SelectorSource.RESPONSE_TRAILERS,
    }
)
_JSON_VALUE_SOURCES = frozenset(
    {
        *BODY_SELECTOR_SOURCES,
        SelectorSource.STREAM_EVENTS,
        SelectorSource.STREAM_EVENT_DATA_JSON,
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
_PLURAL_SOURCES = frozenset(
    {
        *HTTP_FIELD_SOURCES,
        SelectorSource.RESPONSE_ENCODINGS,
        SelectorSource.STREAM_EVENTS,
        SelectorSource.STREAM_EVENT_DATA_JSON,
        SelectorSource.STREAM_RECORDS,
        SelectorSource.STREAM_CONTROLS,
    }
)


def validate_selector_semantics(
    source: SelectorSource,
    path: SelectorPath,
    body_view: BodyViewKind | None,
    occurrence: ObservationOccurrenceSelector,
    capabilities: SelectorCapabilities,
    output: ValueContract,
) -> None:
    """Validate all relationships among one compiled selector's typed fields."""
    if (source in BODY_SELECTOR_SOURCES) != (body_view is not None):
        raise ValueError("exactly body selectors require an explicit body view")
    _require_occurrence_source_compatibility(source, occurrence)
    _require_path_source_compatibility(source, path, body_view)
    expected = derive_selector_capabilities(source, path, occurrence)
    if capabilities != expected:
        raise ValueError("selector capabilities contradict the compiled path and source")
    if output.cardinality is not capabilities.cardinality:
        raise ValueError("selector output cardinality contradicts its capabilities")
    accepted = _whole_source_kinds(source, path, body_view)
    if accepted is not None and not contract_accepts(output, accepted):
        raise ValueError("selector output types contradict its source and view")


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
        if source in BODY_SELECTOR_SOURCES and body_view is not BodyViewKind.JSON:
            raise ValueError("JSON body selector paths require the explicit JSON view")
    if source in BODY_SELECTOR_SOURCES and type(path) not in {
        NoPath,
        JsonPointerPath,
        JsonPathExpression,
    }:
        raise ValueError("body selectors support whole-view or JSON paths only")
    if type(path) is StaticFieldPath:
        if source is not SelectorSource.SCENARIO_STEP and static_field_kind(source, path) is None:
            raise ValueError("static field selector path is not in its source catalog")
    elif source in STATIC_FIELD_REQUIRED_SOURCES:
        raise ValueError("selector source requires one cataloged static field path")
    if type(path) is HttpFieldPath:
        _require_http_field_path(source, path)


def _require_http_field_path(source: SelectorSource, path: HttpFieldPath) -> None:
    if source not in HTTP_FIELD_SOURCES:
        raise ValueError("HTTP field path is incompatible with its source")
    if source in HEADER_FIELD_SOURCES:
        HeaderName(value=path.name)
    elif source is SelectorSource.REQUEST_QUERY:
        QueryName(value=path.name)
    else:
        CookieName(value=path.name)
    if path.projection is HttpFieldProjection.ITEMS and source not in HEADER_FIELD_SOURCES:
        raise ValueError("parsed field-item projection requires an HTTP header source")


def _require_occurrence_source_compatibility(
    source: SelectorSource,
    occurrence: ObservationOccurrenceSelector,
) -> None:
    occurrence_type = type(occurrence)
    if source in {SelectorSource.EXECUTION, SelectorSource.SCENARIO_STEP}:
        if occurrence_type is not TerminalOccurrence:
            raise ValueError("execution and Scenario sources require terminal occurrence scope")
        return
    if source is SelectorSource.ATTEMPT:
        if occurrence_type not in {
            TerminalOccurrence,
            AttemptOccurrence,
            AllAttemptsOccurrence,
        }:
            raise ValueError("attempt source requires terminal, exact, or all-attempt scope")
        return
    if source in STREAM_SELECTOR_SOURCES:
        if occurrence_type is not TerminalOccurrence:
            raise ValueError("logical stream sources currently require terminal occurrence scope")
        return
    if occurrence_type is AllAttemptsOccurrence:
        raise ValueError("exchange-owned sources cannot use all-attempt scope")
    if occurrence_type not in {
        TerminalOccurrence,
        AttemptOccurrence,
        ExchangeOccurrence,
        AllExchangesOccurrence,
    }:
        raise ValueError("exchange-owned source has an incompatible occurrence scope")


def derive_selector_capabilities(
    source: SelectorSource,
    path: SelectorPath,
    occurrence: ObservationOccurrenceSelector = TERMINAL_OCCURRENCE,
) -> SelectorCapabilities:
    """Derive static result and access properties from selector structure."""
    occurrence_is_plural = type(occurrence) in {
        AllAttemptsOccurrence,
        AllExchangesOccurrence,
    }
    if type(path) is JsonPathExpression:
        if source not in _PLURAL_SOURCES and not occurrence_is_plural:
            return path.capabilities
        return SelectorCapabilities(
            cardinality=SelectorCardinality.ZERO_OR_MORE,
            access=path.capabilities.access,
            may_duplicate=path.capabilities.may_duplicate,
            uses_descendant=path.capabilities.uses_descendant,
            uses_filter=path.capabilities.uses_filter,
            function_dependencies=path.capabilities.function_dependencies,
        )
    cardinality = _non_jsonpath_cardinality(source, path, occurrence_is_plural)
    return SelectorCapabilities(
        cardinality=cardinality,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )


def derive_selector_output_contract(
    source: SelectorSource,
    path: SelectorPath,
    body_view: BodyViewKind | None,
    occurrence: ObservationOccurrenceSelector = TERMINAL_OCCURRENCE,
) -> ValueContract:
    """Derive the canonical static output contract for an authored selector.

    Configuration adapters must not duplicate the source/view type catalog.  This helper exposes
    the same closed derivation used by selector invariant validation while keeping compilation
    independent from runtime selection.
    """
    if type(source) is not SelectorSource or not is_selector_path(path):
        raise TypeError("selector output derivation requires a typed source and path")
    if body_view is not None and type(body_view) is not BodyViewKind:
        raise TypeError("selector output derivation requires a typed body view")
    if not is_observation_occurrence_selector(occurrence):
        raise TypeError("selector output derivation requires a typed occurrence")
    capabilities = derive_selector_capabilities(source, path, occurrence)
    kinds = _whole_source_kinds(source, path, body_view)
    if kinds is None:
        kinds = frozenset({ValueKind.UNKNOWN})
    elif len(kinds) > 1:
        # A selector compiler has no schema proof for which member of a broad source union will
        # appear at runtime. UNKNOWN preserves that absence of proof without falsely claiming that
        # every JSON kind will appear in one value.
        kinds = frozenset({ValueKind.UNKNOWN})
    return ValueContract(
        kinds=tuple(sorted(kinds, key=lambda item: item.value)),
        cardinality=capabilities.cardinality,
    )


def _non_jsonpath_cardinality(
    source: SelectorSource,
    path: SelectorPath,
    occurrence_is_plural: bool,
) -> SelectorCardinality:
    if occurrence_is_plural:
        return SelectorCardinality.ZERO_OR_MORE
    if type(path) is HttpFieldPath:
        return (
            SelectorCardinality.ZERO_OR_MORE
            if path.occurrence is None
            else SelectorCardinality.ZERO_OR_ONE
        )
    if source in _PLURAL_SOURCES:
        return SelectorCardinality.ZERO_OR_MORE
    if type(path) is JsonPointerPath:
        return SelectorCardinality.ZERO_OR_ONE
    return SelectorCardinality.EXACTLY_ONE


def derive_path_capabilities(path: SelectorPath) -> SelectorCapabilities:
    """Derive capabilities for a relative path rooted at one current item."""
    if not is_selector_path(path):
        raise TypeError("selector path has an unsupported type")
    if type(path) is JsonPathExpression:
        return path.capabilities
    cardinality = (
        SelectorCardinality.ZERO_OR_ONE
        if type(path) in {JsonPointerPath, StaticFieldPath}
        or (type(path) is HttpFieldPath and path.occurrence is not None)
        else SelectorCardinality.ZERO_OR_MORE
        if type(path) is HttpFieldPath
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
    if type(path) is StaticFieldPath:
        kind = static_field_kind(source, path)
        return None if kind is None else frozenset({kind})
    if type(path) is HttpFieldPath:
        kind = (
            ValueKind.HTTP_FIELD_ITEMS
            if path.projection is HttpFieldProjection.ITEMS
            else ValueKind.HTTP_FIELD_VALUE
        )
        return frozenset({kind})
    if type(path) in {JsonPointerPath, JsonPathExpression}:
        return JSON_VALUE_KINDS
    if type(path) is not NoPath:
        return None
    return _whole_no_path_kinds(source, body_view)


def _whole_no_path_kinds(
    source: SelectorSource,
    body_view: BodyViewKind | None,
) -> frozenset[ValueKind] | None:
    scalar = {
        SelectorSource.EXECUTION: frozenset({ValueKind.STRING}),
        SelectorSource.ATTEMPT: frozenset({ValueKind.STRING}),
        SelectorSource.EXCHANGE: frozenset({ValueKind.STRING}),
        SelectorSource.REQUEST_METHOD: frozenset({ValueKind.STRING}),
        SelectorSource.REQUEST_URL: frozenset({ValueKind.STRING}),
        SelectorSource.RESPONSE_PROTOCOL: frozenset({ValueKind.STRING}),
        SelectorSource.RESPONSE_STATUS: frozenset({ValueKind.INTEGER}),
        SelectorSource.RESPONSE_MEDIA: frozenset({ValueKind.MEDIA_TYPE}),
        SelectorSource.STREAM_CONTROLS: frozenset({ValueKind.OBJECT}),
        SelectorSource.STREAM_TERMINATION: frozenset({ValueKind.STRING}),
    }
    if source in scalar:
        return scalar[source]
    if source in _PLURAL_SOURCES - {
        SelectorSource.STREAM_EVENTS,
        SelectorSource.STREAM_EVENT_DATA_JSON,
        SelectorSource.STREAM_RECORDS,
        SelectorSource.STREAM_CONTROLS,
    }:
        return frozenset({ValueKind.HTTP_FIELD_VALUE})
    if source in BODY_SELECTOR_SOURCES:
        if body_view is None:  # pragma: no cover - validated by caller
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
    if source in {
        SelectorSource.STREAM_EVENT_DATA_JSON,
        SelectorSource.STREAM_RECORDS,
        SelectorSource.SCENARIO_STEP,
    }:
        return JSON_VALUE_KINDS
    return None
