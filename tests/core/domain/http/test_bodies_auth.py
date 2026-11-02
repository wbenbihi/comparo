"""Tests for immutable body variants, byte sources, and typed authentication."""

from __future__ import annotations

from uuid import uuid4

import pytest

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import ApiKeyCredential
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BasicPassword
from comparo.core.domain.http.auth import BasicUsername
from comparo.core.domain.http.auth import BasicWireEncoding
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import BearerToken
from comparo.core.domain.http.auth import CookieApiKeyPlacement
from comparo.core.domain.http.auth import HeaderApiKeyPlacement
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.auth import QueryApiKeyPlacement
from comparo.core.domain.http.auth import is_authentication
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import FormField
from comparo.core.domain.http.bodies import FormName
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import JsonSerialization
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import MultipartDispositionName
from comparo.core.domain.http.bodies import MultipartFilePart
from comparo.core.domain.http.bodies import MultipartTextPart
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.bodies import is_request_body
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from tests.core.domain.http.factories import api_key_secret
from tests.core.domain.http.factories import bearer_secret
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import password_secret
from tests.core.domain.http.factories import provenance


def test_media_type_is_structured_canonical_and_unique() -> None:
    charset = MediaTypeParameter(name="charset", value=MediaTypeTokenValue(value="utf-8"))
    media_type = MediaType(type="application", subtype="json", parameters=(charset,))
    assert media_type.parameters == (charset,)

    with pytest.raises(ValueError, match="lowercase token"):
        MediaType(type="Application", subtype="json")
    with pytest.raises(ValueError, match="unique"):
        MediaType(type="text", subtype="plain", parameters=(charset, charset))
    with pytest.raises(TypeError, match="declare token or quoted"):
        MediaTypeParameter(name="charset", value="quoted value")  # type: ignore[arg-type]

    quoted = MediaTypeParameter(
        name="profile",
        value=MediaTypeQuotedValue(value='a "quoted" profile'),
    )
    rendered = render_media_type(
        MediaType(type="application", subtype="json", parameters=(quoted,))
    )
    assert rendered.value == 'application/json; profile="a \\"quoted\\" profile"'
    with pytest.raises(ValueError, match="canonical token form"):
        MediaTypeQuotedValue(value="utf-8")


def test_body_text_rejects_unencodable_unicode() -> None:
    with pytest.raises(ValueError, match="Unicode scalar"):
        BodyText(value="\ud800")


def test_repeatable_byte_source_records_stable_safe_metadata() -> None:
    source = RepeatableByteSource(
        source_id=BodyId(value=uuid4()),
        byte_count=ByteCount(value=1_000_000),
        digest=SensitiveValue(
            digest("b"),
            origins=(TaintOriginId(value="body-source"),),
        ),
        provenance=provenance(),
    )
    body = BytesBody(value=source)
    assert body.value == source
    with pytest.raises(TypeError, match="cannot be rendered"):
        repr(body)


def test_all_request_body_variants_are_closed_and_immutable() -> None:
    text = BodyText(value="hello")
    json_body = JsonBody(value=JsonString(value="hello"))
    text_body = TextBody(value=text)
    bytes_body = BytesBody(value=BytesValue(value=b"bytes"))
    form = FormBody(fields=(FormField(name=FormName(value="q"), value=text),))
    multipart = MultipartBody(
        boundary=MultipartBoundary(value="comparo-boundary"),
        parts=(
            MultipartTextPart(name=MultipartDispositionName(value="q"), value=text),
            MultipartFilePart(
                name=MultipartDispositionName(value="file"),
                filename=MultipartDispositionFilename(value="sample.txt"),
                value=BytesValue(value=b"payload"),
                media_type=MediaType(type="text", subtype="plain"),
            ),
        ),
    )

    for body in (NoBody(), json_body, text_body, bytes_body, form, multipart):
        assert is_request_body(body)
    assert not is_request_body(text)
    assert json_body.serialization is JsonSerialization.COMPARO_V1
    assert multipart.parts[1].name == MultipartDispositionName(value="file")


def test_sensitive_body_values_do_not_leak_through_repr() -> None:
    secret_text = "body-secret"
    wrapped = SensitiveValue(
        BodyText(value=secret_text),
        origins=(TaintOriginId(value="body-origin"),),
    )
    body = MultipartBody(
        boundary=MultipartBoundary(value="safe-boundary"),
        parts=(
            MultipartTextPart(
                name=MultipartDispositionName(value="message"),
                value=wrapped,
            ),
        ),
    )
    assert secret_text not in repr(body)


@pytest.mark.parametrize("boundary", ["", "contains space", "x" * 71])
def test_multipart_boundary_rejects_invalid_values(boundary: str) -> None:
    with pytest.raises(ValueError, match="boundary"):
        MultipartBoundary(value=boundary)


def test_body_containers_reject_mutable_or_untyped_components() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        FormBody(fields=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable tuple"):
        MultipartBody(
            boundary=MultipartBoundary(value="boundary"),
            parts=[],  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="BytesValue"):
        BytesBody(value="bytes")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="JsonValue"):
        JsonBody(value="json")  # type: ignore[arg-type]


def test_authentication_variants_require_sensitive_credentials_and_render_safely() -> None:
    bearer = bearer_secret("do-not-render")
    password = password_secret("do-not-render")
    api_key = api_key_secret("do-not-render")
    variants = (
        NoAuthentication(),
        BearerAuthentication(token=bearer),
        BasicAuthentication(
            username=BasicUsername(value="user"),
            password=password,
            wire_encoding=BasicWireEncoding.UTF_8,
        ),
        ApiKeyAuthentication(
            credential=api_key,
            placement=HeaderApiKeyPlacement(name=HeaderName(value="x-api-key")),
        ),
        ApiKeyAuthentication(
            credential=api_key,
            placement=QueryApiKeyPlacement(name=QueryName(value="api_key")),
        ),
        ApiKeyAuthentication(
            credential=api_key,
            placement=CookieApiKeyPlacement(name=CookieName(value="api-key")),
        ),
    )

    for authentication in variants:
        assert is_authentication(authentication)
        assert "do-not-render" not in repr(authentication)
    assert not is_authentication(BearerToken(value="raw"))


@pytest.mark.parametrize(
    "name",
    [
        "connection",
        "content-length",
        "cookie",
        "host",
        "http2-settings",
        "keep-alive",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    ],
)
def test_api_key_header_placement_rejects_transport_owned_names(name: str) -> None:
    with pytest.raises(ValueError, match="cannot occupy"):
        HeaderApiKeyPlacement(name=HeaderName(value=name))


def test_authentication_rejects_unsealed_credentials() -> None:
    with pytest.raises(TypeError, match="bearer token"):
        BearerAuthentication(token=BearerToken(value="raw"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="basic password"):
        BasicAuthentication(
            username=BasicUsername(value="user"),
            password=BasicPassword(value="raw"),  # type: ignore[arg-type]
            wire_encoding=BasicWireEncoding.UTF_8,
        )
    with pytest.raises(TypeError, match="API-key credential"):
        ApiKeyAuthentication(
            credential=ApiKeyCredential(value="raw"),  # type: ignore[arg-type]
            placement=QueryApiKeyPlacement(name=QueryName(value="key")),
        )
