"""The sole run-scoped live-value to persistable-value sanitization boundary."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncIterable
from collections.abc import Awaitable
from collections.abc import Callable
from typing import cast
from urllib.parse import quote
from urllib.parse import quote_plus

import msgspec

from comparo.core.artifacts.common import require_text
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolvedValue
from comparo.core.resolution.models import SanitizationSnapshot

_ACCESS = SensitiveValueAccess(purpose="artifact.sanitization")


class SanitizationBoundaryClosedError(RuntimeError):
    """The run-scoped boundary was used after its secret patterns were wiped."""


class SanitizationBoundaryUnsealedError(RuntimeError):
    """Output sanitization was requested before all wire derivatives were registered."""


class SanitizationBoundarySealedError(RuntimeError):
    """A new live sensitive value was registered after sanitization began."""


class SanitizedText(msgspec.Struct, frozen=True, kw_only=True):
    """One safe text value and the number of literal or mandatory replacements."""

    value: str
    redaction_count: int

    def __post_init__(self) -> None:
        """Require exact safe output accounting."""
        if type(self.value) is not str:
            raise TypeError("sanitized text value must be text")
        if type(self.redaction_count) is not int or self.redaction_count < 0:
            raise ValueError("sanitized text redaction count must be non-negative")


class SanitizedBytes(msgspec.Struct, frozen=True, kw_only=True):
    """One safe byte value and the number of literal replacements."""

    value: bytes
    redaction_count: int

    def __post_init__(self) -> None:
        """Require immutable safe bytes and exact accounting."""
        if type(self.value) is not bytes:
            raise TypeError("sanitized byte value must be bytes")
        if type(self.redaction_count) is not int or self.redaction_count < 0:
            raise ValueError("sanitized byte redaction count must be non-negative")


class SanitizationBoundary:
    """Collect, seal, apply, and finally wipe one run's mandatory secret patterns."""

    __slots__ = ("_closed", "_marker", "_patterns", "_sealed")

    def __init__(self, *, marker: str = "<redacted>") -> None:
        """Create an initially collecting boundary with a visible UTF-8 marker."""
        require_text(marker, label="sanitization marker", maximum=64)
        self._marker = marker.encode("utf-8")
        self._patterns: list[bytearray] = []
        self._sealed = False
        self._closed = False

    @classmethod
    def from_snapshot(
        cls,
        snapshot: SanitizationSnapshot,
        *,
        marker: str = "<redacted>",
    ) -> SanitizationBoundary:
        """Collect every sealed resolution value in one same-run registry snapshot."""
        if type(snapshot) is not SanitizationSnapshot:
            raise TypeError("sanitization boundary requires a SanitizationSnapshot")
        boundary = cls(marker=marker)
        for entry in snapshot.entries:
            boundary.register(cast("SensitiveValue[object]", entry.value))
        return boundary

    def register(self, value: SensitiveValue[object]) -> None:
        """Register a sealed value or wire derivative before the boundary is sealed."""
        self._require_open()
        if self._sealed:
            raise SanitizationBoundarySealedError("sanitization boundary is already sealed")
        if not is_sensitive_value(value):
            raise TypeError("sanitization registration requires a SensitiveValue")
        payload = value.reveal(access=_ACCESS)
        for exact in _sensitive_payload_bytes(payload):
            self._add_pattern_derivatives(exact)

    def seal(self) -> None:
        """Deduplicate patterns and fix longest-first deterministic matching order."""
        self._require_open()
        unique = {bytes(pattern) for pattern in self._patterns if pattern}
        ordered = sorted(unique, key=lambda item: (-len(item), item))
        self._patterns = [bytearray(pattern) for pattern in ordered]
        self._sealed = True

    def sanitize_text(
        self,
        value: str | SensitiveValue[object],
        *,
        mandatory: bool = False,
    ) -> SanitizedText:
        """Sanitize UTF-8 text, masking sealed values and mandatory credentials whole."""
        self._require_ready()
        if is_sensitive_value(value):
            value.reveal(access=_ACCESS)
            return SanitizedText(value=self._marker.decode("utf-8"), redaction_count=1)
        if type(value) is not str:
            raise TypeError("text sanitization requires text or SensitiveValue")
        if mandatory:
            return SanitizedText(value=self._marker.decode("utf-8"), redaction_count=1)
        sanitized = self.sanitize_bytes(value.encode("utf-8"))
        return SanitizedText(
            value=sanitized.value.decode("utf-8"),
            redaction_count=sanitized.redaction_count,
        )

    def sanitize_bytes(self, value: bytes) -> SanitizedBytes:
        """Replace all known literal representations in one in-memory byte value."""
        self._require_ready()
        if type(value) is not bytes:
            raise TypeError("byte sanitization requires bytes")
        output, count = _replace_literals(
            bytearray(value),
            self._patterns,
            self._marker,
            final=True,
        )
        return SanitizedBytes(value=bytes(output), redaction_count=count)

    async def sanitize_stream(
        self,
        chunks: AsyncIterable[bytes],
        write: Callable[[bytes], Awaitable[None]],
    ) -> int:
        """Sanitize an async byte stream without retaining the complete body in memory."""
        self._require_ready()
        if not hasattr(chunks, "__aiter__"):
            raise TypeError("stream sanitization requires an async byte iterable")
        if not callable(write):
            raise TypeError("stream sanitization requires an async write callback")
        buffer = bytearray()
        redactions = 0
        async for chunk in chunks:
            if type(chunk) is not bytes:
                raise TypeError("sanitization stream chunks must be bytes")
            buffer.extend(chunk)
            output, count = _replace_literals(buffer, self._patterns, self._marker, final=False)
            redactions += count
            if output:
                await write(bytes(output))
        output, count = _replace_literals(buffer, self._patterns, self._marker, final=True)
        if output:
            await write(bytes(output))
        return redactions + count

    def close(self) -> None:
        """Wipe retained pattern buffers and permanently close the run boundary."""
        if self._closed:
            return
        for pattern in self._patterns:
            pattern[:] = b"\x00" * len(pattern)
        self._patterns.clear()
        self._marker = b""
        self._closed = True

    def _add_pattern_derivatives(self, value: bytes) -> None:
        if not value:
            return
        variants = {value, b64encode(value)}
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            variants.update(
                {
                    quote(text, safe="").encode("ascii"),
                    quote(text, safe="-._~").encode("ascii"),
                    quote_plus(text, safe="").encode("ascii"),
                    _json_quote(text).encode("utf-8"),
                }
            )
        self._patterns.extend(bytearray(item) for item in variants if item)

    def _require_open(self) -> None:
        if self._closed:
            raise SanitizationBoundaryClosedError("sanitization boundary is closed")

    def _require_ready(self) -> None:
        self._require_open()
        if not self._sealed:
            raise SanitizationBoundaryUnsealedError("sanitization boundary is not sealed")


def _replace_literals(
    buffer: bytearray,
    patterns: list[bytearray],
    marker: bytes,
    *,
    final: bool,
) -> tuple[bytearray, int]:
    """Consume the safely decidable buffer prefix with earliest/longest matching."""
    if not patterns:
        output = bytearray(buffer)
        buffer.clear()
        return output, 0
    maximum = len(patterns[0])
    safe_start_limit = len(buffer) if final else max(0, len(buffer) - maximum + 1)
    output = bytearray()
    redactions = 0
    while buffer and (final or safe_start_limit > 0):
        match = _earliest_match(buffer, patterns, safe_start_limit)
        if match is None:
            consume = len(buffer) if final else safe_start_limit
            output.extend(buffer[:consume])
            del buffer[:consume]
        else:
            offset, length = match
            output.extend(buffer[:offset])
            output.extend(marker)
            del buffer[: offset + length]
            redactions += 1
        safe_start_limit = len(buffer) if final else max(0, len(buffer) - maximum + 1)
    return output, redactions


def _earliest_match(
    buffer: bytearray,
    patterns: list[bytearray],
    safe_start_limit: int,
) -> tuple[int, int] | None:
    earliest: tuple[int, int] | None = None
    for pattern in patterns:
        offset = buffer.find(pattern)
        if offset < 0 or offset >= safe_start_limit:
            continue
        candidate = (offset, len(pattern))
        if (
            earliest is None
            or offset < earliest[0]
            or (offset == earliest[0] and len(pattern) > earliest[1])
        ):
            earliest = candidate
    return earliest


def _sensitive_payload_bytes(payload: object) -> tuple[bytes, ...]:
    """Flatten a closed sensitive payload into exact and component representations."""
    scalar = _sensitive_scalar_bytes(payload)
    if scalar is not None:
        return (scalar,)
    if type(payload) is JsonArray:
        nested = tuple(
            component for item in payload.items for component in _sensitive_payload_bytes(item)
        )
        return (_json_value_text(payload).encode("utf-8"), *nested)
    if type(payload) is JsonObject:
        nested = tuple(
            component
            for member in payload.members
            for component in _sensitive_payload_bytes(member.value)
        )
        return (_json_value_text(payload).encode("utf-8"), *nested)
    if type(payload) is ByteCount:
        return (str(payload.value).encode("ascii"),)
    if type(payload) is Duration:
        return (str(payload.nanoseconds).encode("ascii"),)
    raise TypeError("sanitization encountered an unsupported sensitive payload")


def _sensitive_scalar_bytes(payload: object) -> bytes | None:  # noqa: C901
    """Return one exact scalar representation or identify a composite payload."""
    if type(payload) is BytesValue:
        return payload.value
    if type(payload) is JsonString:
        return payload.value.encode("utf-8")
    if type(payload) in {JsonInteger, JsonDecimal}:
        return str(cast("JsonInteger | JsonDecimal", payload).value).encode("ascii")
    if type(payload) is JsonBoolean:
        return b"true" if payload.value else b"false"
    if type(payload) is JsonNull:
        return b"null"
    if type(payload) is Sha256Digest:
        return payload.hex_value.encode("ascii")
    if type(payload) is str:
        return payload.encode("utf-8")
    value = getattr(payload, "value", None)
    if type(value) is str:
        return value.encode("utf-8")
    if type(value) is bytes:
        return value
    if type(value) is int:
        return str(value).encode("ascii")
    return None


def _json_value_text(value: ResolvedValue) -> str:
    if type(value) is JsonNull:
        return "null"
    if type(value) is JsonBoolean:
        return "true" if value.value else "false"
    if type(value) in {JsonInteger, JsonDecimal}:
        return str(cast("JsonInteger | JsonDecimal", value).value)
    if type(value) is JsonString:
        return _json_quote(value.value)
    if type(value) is JsonArray:
        return "[" + ",".join(_json_value_text(item) for item in value.items) + "]"
    if type(value) is JsonObject:
        members = (
            f"{_json_quote(member.name)}:{_json_value_text(member.value)}"
            for member in value.members
        )
        return "{" + ",".join(members) + "}"
    raise TypeError("only exact JSON values have a JSON representation")


def _json_quote(value: str) -> str:
    """Render one JSON string with deterministic compact escaping."""
    return msgspec.json.encode(value).decode("utf-8")
