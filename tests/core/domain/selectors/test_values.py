"""Cover the closed selector runtime value-kind mapping."""

from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import contract_accepts_runtime_kind
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import HttpMessageSection
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import selected_value_kind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (JsonNull(), ValueKind.NULL),
        (JsonBoolean(value=True), ValueKind.BOOLEAN),
        (JsonInteger(value=1), ValueKind.INTEGER),
        (JsonDecimal(value=Decimal("1.5")), ValueKind.NUMBER),
        (JsonString(value="value"), ValueKind.STRING),
        (JsonArray(items=()), ValueKind.ARRAY),
        (JsonObject(members=()), ValueKind.OBJECT),
        (BytesValue(value=b"value"), ValueKind.BYTES),
        (Duration(nanoseconds=1), ValueKind.DURATION),
        (ByteCount(value=1), ValueKind.SIZE),
        (UtcInstant(unix_nanoseconds=1), ValueKind.INSTANT),
        (Count(value=1), ValueKind.COUNT),
        (
            Ratio(numerator=1, denominator=PositiveCount(value=2)),
            ValueKind.RATIO,
        ),
        (BodyId(value=UUID(int=1)), ValueKind.BODY_HANDLE),
        (ViewId(value=UUID(int=2)), ValueKind.VIEW_HANDLE),
        (MediaType(type="application", subtype="json"), ValueKind.MEDIA_TYPE),
        (HttpFieldTextValue(value="application/json; charset=utf-8"), ValueKind.HTTP_FIELD_VALUE),
        (BareQueryFieldValue(), ValueKind.HTTP_FIELD_VALUE),
        (
            HttpFieldItems(items=(MediaTypeTokenValue(value="gzip"),)),
            ValueKind.HTTP_FIELD_ITEMS,
        ),
    ],
)
def test_every_concrete_value_kind_has_one_runtime_carrier(
    value: SelectedValue,
    kind: ValueKind,
) -> None:
    assert selected_value_kind(value) is kind
    exact = ValueContract(kinds=(kind,), cardinality=SelectorCardinality.EXACTLY_ONE)
    assert contract_accepts_runtime_kind(exact, kind)
    unknown = ValueContract(
        kinds=(ValueKind.UNKNOWN,),
        cardinality=SelectorCardinality.EXACTLY_ONE,
    )
    assert contract_accepts_runtime_kind(unknown, kind)


def test_runtime_kind_contract_validation_is_strict_and_numeric_aware() -> None:
    number = ValueContract(
        kinds=(ValueKind.NUMBER,),
        cardinality=SelectorCardinality.EXACTLY_ONE,
    )
    assert contract_accepts_runtime_kind(number, ValueKind.INTEGER)
    assert not contract_accepts_runtime_kind(number, ValueKind.STRING)
    with pytest.raises(TypeError, match="ValueContract and ValueKind"):
        contract_accepts_runtime_kind(
            cast("ValueContract", object()),
            ValueKind.INTEGER,
        )


def test_http_field_items_reject_mutable_or_untyped_tokens() -> None:
    token = MediaTypeTokenValue(value="gzip")
    with pytest.raises(TypeError, match="immutable tuple"):
        HttpFieldItems(items=cast("tuple[MediaTypeTokenValue, ...]", [token]))
    with pytest.raises(TypeError, match="MediaTypeTokenValue"):
        HttpFieldItems(items=cast("tuple[MediaTypeTokenValue, ...]", (object(),)))


def _field_selector(source: SelectorSource) -> CompiledSelector:
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="4" * 64),
        ),
        source=source,
        path=NoPath(),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.ZERO_OR_MORE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(
            kinds=(ValueKind.HTTP_FIELD_VALUE,),
            cardinality=SelectorCardinality.ZERO_OR_MORE,
        ),
    )


def test_raw_field_values_preserve_non_token_text_bare_values_and_order() -> None:
    owner = EnvironmentExecutionId(value=UUID(int=20))
    header_location = HeaderLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_HEADERS,
        exchange_id=ExchangeId(value=UUID(int=21)),
        section=HttpMessageSection.RESPONSE_HEADER,
        occurrence=Count(value=0),
        name=HeaderName(value="content-type"),
    )
    header = SelectionMatch(
        value=HttpFieldTextValue(value="application/json; charset=utf-8"),
        location=header_location,
    )
    assert SelectionReady(
        selector=_field_selector(SelectorSource.RESPONSE_HEADERS),
        environment_execution_id=owner,
        matches=(header,),
    ).matches == (header,)

    query_locations = (
        NamedFieldLocation(
            environment_execution_id=owner,
            source=SelectorSource.REQUEST_QUERY,
            exchange_id=ExchangeId(value=UUID(int=22)),
            name=QueryName(value="q"),
            occurrence=Count(value=0),
        ),
        NamedFieldLocation(
            environment_execution_id=owner,
            source=SelectorSource.REQUEST_QUERY,
            exchange_id=ExchangeId(value=UUID(int=22)),
            name=QueryName(value="flag"),
            occurrence=Count(value=1),
        ),
    )
    query = (
        SelectionMatch(
            value=HttpFieldTextValue(value="café au lait"),
            location=query_locations[0],
        ),
        SelectionMatch(value=BareQueryFieldValue(), location=query_locations[1]),
    )
    assert (
        SelectionReady(
            selector=_field_selector(SelectorSource.REQUEST_QUERY),
            environment_execution_id=owner,
            matches=query,
        ).matches
        == query
    )


@pytest.mark.parametrize(
    ("source", "name", "public_text", "sealed_text"),
    [
        (SelectorSource.REQUEST_QUERY, QueryName(value="search"), "résumé 2026", "query secret"),
        (
            SelectorSource.REQUEST_HEADERS,
            HeaderName(value="x-route"),
            "/v1/items?state=ready now",
            "Bearer opaque/value",
        ),
        (SelectorSource.REQUEST_COOKIES, CookieName(value="session"), "public/value", "s3cr3t"),
    ],
)
def test_public_and_sealed_field_text_use_the_same_raw_runtime_kind(
    source: SelectorSource,
    name: QueryName | HeaderName | CookieName,
    public_text: str,
    sealed_text: str,
) -> None:
    owner = EnvironmentExecutionId(value=UUID(int=30))
    exchange = ExchangeId(value=UUID(int=31))
    locations: tuple[HeaderLocation | NamedFieldLocation, ...]
    if source is SelectorSource.REQUEST_HEADERS:
        locations = (
            HeaderLocation(
                environment_execution_id=owner,
                source=source,
                exchange_id=exchange,
                section=HttpMessageSection.REQUEST_HEADER,
                occurrence=Count(value=0),
                name=cast("HeaderName", name),
            ),
            HeaderLocation(
                environment_execution_id=owner,
                source=source,
                exchange_id=exchange,
                section=HttpMessageSection.REQUEST_HEADER,
                occurrence=Count(value=1),
                name=cast("HeaderName", name),
            ),
        )
    else:
        locations = tuple(
            NamedFieldLocation(
                environment_execution_id=owner,
                source=source,
                exchange_id=exchange,
                name=cast("QueryName | CookieName", name),
                occurrence=Count(value=index),
            )
            for index in range(2)
        )
    public = SelectionMatch(value=HttpFieldTextValue(value=public_text), location=locations[0])
    sealed = SelectionMatch(
        value=SensitiveValue(
            HttpFieldTextValue(value=sealed_text),
            origins=(TaintOriginId(value="field/source"),),
        ),
        location=locations[1],
    )
    ready = SelectionReady(
        selector=_field_selector(source),
        environment_execution_id=owner,
        matches=(public, sealed),
    )
    assert ready.matches == (public, sealed)
    assert public.sensitivity.value == "public"
    assert sealed.sensitivity.value == "sensitive"


def test_raw_field_carriers_reject_invalid_or_misbound_bare_values() -> None:
    with pytest.raises(TypeError, match="must be text"):
        HttpFieldTextValue(value=cast("str", object()))
    with pytest.raises(ValueError, match="Unicode scalar"):
        HttpFieldTextValue(value="\ud800")

    owner = EnvironmentExecutionId(value=UUID(int=40))
    header_location = HeaderLocation(
        environment_execution_id=owner,
        source=SelectorSource.RESPONSE_HEADERS,
        exchange_id=ExchangeId(value=UUID(int=41)),
        section=HttpMessageSection.RESPONSE_HEADER,
        occurrence=Count(value=0),
        name=HeaderName(value="x-flag"),
    )
    with pytest.raises(ValueError, match="request-query"):
        SelectionReady(
            selector=_field_selector(SelectorSource.RESPONSE_HEADERS),
            environment_execution_id=owner,
            matches=(SelectionMatch(value=BareQueryFieldValue(), location=header_location),),
        )

    query_location = NamedFieldLocation(
        environment_execution_id=owner,
        source=SelectorSource.REQUEST_QUERY,
        exchange_id=ExchangeId(value=UUID(int=42)),
        name=QueryName(value="flag"),
        occurrence=Count(value=0),
    )
    with pytest.raises(ValueError, match="cannot carry sensitive"):
        SelectionReady(
            selector=_field_selector(SelectorSource.REQUEST_QUERY),
            environment_execution_id=owner,
            matches=(
                SelectionMatch(
                    value=SensitiveValue(
                        BareQueryFieldValue(),
                        origins=(TaintOriginId(value="field/source"),),
                    ),
                    location=query_location,
                ),
            ),
        )
