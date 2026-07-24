"""Static selector paths and canonical concrete JSON Pointers."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import final

import msgspec

from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.sources import SelectorCapabilities

_SEGMENT_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class PathDialect(StrEnum):
    """Versioned path dialects recognized by the compiler."""

    STATIC_FIELDS = "comparo.static-fields"
    JSON_POINTER = "rfc6901"
    JSON_PATH = "rfc9535"


@final
class NoPath(msgspec.Struct, frozen=True, kw_only=True):
    """An explicit whole-source selection."""


@final
class StaticFieldPath(msgspec.Struct, frozen=True, kw_only=True):
    """A non-empty path through schema-owned typed fields."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require immutable static field identifiers."""
        if type(self.segments) is not tuple:
            raise TypeError("static field path segments must be an immutable tuple")
        if not self.segments or any(
            type(segment) is not str or _SEGMENT_PATTERN.fullmatch(segment) is None
            for segment in self.segments
        ):
            raise ValueError("static field path contains an invalid segment")


@final
class JsonPointer(msgspec.Struct, frozen=True, kw_only=True):
    """A canonical RFC 6901 pointer, including the empty root pointer."""

    value: str

    def __post_init__(self) -> None:
        """Reject non-pointers and malformed tilde escapes."""
        if type(self.value) is not str:
            raise TypeError("JSON Pointer must be text")
        if self.value and not self.value.startswith("/"):
            raise ValueError("non-root JSON Pointer must begin with '/'")
        index = 0
        while index < len(self.value):
            if self.value[index] == "~":
                if index + 1 == len(self.value) or self.value[index + 1] not in "01":
                    raise ValueError("JSON Pointer contains an invalid escape")
                index += 2
            else:
                index += 1


@final
class JsonPointerPath(msgspec.Struct, frozen=True, kw_only=True):
    """A statically singular RFC 6901 selector fast path."""

    pointer: JsonPointer

    def __post_init__(self) -> None:
        """Require a canonical pointer."""
        if type(self.pointer) is not JsonPointer:
            raise TypeError("JSON Pointer path requires a JsonPointer")


@final
class JsonPathExpression(msgspec.Struct, frozen=True, kw_only=True):
    """The identity of one successfully compiled RFC 9535 expression."""

    canonical: str
    fingerprint: Fingerprint
    capabilities: SelectorCapabilities

    def __post_init__(self) -> None:
        """Require a root-addressed expression and compiler fingerprint."""
        if type(self.canonical) is not str or not self.canonical.startswith("$"):
            raise ValueError("JSONPath expression must begin at '$'")
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("JSONPath fingerprint must be a Fingerprint")
        if type(self.capabilities) is not SelectorCapabilities:
            raise TypeError("JSONPath capabilities must be SelectorCapabilities")


type SelectorPath = NoPath | StaticFieldPath | JsonPointerPath | JsonPathExpression


def is_selector_path(value: object) -> bool:
    """Return whether a value belongs to the closed path union."""
    return type(value) in {NoPath, StaticFieldPath, JsonPointerPath, JsonPathExpression}
