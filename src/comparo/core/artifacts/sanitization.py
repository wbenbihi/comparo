"""The sole run-scoped live-value to persistable-value sanitization boundary."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncIterable
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterator
from typing import cast
from urllib.parse import quote
from urllib.parse import quote_plus

import msgspec

from comparo.core.artifacts.common import require_text
from comparo.core.artifacts.policy import SanitizationLimits
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


class SanitizationBudgetExceededError(RuntimeError):
    """The caller-owned literal-redaction pattern budget was exhausted."""


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

    __slots__ = (
        "_closed",
        "_limits",
        "_marker",
        "_pattern_bytes",
        "_patterns",
        "_sealed",
    )

    def __init__(self, limits: SanitizationLimits, *, marker: str = "<redacted>") -> None:
        """Create an initially collecting boundary with a visible UTF-8 marker."""
        if type(limits) is not SanitizationLimits:
            raise TypeError("sanitization boundary requires SanitizationLimits")
        require_text(marker, label="sanitization marker", maximum=64)
        self._limits = limits
        self._marker = marker.encode("utf-8")
        self._patterns: list[bytearray] = []
        self._pattern_bytes = 0
        self._sealed = False
        self._closed = False

    @classmethod
    def from_snapshot(
        cls,
        snapshot: SanitizationSnapshot,
        limits: SanitizationLimits,
        *,
        marker: str = "<redacted>",
    ) -> SanitizationBoundary:
        """Collect every sealed resolution value in one same-run registry snapshot."""
        if type(snapshot) is not SanitizationSnapshot:
            raise TypeError("sanitization boundary requires a SanitizationSnapshot")
        boundary = cls(limits, marker=marker)
        try:
            for entry in snapshot.entries:
                boundary.register(cast("SensitiveValue[object]", entry.value))
        except BaseException:
            boundary.close()
            raise
        return boundary

    def register(self, value: SensitiveValue[object]) -> None:
        """Register a sealed value or wire derivative before the boundary is sealed."""
        self._require_open()
        if self._sealed:
            raise SanitizationBoundarySealedError("sanitization boundary is already sealed")
        if not is_sensitive_value(value):
            raise TypeError("sanitization registration requires a SensitiveValue")
        payload = value.reveal(access=_ACCESS)
        try:
            maximum_exact_bytes = self._limits.maximum_value_bytes.value
            for exact in _sensitive_payload_bytes(
                payload,
                maximum=maximum_exact_bytes,
                maximum_nodes=self._limits.maximum_value_nodes.value,
            ):
                self._add_pattern_derivatives(exact)
        except BaseException:
            self.close()
            raise

    def seal(self) -> None:
        """Fix incrementally deduplicated patterns in deterministic match order."""
        self._require_open()
        self._patterns.sort(key=lambda item: (-len(item), item))
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
        utf8_patterns = [pattern for pattern in self._patterns if _is_utf8(pattern)]
        output, count = _replace_literals(
            bytearray(value.encode("utf-8")),
            utf8_patterns,
            self._marker,
            final=True,
        )
        return SanitizedText(
            value=bytes(output).decode("utf-8"),
            redaction_count=count,
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
        iterator = chunks.__aiter__()
        try:
            async for chunk in iterator:
                if type(chunk) is not bytes:
                    raise TypeError("sanitization stream chunks must be bytes")
                buffer.extend(chunk)
                output, count = _replace_literals(
                    buffer,
                    self._patterns,
                    self._marker,
                    final=False,
                )
                redactions += count
                if output:
                    await write(bytes(output))
            output, count = _replace_literals(buffer, self._patterns, self._marker, final=True)
            if output:
                await write(bytes(output))
            return redactions + count
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()

    def close(self) -> None:
        """Wipe retained pattern buffers and permanently close the run boundary."""
        if self._closed:
            return
        for pattern in self._patterns:
            pattern[:] = b"\x00" * len(pattern)
        self._patterns.clear()
        self._pattern_bytes = 0
        self._marker = b""
        self._closed = True

    def _add_pattern_derivatives(self, value: bytes) -> None:
        if not value:
            return
        if len(value) > self._limits.maximum_value_bytes.value:
            raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
        self._add_pattern(value)
        self._add_pattern(b64encode(value))
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            self._add_pattern(quote(text, safe="").encode("ascii"))
            self._add_pattern(quote(text, safe="-._~").encode("ascii"))
            self._add_pattern(quote_plus(text, safe="").encode("ascii"))
            self._add_pattern(_json_quote(text).encode("utf-8"))

    def _add_pattern(self, item: bytes) -> None:
        if not item or any(pattern == item for pattern in self._patterns):
            return
        if (
            len(self._patterns) >= self._limits.maximum_patterns.value
            or self._pattern_bytes + len(item) > self._limits.maximum_pattern_bytes.value
        ):
            raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
        self._patterns.append(bytearray(item))
        self._pattern_bytes += len(item)

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


def _is_utf8(value: bytearray) -> bool:
    try:
        value.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _sensitive_payload_bytes(  # noqa: C901 - iterative closed sensitive-value dispatch
    payload: object,
    *,
    maximum: int,
    maximum_nodes: int,
) -> Iterator[bytes]:
    """Yield bounded exact and component representations of one sensitive payload."""
    stack: list[tuple[bool, object]] = [(False, payload)]
    nodes = 0
    scheduled_nodes = 1
    while stack:
        is_name, current = stack.pop()
        if is_name:
            name = cast("str", current).encode("utf-8")
            if len(name) > maximum:
                raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
            yield name
            continue
        scheduled_nodes -= 1
        nodes += 1
        if nodes > maximum_nodes:
            raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
        scalar = _sensitive_scalar_bytes(current)
        if scalar is not None:
            if len(scalar) > maximum:
                raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
            yield scalar
        elif type(current) is JsonArray:
            yield _bounded_json_bytes(
                current,
                maximum=maximum,
                maximum_nodes=maximum_nodes,
            )
            if nodes + scheduled_nodes + len(current.items) > maximum_nodes:
                raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
            scheduled_nodes += len(current.items)
            stack.extend((False, item) for item in reversed(current.items))
        elif type(current) is JsonObject:
            yield _bounded_json_bytes(
                current,
                maximum=maximum,
                maximum_nodes=maximum_nodes,
            )
            if nodes + scheduled_nodes + len(current.members) > maximum_nodes:
                raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
            scheduled_nodes += len(current.members)
            for member in reversed(current.members):
                stack.append((False, member.value))
                stack.append((True, member.name))
        elif type(current) is Duration:
            rendered = str(current.nanoseconds).encode("ascii")
            if len(rendered) > maximum:
                raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
            yield rendered
        else:
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


def _bounded_json_bytes(  # noqa: C901 - iterative dispatch over closed JSON values
    value: ResolvedValue,
    *,
    maximum: int,
    maximum_nodes: int,
) -> bytes:
    """Render exact JSON while refusing allocation beyond the caller-owned bound."""
    output = bytearray()

    def append(chunk: bytes) -> None:
        if len(output) + len(chunk) > maximum:
            raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
        output.extend(chunk)

    stack: list[ResolvedValue | bytes | tuple[str]] = [value]
    scheduled_nodes = 1
    rendered_nodes = 0
    while stack:
        item = stack.pop()
        if type(item) is bytes:
            append(item)
            continue
        if type(item) is tuple:
            append(_bounded_json_quote(item[0], maximum=maximum - len(output)))
            continue
        scheduled_nodes -= 1
        rendered_nodes += 1
        if rendered_nodes > maximum_nodes:
            raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
        if type(item) is JsonNull:
            append(b"null")
        elif type(item) is JsonBoolean:
            append(b"true" if item.value else b"false")
        elif type(item) in {JsonInteger, JsonDecimal}:
            append(str(cast("JsonInteger | JsonDecimal", item).value).encode("ascii"))
        elif type(item) is JsonString:
            append(_bounded_json_quote(item.value, maximum=maximum - len(output)))
        elif type(item) is JsonArray:
            append(b"[")
            children = item.items
            if rendered_nodes + scheduled_nodes + len(children) > maximum_nodes:
                raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
            scheduled_nodes += len(children)
            stack.append(b"]")
            for index in range(len(children) - 1, -1, -1):
                stack.append(children[index])
                if index:
                    stack.append(b",")
        elif type(item) is JsonObject:
            append(b"{")
            members = item.members
            if rendered_nodes + scheduled_nodes + len(members) > maximum_nodes:
                raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
            scheduled_nodes += len(members)
            stack.append(b"}")
            for index in range(len(members) - 1, -1, -1):
                member = members[index]
                stack.append(member.value)
                stack.append(b":")
                stack.append((member.name,))
                if index:
                    stack.append(b",")
        else:
            raise TypeError("only exact JSON values have a JSON representation")
    return bytes(output)


def _bounded_json_quote(value: str, *, maximum: int) -> bytes:
    if len(value) > maximum:
        raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
    rendered = msgspec.json.encode(value)
    if len(rendered) > maximum:
        raise SanitizationBudgetExceededError("sanitization pattern budget exceeded")
    return rendered


def _json_quote(value: str) -> str:
    """Render one JSON string with deterministic compact escaping."""
    return msgspec.json.encode(value).decode("utf-8")
