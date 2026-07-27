"""Sanitized HTTP request, response, timing, attempt, and exchange contracts."""

from __future__ import annotations

import re
from typing import final
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

import msgspec

from comparo.core.artifacts.common import ARTIFACT_REDACTION_MARKER
from comparo.core.artifacts.common import ArtifactCapture
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import require_count
from comparo.core.artifacts.common import require_text
from comparo.core.artifacts.common import require_unicode_scalars
from comparo.core.credentials import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.credentials import MANDATORY_SENSITIVE_QUERY_NAMES

_FIELD_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9a-z-]+")
_COOKIE_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


@final
class ArtifactField(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One ordered sanitized HTTP field occurrence."""

    name: str
    value: str
    redacted: bool = False

    def __post_init__(self) -> None:
        """Require exact bounded strings and an explicit redaction marker."""
        require_text(self.name, label="artifact field name", maximum=255)
        if _FIELD_NAME.fullmatch(self.name) is None:
            raise ValueError("artifact field name must be a canonical lowercase HTTP token")
        if type(self.value) is not str or len(self.value) > 1_048_576:
            raise ValueError("artifact field value must be bounded text")
        require_unicode_scalars(self.value, label="artifact field value")
        if type(self.redacted) is not bool:
            raise TypeError("artifact field redaction marker must be bool")


@final
class ArtifactCookie(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One case-preserving cookie name with an unconditionally masked value."""

    name: str
    value: str
    redacted: bool

    def __post_init__(self) -> None:
        """Preserve cookie-name semantics while enforcing fixed value redaction."""
        require_text(self.name, label="artifact cookie name", maximum=255)
        if self.name != ARTIFACT_REDACTION_MARKER and _COOKIE_NAME.fullmatch(self.name) is None:
            raise ValueError("artifact cookie name must be an HTTP token or redaction marker")
        if self.value != ARTIFACT_REDACTION_MARKER or self.redacted is not True:
            raise ValueError("artifact cookie values require fixed redaction")


def _require_fields(fields: object, *, label: str) -> None:
    if type(fields) is not tuple or any(type(item) is not ArtifactField for item in fields):
        raise TypeError(f"artifact {label} fields must be immutable and typed")


def _require_cookies(cookies: object) -> None:
    if type(cookies) is not tuple or any(type(item) is not ArtifactCookie for item in cookies):
        raise TypeError("artifact request cookies must be immutable and typed")


@final
class ArtifactRequest(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Sanitized wire request representation."""

    method: str
    url: str
    url_redaction_count: int
    header_redaction_count: int
    cookie_redaction_count: int
    headers: tuple[ArtifactField, ...]
    cookies: tuple[ArtifactCookie, ...]
    body: ArtifactCapture | None

    def __post_init__(self) -> None:
        """Require ordered field collections and an optional truthful body capture."""
        require_text(self.method, label="artifact request method", maximum=32)
        require_text(self.url, label="artifact request URL", maximum=8192)
        require_count(self.url_redaction_count, label="artifact request URL redactions")
        require_count(self.header_redaction_count, label="artifact request header redactions")
        require_count(self.cookie_redaction_count, label="artifact request cookie redactions")
        _require_fields(self.headers, label="request header")
        _require_cookies(self.cookies)
        if self.body is not None and type(self.body) is not ArtifactCapture:
            raise TypeError("artifact request body must be ArtifactCapture")
        _require_mandatory_field_redaction(self.headers)
        _require_redaction_count_lower_bounds(self)


@final
class ArtifactTiming(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One exact timing value or explicit non-measurement reason."""

    name: str
    nanoseconds: int | None
    reason: str | None

    def __post_init__(self) -> None:
        """Require exactly a duration or structured absence reason."""
        require_text(self.name, label="artifact timing name", maximum=63)
        if self.nanoseconds is not None:
            require_count(self.nanoseconds, label="artifact timing duration")
        if self.reason is not None:
            require_text(self.reason, label="artifact timing reason", maximum=127)
        if (self.nanoseconds is None) == (self.reason is None):
            raise ValueError("artifact timing requires exactly a duration or reason")


@final
class ArtifactResponse(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Sanitized observed response without transport-owned locators."""

    status: int
    protocol: str
    headers: tuple[ArtifactField, ...]
    trailers: tuple[ArtifactField, ...]
    body: ArtifactCapture | None

    def __post_init__(self) -> None:
        """Require valid HTTP status and ordered field collections."""
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("artifact response status must be an HTTP status")
        require_text(self.protocol, label="artifact response protocol", maximum=32)
        _require_fields(self.headers, label="response header")
        _require_fields(self.trailers, label="response trailer")
        if self.body is not None and type(self.body) is not ArtifactCapture:
            raise TypeError("artifact response body must be ArtifactCapture")
        _require_mandatory_field_redaction(self.headers)
        _require_mandatory_field_redaction(self.trailers)


def _require_mandatory_field_redaction(
    fields: tuple[ArtifactField, ...],
) -> None:
    for field in fields:
        mandatory = field.name in MANDATORY_SENSITIVE_HEADER_NAMES | {"set-cookie"}
        if mandatory and (not field.redacted or field.value != ARTIFACT_REDACTION_MARKER):
            raise ValueError("mandatory credential artifact fields require fixed redaction")


def _require_redaction_count_lower_bounds(request: ArtifactRequest) -> None:
    mandatory_query_redactions = _require_mandatory_query_redaction(request.url)
    if request.url_redaction_count < mandatory_query_redactions:
        raise ValueError(
            "artifact request URL redactions cannot under-report mandatory credential masking"
        )
    if request.header_redaction_count < sum(field.redacted for field in request.headers):
        raise ValueError("artifact request header redactions cannot under-report masked fields")
    if request.cookie_redaction_count < len(request.cookies):
        raise ValueError("artifact request cookie redactions cannot under-report masked cookies")


def _require_mandatory_query_redaction(url: str) -> int:
    try:
        parsed = urlsplit(url)
        parameters = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        raise ValueError("artifact request URL must be valid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("artifact request URL must be absolute HTTP(S)")
    mandatory_redactions = 0
    for name, value in parameters:
        if name.casefold() not in MANDATORY_SENSITIVE_QUERY_NAMES:
            continue
        if value not in {"", ARTIFACT_REDACTION_MARKER}:
            raise ValueError("mandatory credential query values require fixed redaction")
        if value == ARTIFACT_REDACTION_MARKER:
            mandatory_redactions += 1
    return mandatory_redactions


@final
class ArtifactExchange(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One sanitized request and terminal response or failure."""

    exchange_id: str
    request: ArtifactRequest | None
    request_omission: ArtifactOmissionReason | None
    response: ArtifactResponse | None
    response_omission: ArtifactOmissionReason | None
    failure: ArtifactFailure | None
    timings: tuple[ArtifactTiming, ...]

    def __post_init__(self) -> None:
        """Require one terminal outcome and canonical timing names."""
        require_text(self.exchange_id, label="artifact exchange ID", maximum=64)
        _validate_exchange_messages(self)
        if type(self.timings) is not tuple or any(
            type(item) is not ArtifactTiming for item in self.timings
        ):
            raise TypeError("artifact exchange timings must be immutable and typed")
        names = tuple(item.name for item in self.timings)
        if len(set(names)) != len(names) or names != tuple(sorted(names)):
            raise ValueError("artifact timing names must be unique and canonical")


def _validate_exchange_messages(exchange: ArtifactExchange) -> None:
    """Keep request, response, omission, and failure variants coherent."""
    request = exchange.request
    request_omission = exchange.request_omission
    response = exchange.response
    response_omission = exchange.response_omission
    failure = exchange.failure
    if request is not None and type(request) is not ArtifactRequest:
        raise TypeError("artifact exchange request must be ArtifactRequest")
    if request_omission is not None and type(request_omission) is not ArtifactOmissionReason:
        raise TypeError("artifact exchange request omission must be typed")
    if (request is None) == (request_omission is None):
        raise ValueError("artifact exchange requires a request or explicit omission")
    if response is not None and type(response) is not ArtifactResponse:
        raise TypeError("artifact exchange response must be ArtifactResponse")
    if response_omission is not None and type(response_omission) is not ArtifactOmissionReason:
        raise TypeError("artifact exchange response omission must be typed")
    if failure is not None and type(failure) is not ArtifactFailure:
        raise TypeError("artifact exchange failure must be ArtifactFailure")
    terminal_count = sum(item is not None for item in (response, response_omission, failure))
    if terminal_count != 1:
        raise ValueError("artifact exchange requires exactly one response, failure, or omission")


@final
class ArtifactAttempt(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One ordered redirect-containing attempt."""

    attempt_id: str
    exchanges: tuple[ArtifactExchange, ...]
    timings: tuple[ArtifactTiming, ...] = ()
    terminal_pre_send_request: ArtifactRequest | None = None
    terminal_pre_send_omission: ArtifactOmissionReason | None = None
    failure: ArtifactFailure | None = None

    def __post_init__(self) -> None:
        """Require a non-empty exchange chain or one pre-send failure."""
        require_text(self.attempt_id, label="artifact attempt ID", maximum=64)
        if type(self.exchanges) is not tuple or any(
            type(item) is not ArtifactExchange for item in self.exchanges
        ):
            raise TypeError("artifact attempt exchanges must be immutable and typed")
        if type(self.timings) is not tuple or any(
            type(item) is not ArtifactTiming for item in self.timings
        ):
            raise TypeError("artifact attempt timings must be immutable and typed")
        if (
            self.terminal_pre_send_request is not None
            and type(self.terminal_pre_send_request) is not ArtifactRequest
        ):
            raise TypeError("artifact pre-send request must be ArtifactRequest")
        if (
            self.terminal_pre_send_omission is not None
            and type(self.terminal_pre_send_omission) is not ArtifactOmissionReason
        ):
            raise TypeError("artifact pre-send omission must be typed")
        if (
            self.terminal_pre_send_request is not None
            and self.terminal_pre_send_omission is not None
        ):
            raise ValueError("artifact pre-send request and omission are mutually exclusive")
        if self.failure is not None and type(self.failure) is not ArtifactFailure:
            raise TypeError("artifact attempt failure must be ArtifactFailure")
        pre_send = (
            self.terminal_pre_send_request is not None
            or self.terminal_pre_send_omission is not None
        )
        if pre_send != (self.failure is not None):
            raise ValueError("artifact pre-send request and failure must be retained together")
        if not self.exchanges and not pre_send:
            raise ValueError("artifact attempt requires exchanges or a pre-send outcome")
