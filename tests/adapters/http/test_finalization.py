"""Typed production request finalization and authentication tests."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import msgspec
import pytest

from comparo.adapters.http.finalization import HttpRequestFinalizer
from comparo.adapters.http.finalization import _FingerprintSink
from comparo.adapters.http.materialization import BodyMaterializationLimits
from comparo.adapters.http.materialization import RequestBodyMaterializer
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import ApiKeyCredential
from comparo.core.domain.http.auth import Authentication
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
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import FormField
from comparo.core.domain.http.bodies import FormName
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from tests.core.domain.http.observation_factories import request

_ACCESS = SensitiveValueAccess(purpose="test.request-finalization")
_AUTH_ORIGIN = TaintOriginId(value="test/auth")
_USER_ORIGIN = TaintOriginId(value="test/auth-user")


def _body_id(value: int) -> BodyId:
    return BodyId(value=UUID(int=value))


def _store(tmp_path: Path) -> TransientBodyStore:
    return TransientBodyStore(
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=32),
            maximum_transient_bytes=ByteCount(value=65_536),
            io_chunk_size=PositiveCount(value=8),
            root=tmp_path,
        )
    )


def _finalizer(tmp_path: Path) -> tuple[HttpRequestFinalizer, TransientBodyStore]:
    store = _store(tmp_path)
    materializer = RequestBodyMaterializer(
        store,
        BodyMaterializationLimits(
            maximum_body_bytes=ByteCount(value=8_192),
            maximum_json_depth=PositiveCount(value=16),
            maximum_json_nodes=PositiveCount(value=128),
        ),
    )
    return HttpRequestFinalizer(materializer), store


def _effective(body: RequestBody | None = None) -> EffectiveRequest:
    return msgspec.structs.replace(request(), body=body or NoBody())


def _secret[ValueT](value: ValueT, origin: TaintOriginId = _AUTH_ORIGIN) -> SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=(origin,))


def test_finalizer_validates_its_explicit_boundaries(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="BodyMaterializer"):
        HttpRequestFinalizer(object())  # type: ignore[arg-type]
    finalizer, store = _finalizer(tmp_path)
    with pytest.raises(TypeError, match="EffectiveRequest"):
        asyncio.run(
            finalizer.finalize(
                object(),  # type: ignore[arg-type]
                NoAuthentication(),
                body_id=_body_id(1),
            )
        )
    with pytest.raises(TypeError, match="typed Authentication"):
        asyncio.run(
            finalizer.finalize(
                _effective(),
                object(),  # type: ignore[arg-type]
                body_id=_body_id(1),
            )
        )
    with pytest.raises(TypeError, match="BodyId"):
        asyncio.run(
            finalizer.finalize(
                _effective(),
                NoAuthentication(),
                body_id=object(),  # type: ignore[arg-type]
            )
        )
    asyncio.run(store.close())


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (NoBody(), None),
        (JsonBody(value=JsonString(value="value")), "application/json"),
        (TextBody(value=BodyText(value="text")), "text/plain; charset=utf-8"),
        (BytesBody(value=BytesValue(value=b"bytes")), None),
        (
            BytesBody(
                value=BytesValue(value=b"bytes"),
                media_type=MediaType(type="application", subtype="octet-stream"),
            ),
            "application/octet-stream",
        ),
        (
            FormBody(fields=(FormField(name=FormName(value="q"), value=BodyText(value="x")),)),
            "application/x-www-form-urlencoded",
        ),
        (
            MultipartBody(boundary=MultipartBoundary(value="boundary"), parts=()),
            "multipart/form-data; boundary=boundary",
        ),
    ],
)
def test_finalizer_materializes_all_body_families_and_adds_only_default_media(
    tmp_path: Path,
    body: RequestBody,
    expected: str | None,
) -> None:
    async def scenario() -> None:
        finalizer, store = _finalizer(tmp_path)
        finalized = await finalizer.finalize(
            _effective(body),
            NoAuthentication(),
            body_id=_body_id(10),
        )
        content_types = tuple(
            field for field in finalized.headers.fields if field.name.value == "content-type"
        )
        if expected is None:
            assert content_types == ()
        else:
            assert len(content_types) == 1
            assert content_types[0].value == HeaderText(value=expected)
            assert content_types[0].source is HeaderSource.BODY
        if type(body) is NoBody:
            assert type(finalized.materialization.body) is not MaterializedBody
        else:
            assert type(finalized.body) is MaterializedBody
        assert type(finalized.materialization.identity.effective_request_fingerprint) is Fingerprint
        await store.close()

    asyncio.run(scenario())


def test_authentication_variants_generate_typed_sensitive_fields(tmp_path: Path) -> None:
    async def scenario() -> None:
        authentications: tuple[Authentication, ...] = (
            BearerAuthentication(token=_secret(BearerToken(value="token-value"))),
            BasicAuthentication(
                username=BasicUsername(value="user"),
                password=_secret(BasicPassword(value="password")),
                wire_encoding=BasicWireEncoding.UTF_8,
            ),
            BasicAuthentication(
                username=_secret(BasicUsername(value="private-user"), _USER_ORIGIN),
                password=_secret(BasicPassword(value="password")),
                wire_encoding=BasicWireEncoding.UTF_8,
            ),
            ApiKeyAuthentication(
                credential=_secret(ApiKeyCredential(value="api-key")),
                placement=HeaderApiKeyPlacement(name=HeaderName(value="x-api-key")),
            ),
            ApiKeyAuthentication(
                credential=_secret(ApiKeyCredential(value="api-key")),
                placement=QueryApiKeyPlacement(name=QueryName(value="token")),
            ),
            ApiKeyAuthentication(
                credential=_secret(ApiKeyCredential(value="api-key")),
                placement=CookieApiKeyPlacement(name=CookieName(value="session")),
            ),
        )
        for index, authentication in enumerate(authentications, start=20):
            finalizer, store = _finalizer(tmp_path)
            finalized = await finalizer.finalize(
                _effective(),
                authentication,
                body_id=_body_id(index),
            )
            fingerprint = finalized.materialization.identity.effective_request_fingerprint
            assert is_sensitive_value(fingerprint)
            assert fingerprint.payload_type is Fingerprint
            if (
                type(authentication) is BearerAuthentication
                or type(authentication) is BasicAuthentication
            ):
                field = finalized.headers.fields[-1]
                assert field.name.value == "authorization"
                assert field.source is HeaderSource.AUTHENTICATION
                assert is_sensitive_value(field.value)
                if type(authentication) is BasicAuthentication and is_sensitive_value(
                    authentication.username
                ):
                    assert field.value.origins == (_USER_ORIGIN, _AUTH_ORIGIN)
            else:
                assert type(authentication) is ApiKeyAuthentication
                placement = authentication.placement
                if type(placement) is HeaderApiKeyPlacement:
                    assert finalized.headers.fields[-1].name.value == "x-api-key"
                elif type(placement) is QueryApiKeyPlacement:
                    parameter = finalized.url.query.parameters[-1]
                    assert parameter.source is QuerySource.AUTHENTICATION
                    assert is_sensitive_value(parameter.value)
                else:
                    cookie = finalized.cookies.cookies[-1]
                    assert cookie.source is CookieSource.AUTHENTICATION
                    assert is_sensitive_value(cookie.value)
            await store.close()

    asyncio.run(scenario())


def test_auth_and_media_collisions_fail_before_transport(tmp_path: Path) -> None:
    async def scenario() -> None:
        finalizer, store = _finalizer(tmp_path)
        authorization = HeaderField(
            name=HeaderName(value="authorization"),
            value=_secret(HeaderText(value="already-present")),
            source=HeaderSource.AUTHENTICATION,
            provenance=_effective().provenance,
        )
        colliding = msgspec.structs.replace(
            _effective(),
            headers=msgspec.structs.replace(
                _effective().headers,
                fields=(authorization,),
            ),
        )
        with pytest.raises(ValueError, match="already contains"):
            await finalizer.finalize(
                colliding,
                BearerAuthentication(token=_secret(BearerToken(value="token"))),
                body_id=_body_id(40),
            )

        query_finalized = await finalizer.finalize(
            _effective(),
            ApiKeyAuthentication(
                credential=_secret(ApiKeyCredential(value="query-key")),
                placement=QueryApiKeyPlacement(name=QueryName(value="token")),
            ),
            body_id=_body_id(43),
        )
        with pytest.raises(ValueError, match="already contains"):
            await finalizer.finalize(
                msgspec.structs.replace(_effective(), url=query_finalized.url),
                NoAuthentication(),
                body_id=_body_id(44),
            )

        cookie_finalized = await finalizer.finalize(
            _effective(),
            ApiKeyAuthentication(
                credential=_secret(ApiKeyCredential(value="cookie-key")),
                placement=CookieApiKeyPlacement(name=CookieName(value="session")),
            ),
            body_id=_body_id(45),
        )
        with pytest.raises(ValueError, match="already contains"):
            await finalizer.finalize(
                msgspec.structs.replace(
                    _effective(),
                    cookies=cookie_finalized.cookies,
                ),
                NoAuthentication(),
                body_id=_body_id(46),
            )

        content_type = HeaderField(
            name=HeaderName(value="content-type"),
            value=HeaderText(value="application/custom"),
            source=HeaderSource.AUTHORED,
            provenance=_effective().provenance,
        )
        json_request = msgspec.structs.replace(
            _effective(JsonBody(value=JsonString(value="x"))),
            headers=msgspec.structs.replace(_effective().headers, fields=(content_type,)),
        )
        finalized = await finalizer.finalize(
            json_request,
            NoAuthentication(),
            body_id=_body_id(41),
        )
        assert finalized.headers.fields == (content_type,)

        multipart = msgspec.structs.replace(
            _effective(MultipartBody(boundary=MultipartBoundary(value="boundary"), parts=())),
            headers=msgspec.structs.replace(_effective().headers, fields=(content_type,)),
        )
        with pytest.raises(ValueError, match="owned by body finalization"):
            await finalizer.finalize(
                multipart,
                NoAuthentication(),
                body_id=_body_id(42),
            )
        await store.close()

    asyncio.run(scenario())


def test_effective_fingerprint_is_deterministic_sensitive_and_exact(tmp_path: Path) -> None:
    async def scenario() -> None:
        finalizer, store = _finalizer(tmp_path)
        auth = BearerAuthentication(token=_secret(BearerToken(value="token-one")))
        first = await finalizer.finalize(_effective(), auth, body_id=_body_id(50))
        second = await finalizer.finalize(_effective(), auth, body_id=_body_id(51))
        changed = await finalizer.finalize(
            _effective(),
            BearerAuthentication(token=_secret(BearerToken(value="token-two"))),
            body_id=_body_id(52),
        )
        first_fingerprint = first.materialization.identity.effective_request_fingerprint
        second_fingerprint = second.materialization.identity.effective_request_fingerprint
        changed_fingerprint = changed.materialization.identity.effective_request_fingerprint
        assert is_sensitive_value(first_fingerprint)
        assert is_sensitive_value(second_fingerprint)
        assert is_sensitive_value(changed_fingerprint)
        assert first_fingerprint.reveal(access=_ACCESS) == second_fingerprint.reveal(access=_ACCESS)
        assert first_fingerprint.reveal(access=_ACCESS) != changed_fingerprint.reveal(
            access=_ACCESS
        )
        await store.close()

    asyncio.run(scenario())


def test_fingerprint_sink_frames_every_supported_immutable_leaf() -> None:
    first = _FingerprintSink()
    for value in (
        None,
        True,
        "text",
        b"bytes",
        7,
        Decimal("1.25"),
        UUID(int=1),
        TlsVersion.TLS_1_2,
        ("nested", 1),
        frozenset({"b", "a"}),
        HeaderName(value="accept"),
        _secret(HeaderText(value="secret")),
    ):
        first.add(value)
    second = _FingerprintSink()
    for value in (
        None,
        True,
        "text",
        b"bytes",
        7,
        Decimal("1.25"),
        UUID(int=1),
        TlsVersion.TLS_1_2,
        ("nested", 1),
        frozenset({"a", "b"}),
        HeaderName(value="accept"),
        _secret(HeaderText(value="secret")),
    ):
        second.add(value)
    assert first.hexdigest() == second.hexdigest()
    with pytest.raises(TypeError, match="unsupported fingerprint value"):
        first.add(object())
