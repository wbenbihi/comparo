"""Source-located, immutable syntax models for the configuration frontend."""

from __future__ import annotations

from decimal import Decimal
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticPathSegment
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.provenance import SourceLocation


@final
class SourceSpan(msgspec.Struct, frozen=True, kw_only=True):
    """A closed source interval whose coordinates contain no authored values."""

    start: SourceLocation
    end: SourceLocation

    def __post_init__(self) -> None:
        """Require ordered locations from the same logical document."""
        if type(self.start) is not SourceLocation or type(self.end) is not SourceLocation:
            raise TypeError("source span endpoints must be SourceLocation values")
        if self.start.source_id != self.end.source_id or self.start.document != self.end.document:
            raise ValueError("source span endpoints must belong to the same document")
        start = (self.start.line.value, self.start.column.value)
        end = (self.end.line.value, self.end.column.value)
        if end < start:
            raise ValueError("source span end must not precede its start")


@final
class ConfigurationDiagnostic(msgspec.Struct, frozen=True, kw_only=True):
    """A value-free compiler issue with optional related source locations."""

    code: DiagnosticCode
    severity: DiagnosticSeverity
    primary: SourceSpan
    related: tuple[SourceSpan, ...] = ()
    path: tuple[DiagnosticPathSegment, ...] = ()

    def __post_init__(self) -> None:
        """Reject arbitrary messages, mutable collections, and untyped metadata."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("configuration diagnostic code must be a DiagnosticCode")
        if type(self.severity) is not DiagnosticSeverity:
            raise TypeError("configuration diagnostic severity must be a DiagnosticSeverity")
        if type(self.primary) is not SourceSpan:
            raise TypeError("configuration diagnostic primary location must be a SourceSpan")
        if type(self.related) is not tuple or any(
            type(location) is not SourceSpan for location in self.related
        ):
            raise TypeError("configuration diagnostic related locations must be a tuple of spans")
        if type(self.path) is not tuple:
            raise TypeError("configuration diagnostic path must be an immutable tuple")


type ScalarValue = None | bool | int | Decimal | str


@final
class ScalarNode(msgspec.Struct, frozen=True, kw_only=True):
    """One exact scalar plus its authored source interval."""

    value: ScalarValue
    span: SourceSpan

    def __post_init__(self) -> None:
        """Reject floats and every value outside the closed scalar model."""
        if type(self.value) not in {type(None), bool, int, Decimal, str}:
            raise TypeError("configuration scalar has an unsupported value type")
        if type(self.span) is not SourceSpan:
            raise TypeError("configuration scalar requires a SourceSpan")


@final
class SequenceNode(msgspec.Struct, frozen=True, kw_only=True):
    """An ordered immutable YAML sequence."""

    items: tuple[ConfigurationNode, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        """Require immutable, typed child nodes."""
        if type(self.items) is not tuple or any(
            not is_configuration_node(item) for item in self.items
        ):
            raise TypeError("configuration sequence items must be a tuple of nodes")
        if type(self.span) is not SourceSpan:
            raise TypeError("configuration sequence requires a SourceSpan")


@final
class MappingEntry(msgspec.Struct, frozen=True, kw_only=True):
    """One source-ordered mapping entry with a plain string field name."""

    name: str
    name_span: SourceSpan
    value: ConfigurationNode

    def __post_init__(self) -> None:
        """Require exact typed mapping components."""
        if type(self.name) is not str:
            raise TypeError("configuration mapping name must be a string")
        if type(self.name_span) is not SourceSpan:
            raise TypeError("configuration mapping name requires a SourceSpan")
        if not is_configuration_node(self.value):
            raise TypeError("configuration mapping value must be a node")


@final
class MappingNode(msgspec.Struct, frozen=True, kw_only=True):
    """An ordered immutable YAML mapping with duplicate-free string keys."""

    entries: tuple[MappingEntry, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        """Require typed entries and enforce the parser's uniqueness invariant."""
        if type(self.entries) is not tuple or any(
            type(entry) is not MappingEntry for entry in self.entries
        ):
            raise TypeError("configuration mapping entries must be a tuple of entries")
        names = tuple(entry.name for entry in self.entries)
        if len(names) != len(set(names)):
            raise ValueError("configuration mapping names must be unique")
        if type(self.span) is not SourceSpan:
            raise TypeError("configuration mapping requires a SourceSpan")


type ConfigurationNode = ScalarNode | SequenceNode | MappingNode


def is_configuration_node(value: object) -> TypeGuard[ConfigurationNode]:
    """Narrow arbitrary input to the closed configuration syntax model."""
    return type(value) in {ScalarNode, SequenceNode, MappingNode}


@final
class ParsedDocument(msgspec.Struct, frozen=True, kw_only=True):
    """One non-empty YAML document and its stable one-based document number."""

    number: int
    root: ConfigurationNode

    def __post_init__(self) -> None:
        """Require a positive exact document number and a typed root."""
        if type(self.number) is not int or self.number < 1:
            raise ValueError("parsed document number must be a positive integer")
        if not is_configuration_node(self.root):
            raise TypeError("parsed document root must be a configuration node")


@final
class ConfigurationParseResult(msgspec.Struct, frozen=True, kw_only=True):
    """All non-empty documents and all safe diagnostics produced for one source."""

    documents: tuple[ParsedDocument, ...] = ()
    diagnostics: tuple[ConfigurationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require immutable, typed result collections."""
        if type(self.documents) is not tuple or any(
            type(document) is not ParsedDocument for document in self.documents
        ):
            raise TypeError("parse result documents must be a tuple of ParsedDocument values")
        if type(self.diagnostics) is not tuple or any(
            type(diagnostic) is not ConfigurationDiagnostic for diagnostic in self.diagnostics
        ):
            raise TypeError("parse result diagnostics must be a tuple of diagnostics")
