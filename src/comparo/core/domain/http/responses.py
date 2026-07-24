"""Immutable terminal HTTP response observations."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.http.fields import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HeaderValue
from comparo.core.domain.http.fields import is_header_value
from comparo.core.domain.http.response_bodies import ContentCoding
from comparo.core.domain.http.response_bodies import InvalidContentEncodingReason
from comparo.core.domain.http.response_bodies import MediaTypeMetadata
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ResponseBody
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import is_media_type_metadata
from comparo.core.domain.http.response_metadata import derive_content_encodings
from comparo.core.domain.http.response_metadata import derive_media_type
from comparo.core.domain.http.transport import EndpointMetadata
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsNotPerformed
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.sensitivity import is_sensitive_value


@final
class HttpStatus(msgspec.Struct, frozen=True, kw_only=True):
    """A three-digit HTTP status code, independent from success policy."""

    value: int

    def __post_init__(self) -> None:
        """Require the protocol-defined three-digit status range."""
        if type(self.value) is not int:
            raise TypeError("HTTP status must be an integer")
        if not 100 <= self.value <= 599:
            raise ValueError("HTTP status must be between 100 and 599")


@final
class ReasonPhrase(msgspec.Struct, frozen=True, kw_only=True):
    """Optional HTTP/1 reason text retained only as observed metadata."""

    value: str

    def __post_init__(self) -> None:
        """Exclude control and line-injection characters."""
        if type(self.value) is not str or any(
            ord(character) > 255
            or (ord(character) < 32 and character != "\t")
            or ord(character) == 127
            for character in self.value
        ):
            raise ValueError("reason phrase contains a forbidden character")


@final
class ResponseField(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered response header or trailer occurrence."""

    name: HeaderName
    value: HeaderValue

    def __post_init__(self) -> None:
        """Require validated field names and sensitivity-preserving values."""
        if type(self.name) is not HeaderName:
            raise TypeError("response field name must be HeaderName")
        if not is_header_value(self.value):
            raise TypeError("response field value must be a supported HeaderValue")
        if is_sensitive_value(self.value) and self.value.payload_type is not HeaderText:
            raise TypeError("sensitive response field payload must be HeaderText")
        if self.name.value in MANDATORY_SENSITIVE_HEADER_NAMES | {
            "set-cookie"
        } and not is_sensitive_value(self.value):
            raise ValueError("known credential response fields must use sealed sensitive values")


@final
class ResponseFields(msgspec.Struct, frozen=True, kw_only=True):
    """An immutable ordered, duplicate-preserving HTTP field multimap."""

    fields: tuple[ResponseField, ...] = ()

    def __post_init__(self) -> None:
        """Reject mutable containers and non-response field entries."""
        if type(self.fields) is not tuple or any(
            type(field) is not ResponseField for field in self.fields
        ):
            raise TypeError("response fields must be an immutable tuple of ResponseField values")


@final
class InformationalResponse(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered informational response preceding a terminal response."""

    protocol: HttpProtocol
    status: HttpStatus
    headers: ResponseFields = ResponseFields()

    def __post_init__(self) -> None:
        """Require a genuine 1xx response and typed metadata."""
        if type(self.protocol) is not HttpProtocol:
            raise TypeError("informational protocol must be HttpProtocol")
        if (
            type(self.status) is not HttpStatus
            or not 100 <= self.status.value < 200
            or self.status.value == 101
        ):
            raise ValueError("preceding informational status must be 1xx other than 101")
        if type(self.headers) is not ResponseFields:
            raise TypeError("informational headers must be ResponseFields")


def _validate_response_collections(
    informational: tuple[InformationalResponse, ...],
    headers: ResponseFields,
    trailers: ResponseFields,
    encodings: tuple[ContentCoding, ...],
    diagnostics: tuple[Diagnostic, ...],
) -> None:
    if type(informational) is not tuple or any(
        type(item) is not InformationalResponse for item in informational
    ):
        raise TypeError("informational responses must be an immutable typed tuple")
    if type(headers) is not ResponseFields or type(trailers) is not ResponseFields:
        raise TypeError("response headers and trailers must be ResponseFields")
    if type(encodings) is not tuple or any(type(item) is not ContentCoding for item in encodings):
        raise TypeError("content encodings must be an immutable tuple of ContentCoding")
    if type(diagnostics) is not tuple or any(
        type(diagnostic) is not Diagnostic for diagnostic in diagnostics
    ):
        raise TypeError("response diagnostics must be an immutable typed tuple")


def _validate_tls(
    endpoint: EndpointMetadata,
    tls: TlsLayers,
) -> None:
    if type(tls) is not TlsLayers:
        raise TypeError("response TLS state must use explicit TLS layers")
    if endpoint.destination.scheme is HttpScheme.HTTP and type(tls.connect) is not TlsNotPerformed:
        raise ValueError(
            "a clear-text connect destination must record connect TLS as not performed"
        )
    if endpoint.destination.scheme is HttpScheme.HTTPS and type(tls.connect) is TlsNotPerformed:
        raise ValueError("an HTTPS connect destination cannot omit connect TLS")


def _validate_response_protocol(
    protocol: HttpProtocol,
    status: HttpStatus,
    reason: ReasonPhrase | None,
    informational: tuple[InformationalResponse, ...],
) -> None:
    """Reject states that cannot occur on one negotiated HTTP connection."""
    if type(protocol) is not HttpProtocol or type(status) is not HttpStatus:
        raise TypeError("response protocol and status must be typed HTTP values")
    if 100 <= status.value < 200 and status.value != 101:
        raise ValueError("only 101 may be represented as a terminal informational status")
    if status.value == 101 and protocol is HttpProtocol.HTTP_2:
        raise ValueError("HTTP/2 cannot represent a 101 Switching Protocols response")
    if reason is not None and type(reason) is not ReasonPhrase:
        raise TypeError("response reason must be ReasonPhrase when present")
    if reason is not None and protocol is HttpProtocol.HTTP_2:
        raise ValueError("HTTP/2 responses cannot carry a reason phrase")
    if any(item.protocol is not protocol for item in informational):
        raise ValueError("informational and terminal responses must use one HTTP protocol")


def _validate_response_body(media: MediaTypeMetadata, body: ResponseBody) -> None:
    """Bind response media metadata to the retained semantic entity."""
    if not is_media_type_metadata(media):
        raise TypeError("response media must be MediaTypeMetadata")
    if type(body) not in {NoResponseBody, ResponseEntityBody}:
        raise TypeError("response body must be a supported ResponseBody")
    if type(body) is ResponseEntityBody and body.handle is not None and body.handle.media != media:
        raise ValueError("body handle and response media metadata must agree")


def _validate_identity_coding_extent(
    body: ResponseBody,
    content_encodings: tuple[ContentCoding, ...],
) -> None:
    """Reconcile known wire and entity byte counts when no content coding transforms bytes."""
    identity_only = not content_encodings or all(
        coding.value == "identity" for coding in content_encodings
    )
    if (
        identity_only
        and type(body) is ResponseEntityBody
        and body.handle is not None
        and body.encoded_byte_count is not None
        and body.encoded_byte_count != body.handle.byte_count
    ):
        raise ValueError("uncoded response encoded and entity byte counts must be equal")


@final
class ResponseObservation(msgspec.Struct, frozen=True, kw_only=True):
    """A terminal response retaining all facts available before any later failure."""

    protocol: HttpProtocol
    status: HttpStatus
    reason: ReasonPhrase | None
    informational: tuple[InformationalResponse, ...]
    headers: ResponseFields
    trailers: ResponseFields
    media: MediaTypeMetadata
    content_encodings: tuple[ContentCoding, ...]
    body: ResponseBody
    endpoint: EndpointMetadata
    tls: TlsLayers
    diagnostics: tuple[Diagnostic, ...] = ()
    content_encoding_error: InvalidContentEncodingReason | None = None

    def __post_init__(self) -> None:
        """Enforce typed metadata while leaving status policy to evaluators."""
        _validate_response_collections(
            self.informational,
            self.headers,
            self.trailers,
            self.content_encodings,
            self.diagnostics,
        )
        _validate_response_protocol(
            self.protocol,
            self.status,
            self.reason,
            self.informational,
        )
        _validate_response_body(self.media, self.body)
        if self.media != derive_media_type(self.headers):
            raise ValueError("response media metadata must be derived from Content-Type fields")
        derived_codings, derived_error = derive_content_encodings(self.headers)
        if (
            self.content_encodings != derived_codings
            or self.content_encoding_error is not derived_error
        ):
            raise ValueError(
                "response content encodings must be derived from Content-Encoding fields"
            )
        _validate_identity_coding_extent(self.body, self.content_encodings)
        if type(self.endpoint) is not EndpointMetadata:
            raise TypeError("response endpoint must be EndpointMetadata")
        _validate_tls(self.endpoint, self.tls)
