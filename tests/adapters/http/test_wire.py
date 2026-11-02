"""Exact request wire-rendering contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import msgspec
import pytest

from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.adapters.http.wire import RequestWireRenderer
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import materialization_fingerprint
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.observation_factories import finalized_wire_request
from tests.core.domain.http.observation_factories import request

_ORIGIN = TaintOriginId(value="test/wire-secret")


def _store(tmp_path: Path) -> TransientBodyStore:
    return TransientBodyStore(
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=4),
            maximum_transient_bytes=ByteCount(value=1_024),
            io_chunk_size=PositiveCount(value=2),
            root=tmp_path,
        )
    )


def test_renderer_rejects_untyped_dependencies_and_requests(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="body source"):
        RequestWireRenderer(object())  # type: ignore[arg-type]
    store = _store(tmp_path)
    with pytest.raises(TypeError, match="FinalizedWireRequest"):
        RequestWireRenderer(store).render(object())  # type: ignore[arg-type]
    asyncio.run(store.close())


def test_renderer_preserves_duplicates_encoding_bare_values_and_secrets(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        effective = request()
        url = msgspec.structs.replace(
            effective.url,
            authority=HttpAuthority(
                host=Ipv6Host(value="2001:db8::1"),
                port=HttpPort(value=443),
            ),
            path=UrlPath(
                segments=(
                    DecodedPathSegment(value="chat room"),
                    EncodedPathSegment(value="already%2Fencoded"),
                    SensitiveValue(DecodedPathSegment(value="sëcret"), origins=(_ORIGIN,)),
                ),
                trailing_slash=True,
            ),
            query=QueryParameters(
                parameters=(
                    QueryParameter(
                        name=QueryName(value="tag"),
                        value=QueryText(value="one two"),
                        source=QuerySource.AUTHORED,
                        provenance=provenance(),
                    ),
                    QueryParameter(
                        name=QueryName(value="tag"),
                        value=SensitiveValue(QueryText(value="three/four"), origins=(_ORIGIN,)),
                        source=QuerySource.AUTHORED,
                        provenance=provenance(),
                    ),
                    QueryParameter(
                        name=QueryName(value="present"),
                        value=BareQueryValue(),
                        source=QuerySource.AUTHORED,
                        provenance=provenance(),
                    ),
                )
            ),
        )
        effective = msgspec.structs.replace(
            effective,
            url=url,
            headers=RequestHeaders(
                fields=(
                    HeaderField(
                        name=HeaderName(value="x-repeat"),
                        value=HeaderText(value="one"),
                        source=HeaderSource.AUTHORED,
                        provenance=provenance(),
                    ),
                    HeaderField(
                        name=HeaderName(value="x-repeat"),
                        value=SensitiveValue(HeaderText(value="two"), origins=(_ORIGIN,)),
                        source=HeaderSource.AUTHORED,
                        provenance=provenance(),
                    ),
                )
            ),
            cookies=RequestCookies(
                cookies=(
                    RequestCookie(
                        name=CookieName(value="session"),
                        value=SensitiveValue(CookieText(value="abc123"), origins=(_ORIGIN,)),
                        source=CookieSource.EXPLICIT,
                        provenance=provenance(),
                    ),
                )
            ),
        )
        wire = RequestWireRenderer(store).render(finalized_wire_request(effective))
        assert bytes(wire.url) == (
            b"https://2001:db8::1:443/chat%20room/already%2Fencoded/"
            b"s%C3%ABcret/?tag=one%20two&tag=three%2Ffour&present"
        )
        assert wire.url.target == (
            b"/chat%20room/already%2Fencoded/s%C3%ABcret/?tag=one%20two&tag=three%2Ffour&present"
        )
        assert wire.headers == (
            (b"Host", b"[2001:db8::1]:443"),
            (b"x-repeat", b"one"),
            (b"x-repeat", b"two"),
            (b"Cookie", b"session=abc123"),
            (b"Accept-Encoding", b"gzip"),
        )
        assert wire.content is None
        await store.close()

    asyncio.run(scenario())


def test_renderer_uses_snapshotted_length_and_fresh_repeatable_streams(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        writer = await store.begin(
            BodyId(value=UUID(int=501)),
            maximum_body_bytes=ByteCount(value=32),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with writer:
            await writer.append(b"request-body")
            source = await writer.commit_request(provenance())
        effective = request()
        effective = msgspec.structs.replace(
            effective,
            headers=RequestHeaders(
                fields=(
                    HeaderField(
                        name=HeaderName(value="accept-encoding"),
                        value=HeaderText(value="identity"),
                        source=HeaderSource.AUTHORED,
                        provenance=provenance(),
                    ),
                )
            ),
            body=TextBody(value=BodyText(value="request-body")),
        )
        body = MaterializedBody(source=source)
        proof = prove_request_materialization(
            effective,
            body,
            effective_request_fingerprint=materialization_fingerprint(effective),
        )
        request_value = msgspec.structs.replace(
            finalized_wire_request(effective, body=body),
            materialization=proof,
        )
        renderer = RequestWireRenderer(store)
        first = renderer.render(request_value)
        second = renderer.render(request_value)
        assert first.headers[-1] == (b"Content-Length", b"12")
        assert sum(name.lower() == b"accept-encoding" for name, _value in first.headers) == 1
        assert first.content is not None
        assert second.content is not None
        assert b"".join([chunk async for chunk in first.content]) == b"request-body"
        assert b"".join([chunk async for chunk in second.content]) == b"request-body"
        await store.close()

    asyncio.run(scenario())
