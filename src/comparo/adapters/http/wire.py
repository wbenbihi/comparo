"""Exact HTTP wire rendering from a validated finalized request."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpcore

from comparo.adapters.http.storage import StoredBodyStream
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import effective_port
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value

_HEADER_ACCESS = SensitiveValueAccess(purpose="http.wire.header")
_COOKIE_ACCESS = SensitiveValueAccess(purpose="http.wire.cookie")
_PATH_ACCESS = SensitiveValueAccess(purpose="http.wire.path")
_QUERY_ACCESS = SensitiveValueAccess(purpose="http.wire.query")


class RequestBodySource(Protocol):
    """Open independently positioned streams for finalized request-body snapshots."""

    def open_request(self, source: RepeatableByteSource) -> StoredBodyStream:
        """Return a fresh exact reader for one materialized body."""
        ...


@dataclass(frozen=True, slots=True)
class RenderedWireRequest:
    """One transport-library request retaining exact ordered byte representations."""

    method: bytes
    url: httpcore.URL
    headers: tuple[tuple[bytes, bytes], ...]
    content: AsyncIterator[bytes] | None


class RequestWireRenderer:
    """Render only domain-validated data and Comparo-owned generated fields."""

    __slots__ = ("_body_source",)

    def __init__(self, body_source: RequestBodySource) -> None:
        """Bind repeatable body access behind its narrow read-only interface."""
        if not hasattr(body_source, "open_request"):
            raise TypeError("wire renderer requires a request body source")
        self._body_source = body_source

    def render(self, request: FinalizedWireRequest) -> RenderedWireRequest:
        """Render one finalized request without ambient cookies, proxies, or credentials."""
        if type(request) is not FinalizedWireRequest:
            raise TypeError("wire renderer requires FinalizedWireRequest")
        url = request.url
        headers: list[tuple[bytes, bytes]] = [(b"Host", _authority(request))]
        headers.extend(
            (
                field.name.value.encode("ascii"),
                _header_text(field.value).value.encode("latin-1"),
            )
            for field in request.headers.fields
        )
        if request.cookies.cookies:
            headers.append((b"Cookie", _cookies(request)))
        if not any(name.lower() == b"accept-encoding" for name, _value in headers):
            headers.append((b"Accept-Encoding", b"gzip"))

        content: AsyncIterator[bytes] | None = None
        if type(request.body) is MaterializedBody:
            headers.append(
                (b"Content-Length", str(request.body.source.byte_count.value).encode("ascii"))
            )
            content = self._body_source.open_request(request.body.source).__aiter__()
        elif type(request.body) is not NoMaterializedBody:  # pragma: no cover - domain invariant
            raise TypeError("wire request body must be finalized")

        return RenderedWireRequest(
            method=request.method.value.encode("ascii"),
            url=httpcore.URL(
                scheme=url.scheme.value.encode("ascii"),
                host=url.authority.host.value.encode("ascii"),
                port=url.authority.port.value if url.authority.port is not None else None,
                target=_target(request),
            ),
            headers=tuple(headers),
            content=content,
        )


def _authority(request: FinalizedWireRequest) -> bytes:
    authority = request.url.authority
    host = authority.host.value
    if type(authority.host) is Ipv6Host:
        host = f"[{host}]"
    if authority.port is not None:
        host = f"{host}:{effective_port(request.url).value}"
    return host.encode("ascii")


def _target(request: FinalizedWireRequest) -> bytes:
    path = request.url.path
    segments = "/".join(_path_segment(segment) for segment in path.segments)
    rendered = f"/{segments}" if segments else "/"
    if path.trailing_slash:
        rendered += "/"
    parameters = request.url.query.parameters
    if parameters:
        rendered += "?" + "&".join(_query_parameter(parameter) for parameter in parameters)
    return rendered.encode("ascii")


def _path_segment(value: object) -> str:
    exact = value.reveal(access=_PATH_ACCESS) if is_sensitive_value(value) else value
    if type(exact) is EncodedPathSegment:
        return exact.value
    if type(exact) is DecodedPathSegment:
        return quote(exact.value, safe="-._~", encoding="utf-8", errors="strict")
    raise TypeError("wire path contains an unsupported segment")  # pragma: no cover


def _query_parameter(parameter: QueryParameter) -> str:
    name = quote(parameter.name.value, safe="-._~", encoding="utf-8", errors="strict")
    value = parameter.value
    if type(value) is BareQueryValue:
        return name
    exact = value.reveal(access=_QUERY_ACCESS) if is_sensitive_value(value) else value
    if type(exact) is not QueryText:  # pragma: no cover - domain invariant
        raise TypeError("wire query contains an unsupported value")
    rendered = quote(exact.value, safe="-._~", encoding="utf-8", errors="strict")
    return f"{name}={rendered}"


def _header_text(value: object) -> HeaderText:
    exact = value.reveal(access=_HEADER_ACCESS) if is_sensitive_value(value) else value
    if type(exact) is not HeaderText:  # pragma: no cover - domain invariant
        raise TypeError("wire header contains an unsupported value")
    return exact


def _cookies(request: FinalizedWireRequest) -> bytes:
    rendered: list[str] = []
    for cookie in request.cookies.cookies:
        value = cookie.value.reveal(access=_COOKIE_ACCESS)
        if type(value) is not CookieText:  # pragma: no cover - domain invariant
            raise TypeError("wire cookie contains an unsupported value")
        rendered.append(f"{cookie.name.value}={value.value}")
    return "; ".join(rendered).encode("ascii")
