"""Prove that built-in static selector fields are closed and output-typed."""

from __future__ import annotations

import pytest

from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import REQUEST_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import RESPONSE_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import STREAM_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.static_fields import static_field_kind
from comparo.core.domain.value_kinds import ValueKind


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="f" * 64))


def _selector(source: SelectorSource, path: StaticFieldPath, kind: ValueKind) -> CompiledSelector:
    capabilities = derive_selector_capabilities(source, path)
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=source,
        path=path,
        capabilities=capabilities,
        output=ValueContract(kinds=(kind,), cardinality=capabilities.cardinality),
    )


def test_catalog_returns_exact_leaf_kind() -> None:
    elapsed = StaticFieldPath(segments=("execution", "timings", "elapsed"))

    assert static_field_kind(SelectorSource.EXECUTION, elapsed) is ValueKind.DURATION
    assert _selector(SelectorSource.EXECUTION, elapsed, ValueKind.DURATION).path == elapsed


def test_unknown_and_wrongly_typed_static_fields_fail_compilation() -> None:
    unknown = StaticFieldPath(segments=("response", "timings", "total"))
    with pytest.raises(ValueError, match="source catalog"):
        _selector(SelectorSource.RESPONSE_TIMINGS, unknown, ValueKind.DURATION)

    elapsed = StaticFieldPath(segments=("response", "timings", "elapsed"))
    with pytest.raises(ValueError, match="output types"):
        _selector(SelectorSource.RESPONSE_TIMINGS, elapsed, ValueKind.STRING)


def test_leaf_owned_sources_reject_aggregate_no_path() -> None:
    path = NoPath()
    capabilities = derive_selector_capabilities(SelectorSource.RESPONSE_ENDPOINT, path)
    with pytest.raises(ValueError, match="cataloged static field"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_ENDPOINT,
            path=path,
            capabilities=capabilities,
            output=ValueContract(
                kinds=(ValueKind.UNKNOWN,),
                cardinality=capabilities.cardinality,
            ),
        )


def test_http_and_stream_source_families_are_explicit_and_disjoint() -> None:
    assert {
        SelectorSource.REQUEST_METHOD,
        SelectorSource.REQUEST_URL,
        SelectorSource.REQUEST_QUERY,
        SelectorSource.REQUEST_HEADERS,
        SelectorSource.REQUEST_COOKIES,
        SelectorSource.REQUEST_BODY,
    } == REQUEST_SELECTOR_SOURCES
    assert {
        SelectorSource.RESPONSE_PROTOCOL,
        SelectorSource.RESPONSE_STATUS,
        SelectorSource.RESPONSE_INFORMATIONAL,
        SelectorSource.RESPONSE_HEADERS,
        SelectorSource.RESPONSE_TRAILERS,
        SelectorSource.RESPONSE_MEDIA,
        SelectorSource.RESPONSE_ENCODINGS,
        SelectorSource.RESPONSE_BODY,
        SelectorSource.RESPONSE_TIMINGS,
        SelectorSource.RESPONSE_ENDPOINT,
        SelectorSource.RESPONSE_TLS,
    } == RESPONSE_SELECTOR_SOURCES
    assert {
        SelectorSource.STREAM_EVENTS,
        SelectorSource.STREAM_EVENT_DATA_JSON,
        SelectorSource.STREAM_RECORDS,
        SelectorSource.STREAM_CONTROLS,
        SelectorSource.STREAM_TIMINGS,
        SelectorSource.STREAM_TERMINATION,
    } == STREAM_SELECTOR_SOURCES
    assert not REQUEST_SELECTOR_SOURCES & RESPONSE_SELECTOR_SOURCES
    assert not REQUEST_SELECTOR_SOURCES & STREAM_SELECTOR_SOURCES
    assert not RESPONSE_SELECTOR_SOURCES & STREAM_SELECTOR_SOURCES
