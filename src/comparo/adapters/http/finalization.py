"""Typed authentication, media defaults, materialization, and request identity finalization."""

from __future__ import annotations

import base64
import hashlib
from decimal import Decimal
from enum import Enum
from typing import Protocol
from typing import cast
from uuid import UUID

import msgspec

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import Authentication
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BasicUsername
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import HeaderApiKeyPlacement
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.auth import QueryApiKeyPlacement
from comparo.core.domain.http.auth import is_authentication
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.bodies import multipart_content_type
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.http.request_materialization import EffectiveRequestFingerprint
from comparo.core.domain.http.request_materialization import effective_request_taint_origins
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union

_FINALIZATION_ACCESS = SensitiveValueAccess(purpose="http.request.finalization")
_AUTHORIZATION = HeaderName(value="authorization")
_CONTENT_TYPE = HeaderName(value="content-type")


class BodyMaterializer(Protocol):
    """Produce one complete retry-safe body snapshot from a typed request body."""

    async def materialize(
        self,
        body: RequestBody,
        *,
        body_id: BodyId,
        provenance: ValueProvenance,
    ) -> FinalizedBody:
        """Materialize a body without changing its semantic representation."""
        ...


class HttpRequestFinalizer:
    """Finalize one Environment-effective request behind a narrow body-materializer port."""

    __slots__ = ("_materializer",)

    def __init__(self, materializer: BodyMaterializer) -> None:
        """Bind a run-local body materializer without assuming its storage implementation."""
        if not hasattr(materializer, "materialize"):
            raise TypeError("HTTP request finalizer requires BodyMaterializer")
        self._materializer = materializer

    async def finalize(
        self,
        request: EffectiveRequest,
        authentication: Authentication,
        *,
        body_id: BodyId,
    ) -> FinalizedWireRequest:
        """Apply credentials and media defaults, then snapshot and bind exact wire intent."""
        if type(request) is not EffectiveRequest:
            raise TypeError("HTTP request finalization requires EffectiveRequest")
        if not is_authentication(authentication):
            raise TypeError("HTTP request finalization requires typed Authentication")
        if type(body_id) is not BodyId:
            raise TypeError("HTTP request finalization requires BodyId")
        effective = _apply_authentication(request, authentication)
        effective = _apply_body_content_type(effective)
        body = await self._materializer.materialize(
            effective.body,
            body_id=body_id,
            provenance=effective.provenance,
        )
        fingerprint = _effective_fingerprint(effective, body)
        proof = prove_request_materialization(
            effective,
            body,
            effective_request_fingerprint=fingerprint,
        )
        return FinalizedWireRequest(
            environment=effective.environment,
            logical_request_fingerprint=effective.logical_request_fingerprint,
            method=effective.method,
            url=effective.url,
            headers=effective.headers,
            cookies=effective.cookies,
            body=body,
            materialization=proof,
            policies=effective.policies,
            provenance=effective.provenance,
        )


def _apply_authentication(
    request: EffectiveRequest,
    authentication: Authentication,
) -> EffectiveRequest:
    if (
        any(field.source is HeaderSource.AUTHENTICATION for field in request.headers.fields)
        or any(
            parameter.source is QuerySource.AUTHENTICATION
            for parameter in request.url.query.parameters
        )
        or any(cookie.source is CookieSource.AUTHENTICATION for cookie in request.cookies.cookies)
    ):
        raise ValueError("effective request already contains authentication-generated fields")
    if type(authentication) is NoAuthentication:
        return request
    if type(authentication) is BearerAuthentication:
        token = authentication.token.reveal(access=_FINALIZATION_ACCESS)
        return _with_header(
            request,
            _AUTHORIZATION,
            SensitiveValue(
                HeaderText(value=f"Bearer {token.value}"),
                origins=authentication.token.origins,
            ),
        )
    if type(authentication) is BasicAuthentication:
        return _apply_basic(request, authentication)
    if type(authentication) is ApiKeyAuthentication:
        return _apply_api_key(request, authentication)
    raise TypeError("unsupported typed authentication")  # pragma: no cover - closed union


def _apply_basic(
    request: EffectiveRequest,
    authentication: BasicAuthentication,
) -> EffectiveRequest:
    username = authentication.username
    if is_sensitive_value(username):
        exact_username = cast(
            BasicUsername,
            username.reveal(access=_FINALIZATION_ACCESS),
        )
        username_origins = username.origins
    else:
        exact_username = cast(BasicUsername, username)
        username_origins = ()
    password = authentication.password.reveal(access=_FINALIZATION_ACCESS)
    encoded = base64.b64encode(
        f"{exact_username.value}:{password.value}".encode(authentication.wire_encoding.value)
    ).decode("ascii")
    return _with_header(
        request,
        _AUTHORIZATION,
        SensitiveValue(
            HeaderText(value=f"Basic {encoded}"),
            origins=stable_taint_origin_union(
                username_origins,
                authentication.password.origins,
            ),
        ),
    )


def _apply_api_key(
    request: EffectiveRequest,
    authentication: ApiKeyAuthentication,
) -> EffectiveRequest:
    credential = authentication.credential.reveal(access=_FINALIZATION_ACCESS)
    origins = authentication.credential.origins
    placement = authentication.placement
    if type(placement) is HeaderApiKeyPlacement:
        return _with_header(
            request,
            placement.name,
            SensitiveValue(HeaderText(value=credential.value), origins=origins),
        )
    if type(placement) is QueryApiKeyPlacement:
        parameter = QueryParameter(
            name=placement.name,
            value=SensitiveValue(QueryText(value=credential.value), origins=origins),
            source=QuerySource.AUTHENTICATION,
            provenance=request.provenance,
        )
        return msgspec.structs.replace(
            request,
            url=msgspec.structs.replace(
                request.url,
                query=msgspec.structs.replace(
                    request.url.query,
                    parameters=(*request.url.query.parameters, parameter),
                ),
            ),
        )
    cookie = RequestCookie(
        name=placement.name,
        value=SensitiveValue(CookieText(value=credential.value), origins=origins),
        source=CookieSource.AUTHENTICATION,
        provenance=request.provenance,
    )
    return msgspec.structs.replace(
        request,
        cookies=RequestCookies(cookies=(*request.cookies.cookies, cookie)),
    )


def _with_header(
    request: EffectiveRequest,
    name: HeaderName,
    value: HeaderText | SensitiveValue[HeaderText],
) -> EffectiveRequest:
    field = HeaderField(
        name=name,
        value=value,
        source=HeaderSource.AUTHENTICATION,
        provenance=request.provenance,
    )
    return msgspec.structs.replace(
        request,
        headers=RequestHeaders(fields=(*request.headers.fields, field)),
    )


def _apply_body_content_type(request: EffectiveRequest) -> EffectiveRequest:
    if any(field.name == _CONTENT_TYPE for field in request.headers.fields):
        if type(request.body) is MultipartBody:
            raise ValueError("multipart Content-Type is owned by body finalization")
        return request
    value = _body_content_type(request.body)
    if value is None:
        return request
    field = HeaderField(
        name=_CONTENT_TYPE,
        value=value,
        source=HeaderSource.BODY,
        provenance=request.provenance,
    )
    return msgspec.structs.replace(
        request,
        headers=RequestHeaders(fields=(*request.headers.fields, field)),
    )


def _body_content_type(body: RequestBody) -> HeaderText | None:
    if type(body) is NoBody:
        return None
    if type(body) is JsonBody:
        return render_media_type(body.media_type or MediaType(type="application", subtype="json"))
    if type(body) is TextBody:
        media = body.media_type or MediaType(
            type="text",
            subtype="plain",
            parameters=(
                MediaTypeParameter(
                    name="charset",
                    value=MediaTypeTokenValue(value="utf-8"),
                ),
            ),
        )
        return render_media_type(media)
    if type(body) is BytesBody:
        return render_media_type(body.media_type) if body.media_type is not None else None
    if type(body) is FormBody:
        return HeaderText(value="application/x-www-form-urlencoded")
    return multipart_content_type(body.boundary)


def _effective_fingerprint(
    request: EffectiveRequest,
    body: FinalizedBody,
) -> EffectiveRequestFingerprint:
    sink = _FingerprintSink()
    sink.add(request)
    if type(body) is MaterializedBody:
        sink.add(body.source.byte_count)
        sink.add(body.source.digest)
        sink.add(body.multipart)
    else:
        sink.add(None)
    value = Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=sink.hexdigest()),
    )
    origins = effective_request_taint_origins(
        request.url,
        request.headers,
        request.cookies,
        request.body,
    )
    return SensitiveValue(value, origins=origins) if origins else value


class _FingerprintSink:
    """Length-frame immutable domain values into one process-independent SHA-256 identity."""

    __slots__ = ("_digest",)

    def __init__(self) -> None:
        self._digest = hashlib.sha256()

    def add(self, value: object) -> None:  # noqa: C901 - closed immutable graph visitor
        """Add one closed immutable value with explicit type and collection framing."""
        if is_sensitive_value(value):
            self._token(b"sensitive")
            self.add(value.origins)
            self.add(value.reveal(access=_FINALIZATION_ACCESS))
            return
        if value is None:
            self._token(b"none")
            return
        if type(value) is bool:
            self._token(b"true" if value else b"false")
            return
        if type(value) in {str, bytes, int, Decimal, UUID}:
            self._token(type(value).__name__.encode("ascii"))
            content = value if type(value) is bytes else str(value).encode("utf-8")
            self._token(content)
            return
        if isinstance(value, Enum):
            self._token(_type_name(value))
            self._token(str(value.value).encode("utf-8"))
            return
        if type(value) is tuple:
            self._token(b"tuple")
            self._token(str(len(value)).encode("ascii"))
            for item in value:
                self.add(item)
            return
        if type(value) is frozenset:
            self._token(b"frozenset")
            encoded = sorted(_isolated_digest(item) for item in value)
            for item in encoded:
                self._token(item)
            return
        if isinstance(value, msgspec.Struct):
            self._token(_type_name(value))
            for field in msgspec.structs.fields(value):
                self._token(field.name.encode("utf-8"))
                self.add(getattr(value, field.name))
            return
        raise TypeError("effective request contains an unsupported fingerprint value")

    def _token(self, value: bytes) -> None:
        self._digest.update(len(value).to_bytes(8, "big"))
        self._digest.update(value)

    def hexdigest(self) -> str:
        """Return the finished lower-case SHA-256 identity."""
        return self._digest.hexdigest()


def _type_name(value: object) -> bytes:
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}".encode()


def _isolated_digest(value: object) -> bytes:
    sink = _FingerprintSink()
    sink.add(value)
    return bytes.fromhex(sink.hexdigest())


__all__ = ("BodyMaterializer", "HttpRequestFinalizer")
