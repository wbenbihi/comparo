"""Versioned semantic transforms for independent diff sides."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.items import is_singular_item_selector
from comparo.core.domain.selectors.paths import JsonPointer

_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")
BUILTIN_DIFF_NAMESPACE = "comparo.core"
BUILTIN_DIFF_VERSION = "1"


class DiffOperationFamily(StrEnum):
    """Distinct pure pipeline stages."""

    TRANSFORM = "transform"
    REDUCER = "reducer"
    PAIRING = "pairing"
    COMPARATOR = "comparator"


@final
class DiffOperationIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """A namespaced positive-version operation identity retained in artifacts."""

    namespace: str
    family: DiffOperationFamily
    name: str
    version: str

    def __post_init__(self) -> None:
        """Reject ambiguous and unversioned operation identities."""
        for label, value in (("namespace", self.namespace), ("name", self.name)):
            if type(value) is not str or _TOKEN_PATTERN.fullmatch(value) is None:
                raise ValueError(f"diff operation {label} must be a lowercase dotted token")
        if type(self.family) is not DiffOperationFamily:
            raise TypeError("diff operation family must be a DiffOperationFamily")
        if (
            type(self.version) is not str
            or not self.version.isascii()
            or not self.version.isdigit()
        ):
            raise ValueError("diff operation version must be an ASCII decimal version")
        if int(self.version) < 1:
            raise ValueError("diff operation version must be positive")


@final
class RelativeSelection(msgspec.Struct, frozen=True, kw_only=True):
    """Map current values through a relative typed selector."""

    identity: DiffOperationIdentity
    selector: CompiledItemSelector

    def __post_init__(self) -> None:
        """Require matching transform identity and selector."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "relative-select")
        if type(self.selector) is not CompiledItemSelector:
            raise TypeError("relative selection requires a CompiledItemSelector")


@final
class Projection(msgspec.Struct, frozen=True, kw_only=True):
    """Derive only explicitly projected JSON locations."""

    identity: DiffOperationIdentity
    pointers: tuple[JsonPointer, ...]

    def __post_init__(self) -> None:
        """Require a non-empty unique immutable projection."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "project")
        _require_pointers(self.pointers)


@final
class Exclusion(msgspec.Struct, frozen=True, kw_only=True):
    """Remove explicit locations while retaining every other current and future field."""

    identity: DiffOperationIdentity
    pointers: tuple[JsonPointer, ...]

    def __post_init__(self) -> None:
        """Require a non-empty unique immutable exclusion."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "exclude")
        _require_pointers(self.pointers)


class TrimMode(StrEnum):
    """Explicit text trimming."""

    NONE = "none"
    START = "start"
    END = "end"
    BOTH = "both"


class NewlineMode(StrEnum):
    """Explicit newline treatment."""

    PRESERVE = "preserve"
    LF = "lf"


class CaseMode(StrEnum):
    """Explicit case treatment."""

    PRESERVE = "preserve"
    LOWERCASE_UNICODE = "lowercase-unicode"
    CASEFOLD_UNICODE = "casefold-unicode"


@final
class TextNormalization(msgspec.Struct, frozen=True, kw_only=True):
    """Visible normalization with no locale or hidden defaults."""

    identity: DiffOperationIdentity
    trim: TrimMode
    newlines: NewlineMode
    case: CaseMode

    def __post_init__(self) -> None:
        """Require typed normalization choices."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "normalize-text")
        if type(self.trim) is not TrimMode:
            raise TypeError("text trim must be a TrimMode")
        if type(self.newlines) is not NewlineMode:
            raise TypeError("newline treatment must be a NewlineMode")
        if type(self.case) is not CaseMode:
            raise TypeError("case treatment must be a CaseMode")


@final
class CollectionMap(msgspec.Struct, frozen=True, kw_only=True):
    """Map each collection item through one pure relative selector."""

    identity: DiffOperationIdentity
    selector: CompiledItemSelector

    def __post_init__(self) -> None:
        """Require the exact built-in identity and typed relative selector."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "collection-map")
        if type(self.selector) is not CompiledItemSelector:
            raise TypeError("collection map requires a CompiledItemSelector")


@final
class CollectionFilter(msgspec.Struct, frozen=True, kw_only=True):
    """Retain collection items satisfying one constrained pure predicate."""

    identity: DiffOperationIdentity
    predicate: CompiledItemPredicate

    def __post_init__(self) -> None:
        """Require the exact built-in identity and predicate contract."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "collection-filter")
        if type(self.predicate) is not CompiledItemPredicate:
            raise TypeError("collection filter requires a CompiledItemPredicate")


class LengthUnit(StrEnum):
    """Explicit units for string, byte, and collection lengths."""

    UNICODE_SCALARS = "unicode-scalars"
    BYTES = "bytes"
    ITEMS = "items"
    MEMBERS = "members"


@final
class LengthTransform(msgspec.Struct, frozen=True, kw_only=True):
    """Derive a length in one declared type-specific unit."""

    identity: DiffOperationIdentity
    unit: LengthUnit

    def __post_init__(self) -> None:
        """Require the exact built-in identity and a typed unit."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "length")
        if type(self.unit) is not LengthUnit:
            raise TypeError("length transform unit must be a LengthUnit")


@final
class ValueTypeTransform(msgspec.Struct, frozen=True, kw_only=True):
    """Derive the exact Comparo value-type tag."""

    identity: DiffOperationIdentity

    def __post_init__(self) -> None:
        """Require the exact built-in identity."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "value-type")


class JsonShapeArrayMode(StrEnum):
    """How array members contribute to a derived JSON shape."""

    POSITIONAL = "positional"
    UNION = "union"


@final
class JsonShapeTransform(msgspec.Struct, frozen=True, kw_only=True):
    """Derive a deterministic JSON structural shape."""

    identity: DiffOperationIdentity
    arrays: JsonShapeArrayMode

    def __post_init__(self) -> None:
        """Require the exact built-in identity and explicit array semantics."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "json-shape")
        if type(self.arrays) is not JsonShapeArrayMode:
            raise TypeError("JSON shape array mode must be a JsonShapeArrayMode")


class DiffDigestAlgorithm(StrEnum):
    """Initial deterministic digest transforms."""

    SHA256 = "sha256"
    SHA512 = "sha512"


@final
class DigestTransform(msgspec.Struct, frozen=True, kw_only=True):
    """Derive a byte digest without retaining source bytes in the result."""

    identity: DiffOperationIdentity
    algorithm: DiffDigestAlgorithm

    def __post_init__(self) -> None:
        """Require the exact built-in identity and algorithm."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "digest")
        if type(self.algorithm) is not DiffDigestAlgorithm:
            raise TypeError("digest transform algorithm must be a DiffDigestAlgorithm")


class StreamSemanticSource(StrEnum):
    """Application records that deliberately exclude HTTP chunk boundaries."""

    SSE_EVENT = "sse-event"
    SSE_DATA = "sse-data"
    SSE_JSON_DATA = "sse-json-data"
    JSON_RECORD = "json-record"


@final
class StreamProjection(msgspec.Struct, frozen=True, kw_only=True):
    """Project semantic records before reduction and pairing."""

    identity: DiffOperationIdentity
    source: StreamSemanticSource
    selector: CompiledItemSelector

    def __post_init__(self) -> None:
        """Require semantic record source and typed relative selector."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "stream-project")
        if type(self.source) is not StreamSemanticSource:
            raise TypeError("stream source must be a StreamSemanticSource")
        if type(self.selector) is not CompiledItemSelector:
            raise TypeError("stream projection selector must be a CompiledItemSelector")


class SortDirection(StrEnum):
    """Explicit deterministic sort direction."""

    ASCENDING = "ascending"
    DESCENDING = "descending"


@final
class SortBy(msgspec.Struct, frozen=True, kw_only=True):
    """Sort semantic values by a statically singular typed key."""

    identity: DiffOperationIdentity
    key: CompiledItemSelector
    direction: SortDirection

    def __post_init__(self) -> None:
        """Require matching identity, typed key, and direction."""
        require_operation(self.identity, DiffOperationFamily.TRANSFORM, "sort-by")
        if type(self.key) is not CompiledItemSelector:
            raise TypeError("sort key must be a CompiledItemSelector")
        if not is_singular_item_selector(self.key):
            raise ValueError("sort key selector must be statically singular")
        if type(self.direction) is not SortDirection:
            raise TypeError("sort direction must be a SortDirection")


type DiffTransform = (
    RelativeSelection
    | Projection
    | Exclusion
    | TextNormalization
    | CollectionMap
    | CollectionFilter
    | LengthTransform
    | ValueTypeTransform
    | JsonShapeTransform
    | DigestTransform
    | StreamProjection
    | SortBy
)


def is_diff_transform(value: object) -> TypeGuard[DiffTransform]:
    """Narrow to the closed transform union."""
    return type(value) in {
        RelativeSelection,
        Projection,
        Exclusion,
        TextNormalization,
        CollectionMap,
        CollectionFilter,
        LengthTransform,
        ValueTypeTransform,
        JsonShapeTransform,
        DigestTransform,
        StreamProjection,
        SortBy,
    }


def require_operation(
    identity: DiffOperationIdentity,
    family: DiffOperationFamily,
    name: str,
) -> None:
    """Require one exact typed stage identity."""
    require_operation_family(identity, family)
    if identity.name != name:
        raise ValueError("diff identity does not match its typed operation")


def require_operation_family(identity: DiffOperationIdentity, family: DiffOperationFamily) -> None:
    """Require an operation identity from one pipeline stage."""
    if type(identity) is not DiffOperationIdentity:
        raise TypeError("diff identity must be a DiffOperationIdentity")
    if identity.namespace != BUILTIN_DIFF_NAMESPACE or identity.version != BUILTIN_DIFF_VERSION:
        raise ValueError("built-in diff operations require comparo.core identity version 1")
    if identity.family is not family:
        raise ValueError("diff identity belongs to the wrong operation family")


def _require_pointers(pointers: tuple[JsonPointer, ...]) -> None:
    if type(pointers) is not tuple or not pointers:
        raise ValueError("JSON pointers must be a non-empty immutable tuple")
    if any(type(pointer) is not JsonPointer for pointer in pointers):
        raise TypeError("JSON pointers must contain JsonPointer values")
    if len(set(pointers)) != len(pointers):
        raise ValueError("JSON pointers must be unique")
