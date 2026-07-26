"""Close defensive branches across the compiled selector domain boundary."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.http_fields import HttpFieldProjection
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.jsonpath import ChildSegment
from comparo.core.domain.selectors.jsonpath import FilterSelector
from comparo.core.domain.selectors.jsonpath import IndexSelector
from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath import LiteralExpression
from comparo.core.domain.selectors.jsonpath import NotExpression
from comparo.core.domain.selectors.jsonpath import SliceSelector
from comparo.core.domain.selectors.jsonpath_derivation import JsonPathAstError
from comparo.core.domain.selectors.jsonpath_derivation import JsonPathAstErrorCode
from comparo.core.domain.selectors.jsonpath_derivation import canonical_jsonpath
from comparo.core.domain.selectors.jsonpath_derivation import derive_jsonpath_capabilities
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import HttpMessageSection
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.model import _require_http_field_location
from comparo.core.domain.selectors.model import require_location_matches_selector
from comparo.core.domain.selectors.occurrences import AttemptOccurrence
from comparo.core.domain.selectors.occurrences import ExchangeOccurrence
from comparo.core.domain.selectors.occurrences import ObservationOccurrenceSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.selector_semantics import _require_http_field_path
from comparo.core.domain.selectors.selector_semantics import (
    _require_occurrence_source_compatibility,
)
from comparo.core.domain.selectors.selector_semantics import _whole_source_kinds
from comparo.core.domain.selectors.selector_semantics import derive_path_capabilities
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.selector_semantics import derive_selector_output_contract
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonBoolean
from comparo.core.selection import compile_jsonpath


def _fingerprint() -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value="9" * 64),
    )


def _output(kind: ValueKind, cardinality: SelectorCardinality) -> ValueContract:
    return ValueContract(kinds=(kind,), cardinality=cardinality)


def test_http_field_path_validates_unicode_occurrence_and_projection() -> None:
    assert HttpFieldPath(name="x").name == "x"
    for name in ("", "x" * 8_193):
        with pytest.raises(ValueError, match="non-empty bounded"):
            HttpFieldPath(name=name)
    with pytest.raises(ValueError, match="Unicode scalar"):
        HttpFieldPath(name="\ud800")
    with pytest.raises(TypeError, match="occurrence"):
        HttpFieldPath(name="x", occurrence=cast("Count", 1))
    with pytest.raises(TypeError, match="projection"):
        HttpFieldPath(name="x", projection=cast("HttpFieldProjection", "raw-value"))


def test_occurrence_selectors_require_exact_runtime_identities() -> None:
    assert AttemptOccurrence(attempt_id=AttemptId(value=UUID(int=1))).attempt_id.value.int == 1
    assert ExchangeOccurrence(exchange_id=ExchangeId(value=UUID(int=2))).exchange_id.value.int == 2
    with pytest.raises(TypeError, match="AttemptId"):
        AttemptOccurrence(attempt_id=cast("AttemptId", UUID(int=1)))
    with pytest.raises(TypeError, match="ExchangeId"):
        ExchangeOccurrence(exchange_id=cast("ExchangeId", UUID(int=2)))


def test_item_selector_rejects_observation_rooted_http_field_paths() -> None:
    path = HttpFieldPath(name="x")
    capabilities = derive_path_capabilities(path)
    with pytest.raises(ValueError, match="observation-rooted"):
        CompiledItemSelector(
            fingerprint=_fingerprint(),
            input=_output(ValueKind.OBJECT, SelectorCardinality.EXACTLY_ONE),
            path=path,
            capabilities=capabilities,
            output=_output(ValueKind.HTTP_FIELD_VALUE, capabilities.cardinality),
        )


@pytest.mark.parametrize(
    "ast",
    [
        JsonPathAst(segments=(ChildSegment(selectors=(IndexSelector(index=(1 << 53)),)),)),
        JsonPathAst(
            segments=(
                ChildSegment(selectors=(SliceSelector(start=None, stop=(1 << 53), step=None),)),
            )
        ),
    ],
)
def test_direct_jsonpath_ast_rejects_non_interoperable_numbers(ast: JsonPathAst) -> None:
    with pytest.raises(JsonPathAstError) as captured:
        canonical_jsonpath(ast)
    assert captured.value.code is JsonPathAstErrorCode.INVALID_NUMBER


def test_jsonpath_derivation_rejects_untyped_ast_and_non_logical_negation() -> None:
    with pytest.raises(TypeError, match="JsonPathAst"):
        canonical_jsonpath(cast("JsonPathAst", object()))

    ast = JsonPathAst(
        segments=(
            ChildSegment(
                selectors=(
                    FilterSelector(
                        predicate=NotExpression(
                            operand=LiteralExpression(value=JsonBoolean(value=True))
                        )
                    ),
                )
            ),
        )
    )
    with pytest.raises(JsonPathAstError) as captured:
        derive_jsonpath_capabilities(ast)
    assert captured.value.code is JsonPathAstErrorCode.INVALID_TYPE


def test_function_collection_descends_into_query_local_filters() -> None:
    path = compile_jsonpath("$[?@.items[?count(@[*])>0]]")
    assert derive_jsonpath_capabilities(path.ast).function_dependencies == ("count",)


def test_compiled_selector_rejects_untyped_occurrence() -> None:
    with pytest.raises(TypeError, match="occurrence"):
        CompiledSelector(
            fingerprint=_fingerprint(),
            source=SelectorSource.RESPONSE_STATUS,
            path=NoPath(),
            capabilities=derive_selector_capabilities(SelectorSource.RESPONSE_STATUS, NoPath()),
            output=_output(ValueKind.INTEGER, SelectorCardinality.EXACTLY_ONE),
            occurrence=cast("ObservationOccurrenceSelector", object()),
        )


def test_http_field_source_semantics_cover_headers_query_cookies_and_errors() -> None:
    raw = HttpFieldPath(name="x")
    items = HttpFieldPath(name="x", projection=HttpFieldProjection.ITEMS)
    _require_http_field_path(SelectorSource.RESPONSE_HEADERS, items)
    _require_http_field_path(SelectorSource.REQUEST_QUERY, raw)
    _require_http_field_path(SelectorSource.REQUEST_COOKIES, raw)
    with pytest.raises(ValueError, match="incompatible"):
        _require_http_field_path(SelectorSource.RESPONSE_STATUS, raw)
    with pytest.raises(ValueError, match="header source"):
        _require_http_field_path(SelectorSource.REQUEST_QUERY, items)


def test_occurrence_semantics_reject_unknown_exchange_scope() -> None:
    with pytest.raises(ValueError, match="incompatible occurrence"):
        _require_occurrence_source_compatibility(
            SelectorSource.RESPONSE_STATUS,
            cast("ObservationOccurrenceSelector", object()),
        )


def test_selector_semantics_cover_http_and_unknown_whole_source_kinds() -> None:
    path = HttpFieldPath(name="x", occurrence=Count(value=0))
    kinds = _whole_source_kinds(SelectorSource.RESPONSE_HEADERS, path, None)
    assert kinds == frozenset({ValueKind.HTTP_FIELD_VALUE})
    items = HttpFieldPath(name="x", projection=HttpFieldProjection.ITEMS)
    assert _whole_source_kinds(SelectorSource.RESPONSE_HEADERS, items, None) == frozenset(
        {ValueKind.HTTP_FIELD_ITEMS}
    )
    assert (
        _whole_source_kinds(
            SelectorSource.STREAM_TIMINGS,
            cast("SelectorPath", NoPath()),
            None,
        )
        is None
    )
    assert (
        _whole_source_kinds(
            SelectorSource.RESPONSE_STATUS,
            cast("SelectorPath", object()),
            None,
        )
        is None
    )


def test_http_field_locations_bind_exact_name_and_occurrence() -> None:
    path = HttpFieldPath(name="x-test", occurrence=Count(value=1))
    capabilities = derive_selector_capabilities(SelectorSource.RESPONSE_HEADERS, path)
    selector = CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_HEADERS,
        path=path,
        capabilities=capabilities,
        output=_output(ValueKind.HTTP_FIELD_VALUE, capabilities.cardinality),
    )
    location = HeaderLocation(
        environment_execution_id=EnvironmentExecutionId(value=UUID(int=10)),
        source=SelectorSource.RESPONSE_HEADERS,
        exchange_id=ExchangeId(value=UUID(int=11)),
        section=HttpMessageSection.RESPONSE_HEADER,
        occurrence=Count(value=1),
        name=HeaderName(value="x-test"),
    )
    require_location_matches_selector(location, selector)

    wrong_name = HeaderLocation(
        environment_execution_id=location.environment_execution_id,
        source=location.source,
        exchange_id=location.exchange_id,
        section=location.section,
        occurrence=location.occurrence,
        name=HeaderName(value="other"),
    )
    with pytest.raises(ValueError, match="name contradicts"):
        _require_http_field_location(wrong_name, path)

    wrong_occurrence = HeaderLocation(
        environment_execution_id=location.environment_execution_id,
        source=location.source,
        exchange_id=location.exchange_id,
        section=location.section,
        occurrence=Count(value=0),
        name=location.name,
    )
    with pytest.raises(ValueError, match="occurrence contradicts"):
        _require_http_field_location(wrong_occurrence, path)

    not_a_field = ExchangeLocation(
        environment_execution_id=location.environment_execution_id,
        source=SelectorSource.RESPONSE_STATUS,
        exchange_id=location.exchange_id,
        field=StaticFieldPath(segments=("response", "status")),
    )
    with pytest.raises(ValueError, match="named field"):
        _require_http_field_location(cast("HeaderLocation", not_a_field), path)


def test_json_body_whole_source_kind_requires_explicit_view_defensively() -> None:
    with pytest.raises(ValueError, match="explicit view"):
        _whole_source_kinds(SelectorSource.RESPONSE_BODY, NoPath(), None)
    assert _whole_source_kinds(SelectorSource.RESPONSE_BODY, NoPath(), BodyViewKind.JSON)


def test_selector_output_contract_rejects_untyped_inputs_and_preserves_unknowns() -> None:
    with pytest.raises(TypeError, match="typed source and path"):
        derive_selector_output_contract(cast("SelectorSource", object()), NoPath(), None)
    with pytest.raises(TypeError, match="typed body view"):
        derive_selector_output_contract(
            SelectorSource.RESPONSE_STATUS,
            NoPath(),
            cast("BodyViewKind", object()),
        )
    with pytest.raises(TypeError, match="typed occurrence"):
        derive_selector_output_contract(
            SelectorSource.RESPONSE_STATUS,
            NoPath(),
            None,
            cast("ObservationOccurrenceSelector", object()),
        )
    contract = derive_selector_output_contract(
        SelectorSource.STREAM_TIMINGS,
        NoPath(),
        None,
    )
    assert contract.kinds == (ValueKind.UNKNOWN,)
