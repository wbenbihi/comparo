"""Exact, ordered HTTP field collections with explicit wire-value distinctions."""

from __future__ import annotations

import re
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value

_TOKEN_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_METHOD_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Z-]+")
_RESERVED_FRAMING_NAMES = frozenset({"content-length", "host", "transfer-encoding"})
_FORBIDDEN_CONNECTION_NAMES = frozenset(
    {
        "connection",
        "http2-settings",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "upgrade",
    }
)
FORBIDDEN_API_KEY_HEADER_NAMES = (
    _RESERVED_FRAMING_NAMES
    | _FORBIDDEN_CONNECTION_NAMES
    | frozenset({"cookie", "proxy-authorization"})
)
# Conservative protocol/common credential names. Project policy may extend this immutable minimum.
MANDATORY_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "api-key",
        "authorization",
        "ocp-apim-subscription-key",
        "proxy-authorization",
        "x-amz-security-token",
        "x-access-token",
        "x-api-key",
        "x-auth-token",
        "x-functions-key",
        "x-goog-api-key",
        "x-master-key",
        "x-subscription-key",
    }
)
MANDATORY_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "auth_token",
        "client_secret",
        "id_token",
        "refresh_token",
        "signature",
        "token",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-signature",
    }
)


def _require_utf8_text(value: str, *, label: str, allow_empty: bool = True) -> None:
    if type(value) is not str:
        raise TypeError(f"{label} must be text")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must contain valid Unicode scalar values") from error


def _require_provenance(value: ValueProvenance) -> None:
    if type(value) is not ValueProvenance:
        raise TypeError("HTTP field provenance must be ValueProvenance")


@final
class HttpMethod(msgspec.Struct, frozen=True, kw_only=True):
    """A canonical uppercase HTTP method token, including registered extensions."""

    value: str

    def __post_init__(self) -> None:
        """Reject non-canonical or malformed method tokens."""
        if type(self.value) is not str or _METHOD_PATTERN.fullmatch(self.value) is None:
            raise ValueError("HTTP method must be a non-empty canonical uppercase token")


@final
class HeaderName(msgspec.Struct, frozen=True, kw_only=True):
    """A lowercase canonical representation of a case-insensitive field name."""

    value: str

    def __post_init__(self) -> None:
        """Require the one lowercase representation used for equality."""
        if (
            type(self.value) is not str
            or self.value.lower() != self.value
            or _TOKEN_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError("header name must be a lowercase HTTP token")


@final
class HeaderText(msgspec.Struct, frozen=True, kw_only=True):
    """One validated HTTP field value with injection characters excluded."""

    value: str

    def __post_init__(self) -> None:
        """Reject injection, controls, and unsupported wire characters."""
        if type(self.value) is not str:
            raise TypeError("header value must be text")
        if any(
            ord(character) > 255
            or (ord(character) < 32 and character != "\t")
            or ord(character) == 127
            for character in self.value
        ):
            raise ValueError("header value contains a forbidden control or non-Latin-1 character")


type HeaderValue = HeaderText | SensitiveValue[HeaderText]


class HeaderSource(DomainStrEnum):
    """The mechanism that contributed an effective request field."""

    AUTHORED = "authored"
    AUTHENTICATION = "authentication"
    BODY = "body"


@final
class HeaderField(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered header occurrence; duplicate names remain separate occurrences."""

    name: HeaderName
    value: HeaderValue
    source: HeaderSource
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require validated field components and value-free provenance."""
        if type(self.name) is not HeaderName:
            raise TypeError("header field name must be HeaderName")
        if type(self.value) is not HeaderText and not is_sensitive_value(self.value):
            raise TypeError("header field value must be HeaderText or SensitiveValue")
        if is_sensitive_value(self.value) and self.value.payload_type is not HeaderText:
            raise TypeError("sensitive header field payload must be HeaderText")
        if type(self.source) is not HeaderSource:
            raise TypeError("header field source must be HeaderSource")
        if self.source is HeaderSource.AUTHENTICATION and not is_sensitive_value(self.value):
            raise ValueError("authentication-generated headers must remain sensitive")
        if self.name.value in MANDATORY_SENSITIVE_HEADER_NAMES and not is_sensitive_value(
            self.value
        ):
            raise ValueError("known credential header values must remain sensitive")
        _require_provenance(self.provenance)


@final
class RequestHeaders(msgspec.Struct, frozen=True, kw_only=True):
    """An immutable ordered, duplicate-preserving header multimap."""

    fields: tuple[HeaderField, ...] = ()

    def __post_init__(self) -> None:
        """Enforce immutable entries and transport-owned header boundaries."""
        if type(self.fields) is not tuple or any(
            type(field) is not HeaderField for field in self.fields
        ):
            raise TypeError("request headers must be an immutable tuple of HeaderField values")
        for field in self.fields:
            name = field.name.value
            if name in _FORBIDDEN_CONNECTION_NAMES:
                raise ValueError("connection-specific request headers are not supported")
            if name in _RESERVED_FRAMING_NAMES:
                raise ValueError("Host and framing headers are derived only by the transport")
            if name == "cookie":
                raise ValueError("Cookie headers must be represented by typed request cookies")
            if name == "authorization" and field.source is not HeaderSource.AUTHENTICATION:
                raise ValueError("Authorization must be produced by typed authentication")
            if name == "proxy-authorization":
                raise ValueError("Proxy-Authorization belongs to typed proxy authentication")


@final
class QueryName(msgspec.Struct, frozen=True, kw_only=True):
    """An exact query parameter name encoded as UTF-8 by a transport adapter."""

    value: str

    def __post_init__(self) -> None:
        """Require non-empty text that has an unambiguous UTF-8 encoding."""
        _require_utf8_text(self.value, label="query name", allow_empty=False)


@final
class QueryText(msgspec.Struct, frozen=True, kw_only=True):
    """An exact query value; an empty value remains distinct from a bare parameter."""

    value: str

    def __post_init__(self) -> None:
        """Require text that has an unambiguous UTF-8 encoding."""
        _require_utf8_text(self.value, label="query value")


@final
class BareQueryValue(msgspec.Struct, frozen=True, kw_only=True):
    """A query parameter with no equals sign, distinct from an empty value."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


type QueryValue = BareQueryValue | QueryText | SensitiveValue[QueryText]


class QuerySource(DomainStrEnum):
    """The mechanism that contributed a query occurrence."""

    AUTHORED = "authored"
    AUTHENTICATION = "authentication"


@final
class QueryParameter(msgspec.Struct, frozen=True, kw_only=True):
    """One source-ordered query parameter occurrence."""

    name: QueryName
    value: QueryValue
    source: QuerySource
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require an exact query value, source, and provenance."""
        if type(self.name) is not QueryName:
            raise TypeError("query parameter name must be QueryName")
        if type(self.value) not in {BareQueryValue, QueryText} and not is_sensitive_value(
            self.value
        ):
            raise TypeError("query parameter value must be a supported QueryValue")
        if is_sensitive_value(self.value) and self.value.payload_type is not QueryText:
            raise TypeError("sensitive query parameter payload must be QueryText")
        if type(self.source) is not QuerySource:
            raise TypeError("query parameter source must be QuerySource")
        if self.source is QuerySource.AUTHENTICATION and not is_sensitive_value(self.value):
            raise ValueError("authentication-generated query values must remain sensitive")
        if self.name.value.casefold() in MANDATORY_SENSITIVE_QUERY_NAMES and not is_sensitive_value(
            self.value
        ):
            raise ValueError("known credential query values must remain sensitive")
        _require_provenance(self.provenance)


@final
class QueryParameters(msgspec.Struct, frozen=True, kw_only=True):
    """An immutable ordered, duplicate-preserving query multimap."""

    parameters: tuple[QueryParameter, ...] = ()

    def __post_init__(self) -> None:
        """Require an immutable ordered collection of typed parameters."""
        if type(self.parameters) is not tuple or any(
            type(parameter) is not QueryParameter for parameter in self.parameters
        ):
            raise TypeError("query parameters must be an immutable tuple of QueryParameter values")

    @property
    def sensitivity(self) -> Sensitivity:
        """Derive collection sensitivity from its value containment."""
        if any(is_sensitive_value(parameter.value) for parameter in self.parameters):
            return Sensitivity.SENSITIVE
        return Sensitivity.PUBLIC

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Return the stable union of every sealed query value's lineage."""
        origins = (
            origin
            for parameter in self.parameters
            if is_sensitive_value(parameter.value)
            for origin in parameter.value.origins
        )
        return tuple(dict.fromkeys(origins))


@final
class CookieName(msgspec.Struct, frozen=True, kw_only=True):
    """A request-cookie name represented without raw Cookie header syntax."""

    value: str

    def __post_init__(self) -> None:
        """Reject names that cannot be emitted as typed cookie syntax."""
        if type(self.value) is not str or _TOKEN_PATTERN.fullmatch(self.value) is None:
            raise ValueError("cookie name must be an HTTP token")


@final
class CookieText(msgspec.Struct, frozen=True, kw_only=True):
    """A request-cookie value excluding delimiters and control characters."""

    value: str

    def __post_init__(self) -> None:
        """Reject raw delimiters and injection characters."""
        if type(self.value) is not str or any(
            ord(character) < 33 or ord(character) > 126 or character in {'"', ",", ";", "\\"}
            for character in self.value
        ):
            raise ValueError("cookie value contains a forbidden delimiter or character")


type CookieValue = SensitiveValue[CookieText]


class CookieSource(DomainStrEnum):
    """The typed source of a request cookie."""

    EXPLICIT = "explicit"
    SESSION = "session"
    AUTHENTICATION = "authentication"


@final
class RequestCookie(msgspec.Struct, frozen=True, kw_only=True):
    """One typed request cookie with sensitivity-preserving value containment."""

    name: CookieName
    value: CookieValue
    source: CookieSource
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require validated cookie components and value-free provenance."""
        if type(self.name) is not CookieName:
            raise TypeError("request cookie name must be CookieName")
        if not is_sensitive_value(self.value):
            raise TypeError("request cookie value must remain sensitive")
        if self.value.payload_type is not CookieText:
            raise TypeError("sensitive request cookie payload must be CookieText")
        if type(self.source) is not CookieSource:
            raise TypeError("request cookie source must be CookieSource")
        _require_provenance(self.provenance)


@final
class RequestCookies(msgspec.Struct, frozen=True, kw_only=True):
    """A resolved typed cookie collection with no ambiguous duplicate names."""

    cookies: tuple[RequestCookie, ...] = ()

    def __post_init__(self) -> None:
        """Require immutable typed entries and resolved unique cookie names."""
        if type(self.cookies) is not tuple or any(
            type(cookie) is not RequestCookie for cookie in self.cookies
        ):
            raise TypeError("request cookies must be an immutable tuple of RequestCookie values")
        names = tuple(cookie.name.value for cookie in self.cookies)
        if len(set(names)) != len(names):
            raise ValueError("resolved request cookie names must be unique")


def is_header_value(value: object) -> TypeGuard[HeaderValue]:
    """Narrow to the outer header-value union; receiving fields validate sealed payload types."""
    return type(value) is HeaderText or is_sensitive_value(value)
