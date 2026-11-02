"""Typed HTTP authentication declarations with sensitivity-preserving credentials."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.fields import FORBIDDEN_API_KEY_HEADER_NAMES
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import is_sensitive_value


@final
class BearerToken(msgspec.Struct, frozen=True, kw_only=True):
    """One syntactically valid RFC 6750 bearer credential payload."""

    value: str

    def __post_init__(self) -> None:
        """Require the bearer b64token character repertoire."""
        if type(self.value) is not str or not self.value:
            raise ValueError("bearer token must be non-empty text")
        unpadded = self.value.rstrip("=")
        if not unpadded or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~+/"
            for character in unpadded
        ):
            raise ValueError("bearer token contains an invalid character")


@final
class BasicUsername(msgspec.Struct, frozen=True, kw_only=True):
    """A Basic-auth user-id whose colon boundary cannot be ambiguous."""

    value: str

    def __post_init__(self) -> None:
        """Require valid UTF-8 text without controls or a colon separator."""
        _require_credential_text(self.value, label="Basic username")
        if ":" in self.value:
            raise ValueError("Basic username must not contain a colon")


@final
class BasicPassword(msgspec.Struct, frozen=True, kw_only=True):
    """A Basic-auth password payload, always sensitivity-contained by its owner."""

    value: str

    def __post_init__(self) -> None:
        """Require valid UTF-8 text without wire control characters."""
        _require_credential_text(self.value, label="Basic password")


class BasicWireEncoding(DomainStrEnum):
    """The explicit pre-Base64 octet encoding used for Basic credentials."""

    UTF_8 = "utf-8"


@final
class ApiKeyCredential(msgspec.Struct, frozen=True, kw_only=True):
    """A placement-safe visible-ASCII API credential payload."""

    value: str

    def __post_init__(self) -> None:
        """Use one repertoire safe in typed header, query, and cookie placement."""
        if (
            type(self.value) is not str
            or not self.value
            or any(
                ord(character) < 33 or ord(character) > 126 or character in {'"', ",", ";", "\\"}
                for character in self.value
            )
        ):
            raise ValueError("API-key credential must use safe visible ASCII characters")


def _require_credential_text(value: str, *, label: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{label} must be text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must contain valid Unicode scalar values") from error
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} must not contain control characters")


def _require_sensitive_payload(value: object, expected: type[object], *, label: str) -> None:
    if not is_sensitive_value(value):
        raise TypeError(f"{label} must be SensitiveValue")
    if value.payload_type is not expected:
        raise TypeError(f"{label} sensitive payload has the wrong credential type")


@final
class NoAuthentication(msgspec.Struct, frozen=True, kw_only=True):
    """An explicit absence that also clears inherited Environment authentication."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


@final
class BearerAuthentication(msgspec.Struct, frozen=True, kw_only=True):
    """A bearer token that cannot render or serialize outside its live wrapper."""

    token: SensitiveValue[BearerToken]

    def __post_init__(self) -> None:
        """Require fail-closed sensitive token containment."""
        _require_sensitive_payload(self.token, BearerToken, label="bearer token")


@final
class BasicAuthentication(msgspec.Struct, frozen=True, kw_only=True):
    """HTTP Basic credentials with an always-sensitive password."""

    username: BasicUsername | SensitiveValue[BasicUsername]
    password: SensitiveValue[BasicPassword]
    wire_encoding: BasicWireEncoding

    def __post_init__(self) -> None:
        """Require validated username and sensitive password containment."""
        if type(self.username) is not BasicUsername and not is_sensitive_value(self.username):
            raise TypeError("basic username must be BasicUsername or SensitiveValue")
        if is_sensitive_value(self.username) and self.username.payload_type is not BasicUsername:
            raise TypeError("sensitive Basic username payload must be BasicUsername")
        _require_sensitive_payload(self.password, BasicPassword, label="basic password")
        if type(self.wire_encoding) is not BasicWireEncoding:
            raise TypeError("Basic authentication wire encoding must be BasicWireEncoding")


@final
class HeaderApiKeyPlacement(msgspec.Struct, frozen=True, kw_only=True):
    """Place an API credential in one typed generated request header."""

    name: HeaderName

    def __post_init__(self) -> None:
        """Reject untyped and transport-owned header placements."""
        if type(self.name) is not HeaderName:
            raise TypeError("API-key header placement name must be HeaderName")
        if self.name.value in FORBIDDEN_API_KEY_HEADER_NAMES:
            raise ValueError("API key cannot occupy a cookie, authority, or framing header")


@final
class QueryApiKeyPlacement(msgspec.Struct, frozen=True, kw_only=True):
    """Place an API credential in one typed generated query parameter."""

    name: QueryName

    def __post_init__(self) -> None:
        """Require a validated query parameter name."""
        if type(self.name) is not QueryName:
            raise TypeError("API-key query placement name must be QueryName")


@final
class CookieApiKeyPlacement(msgspec.Struct, frozen=True, kw_only=True):
    """Place an API credential in one typed generated request cookie."""

    name: CookieName

    def __post_init__(self) -> None:
        """Require a validated typed cookie name."""
        if type(self.name) is not CookieName:
            raise TypeError("API-key cookie placement name must be CookieName")


type ApiKeyPlacement = HeaderApiKeyPlacement | QueryApiKeyPlacement | CookieApiKeyPlacement


@final
class ApiKeyAuthentication(msgspec.Struct, frozen=True, kw_only=True):
    """A sensitive API credential with an explicit collision-checkable placement."""

    credential: SensitiveValue[ApiKeyCredential]
    placement: ApiKeyPlacement

    def __post_init__(self) -> None:
        """Require a sealed credential and closed placement variant."""
        _require_sensitive_payload(
            self.credential,
            ApiKeyCredential,
            label="API-key credential",
        )
        if type(self.placement) not in {
            HeaderApiKeyPlacement,
            QueryApiKeyPlacement,
            CookieApiKeyPlacement,
        }:
            raise TypeError("API-key placement must be a supported typed placement")


type Authentication = (
    NoAuthentication | BearerAuthentication | BasicAuthentication | ApiKeyAuthentication
)


def is_authentication(value: object) -> TypeGuard[Authentication]:
    """Narrow an arbitrary value to the initial typed authentication union."""
    return type(value) in {
        NoAuthentication,
        BearerAuthentication,
        BasicAuthentication,
        ApiKeyAuthentication,
    }
