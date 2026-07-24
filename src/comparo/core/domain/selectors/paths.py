"""Static selector paths and canonical concrete JSON Pointers."""

from __future__ import annotations

import re
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath_derivation import canonical_jsonpath
from comparo.core.domain.selectors.jsonpath_derivation import derive_jsonpath_capabilities
from comparo.core.domain.selectors.jsonpath_derivation import jsonpath_fingerprint
from comparo.core.domain.selectors.sources import SelectorCapabilities

_SEGMENT_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class PathDialect(DomainStrEnum):
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

    ast: JsonPathAst

    def __post_init__(self) -> None:
        """Require a root-addressed expression and compiler fingerprint."""
        if type(self.ast) is not JsonPathAst:
            raise TypeError("JSONPath expression AST must be a JsonPathAst")
        canonical_jsonpath(self.ast)
        derive_jsonpath_capabilities(self.ast)

    @property
    def canonical(self) -> str:
        """Return canonical syntax derived solely from the immutable AST."""
        return canonical_jsonpath(self.ast)

    @property
    def fingerprint(self) -> Fingerprint:
        """Return the compiler-versioned AST identity."""
        return jsonpath_fingerprint(self.ast)

    @property
    def capabilities(self) -> SelectorCapabilities:
        """Return static execution requirements derived solely from the AST."""
        return derive_jsonpath_capabilities(self.ast)


type SelectorPath = NoPath | StaticFieldPath | HttpFieldPath | JsonPointerPath | JsonPathExpression


def is_selector_path(value: object) -> bool:
    """Return whether a value belongs to the closed path union."""
    return type(value) in {
        NoPath,
        StaticFieldPath,
        HttpFieldPath,
        JsonPointerPath,
        JsonPathExpression,
    }
