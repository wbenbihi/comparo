"""Authored selector lowering shared by assertion and diff configuration adapters."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.http_fields import HttpFieldProjection
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.occurrences import AllAttemptsOccurrence
from comparo.core.domain.selectors.occurrences import AllExchangesOccurrence
from comparo.core.domain.selectors.occurrences import ObservationOccurrenceSelector
from comparo.core.domain.selectors.occurrences import TerminalOccurrence
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.selector_semantics import derive_selector_output_contract
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.static_fields import STATIC_FIELD_CATALOG
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.selection import PathCompileReady
from comparo.core.selection import try_compile_json_pointer
from comparo.core.selection import try_compile_jsonpath

_BODY_SOURCES = frozenset({SelectorSource.REQUEST_BODY, SelectorSource.RESPONSE_BODY})
_FIELD_PREFIXES: tuple[tuple[str, SelectorSource], ...] = (
    ("request.query:", SelectorSource.REQUEST_QUERY),
    ("request.headers:", SelectorSource.REQUEST_HEADERS),
    ("request.cookies:", SelectorSource.REQUEST_COOKIES),
    ("response.informational:", SelectorSource.RESPONSE_INFORMATIONAL),
    ("response.headers:", SelectorSource.RESPONSE_HEADERS),
    ("response.trailers:", SelectorSource.RESPONSE_TRAILERS),
)


@final
class SelectorCompileError(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free authored selector rejection suitable for a compilation diagnostic."""

    alias: str | None = None


class InvalidSelectorError(ValueError):
    """Internal control-flow exception carrying only a bounded public selector alias."""

    def __init__(self, error: SelectorCompileError) -> None:
        """Retain one value-free compilation error for the diagnostic adapter."""
        super().__init__("selector cannot be lowered to the supported typed catalog")
        self.error = error


def compile_selector(
    expression: ValueExpression | None,
    *,
    fingerprint: Fingerprint,
    default_body_view: BodyViewKind,
) -> CompiledSelector:
    """Compile a concise or structured selector into the shared typed selector AST."""
    if type(fingerprint) is not Fingerprint or type(default_body_view) is not BodyViewKind:
        raise TypeError("selector compilation requires typed fingerprint and body-view default")
    text = _literal_text(expression)
    if text is not None:
        source, path, view, occurrence = _compile_shorthand(text, default_body_view)
    elif type(expression) is ObjectExpr:
        source, path, view, occurrence = _compile_structured(expression, default_body_view)
    else:
        raise InvalidSelectorError(SelectorCompileError())
    capabilities = derive_selector_capabilities(source, path, occurrence)
    try:
        return CompiledSelector(
            fingerprint=fingerprint,
            source=source,
            path=path,
            capabilities=capabilities,
            output=derive_selector_output_contract(source, path, view, occurrence),
            body_view=view,
            occurrence=occurrence,
        )
    except (TypeError, ValueError) as error:
        raise InvalidSelectorError(SelectorCompileError(alias=_bounded_alias(text))) from error


def _compile_shorthand(
    text: str,
    default_body_view: BodyViewKind,
) -> tuple[SelectorSource, SelectorPath, BodyViewKind | None, ObservationOccurrenceSelector]:
    aliases = {"status": "response.status", "body": "response.body"}
    normalized = aliases.get(text, text)
    for prefix, source in _FIELD_PREFIXES:
        if normalized.startswith(prefix) and len(normalized) > len(prefix):
            return source, HttpFieldPath(name=normalized[len(prefix) :]), None, TerminalOccurrence()
    static = next(
        (
            (source, path)
            for source, path, _kind in STATIC_FIELD_CATALOG
            if ".".join(path.segments) == normalized
        ),
        None,
    )
    if static is not None:
        return static[0], static[1], None, TerminalOccurrence()
    try:
        source = SelectorSource(normalized)
    except ValueError as error:
        raise InvalidSelectorError(SelectorCompileError()) from error
    view = default_body_view if source in _BODY_SOURCES else None
    return source, NoPath(), view, TerminalOccurrence()


def _compile_structured(
    expression: ObjectExpr,
    default_body_view: BodyViewKind,
) -> tuple[SelectorSource, SelectorPath, BodyViewKind | None, ObservationOccurrenceSelector]:
    fields = {member.name: member.expression for member in expression.members}
    if not set(fields) <= {"source", "view", "path", "field", "occurrence"}:
        raise InvalidSelectorError(SelectorCompileError())
    source_text = _literal_text(fields.get("source"))
    if source_text is None:
        raise InvalidSelectorError(SelectorCompileError())
    try:
        source = SelectorSource(source_text)
    except ValueError as error:
        raise InvalidSelectorError(
            SelectorCompileError(alias=_bounded_alias(source_text))
        ) from error
    view = _body_view(source, fields.get("view"), default_body_view)
    occurrence = _occurrence(fields.get("occurrence"))
    if "field" in fields and "path" in fields:
        raise InvalidSelectorError(SelectorCompileError(alias=_bounded_alias(source_text)))
    path: SelectorPath
    if "field" in fields:
        path = _http_field(fields["field"])
    elif "path" in fields:
        path = _path(source, fields["path"])
    else:
        path = NoPath()
    return source, path, view, occurrence


def _body_view(
    source: SelectorSource,
    expression: ValueExpression | None,
    default: BodyViewKind,
) -> BodyViewKind | None:
    if source not in _BODY_SOURCES:
        if expression is not None:
            raise InvalidSelectorError(SelectorCompileError(alias=source.value))
        return None
    text = _literal_text(expression)
    if expression is None:
        return default
    if text is None:
        raise InvalidSelectorError(SelectorCompileError(alias=source.value))
    try:
        return BodyViewKind(text)
    except ValueError as error:
        raise InvalidSelectorError(SelectorCompileError(alias=source.value)) from error


def _occurrence(expression: ValueExpression | None) -> ObservationOccurrenceSelector:
    if expression is None:
        return TerminalOccurrence()
    text = _literal_text(expression)
    if text == "terminal":
        return TerminalOccurrence()
    if text == "allAttempts":
        return AllAttemptsOccurrence()
    if text == "allExchanges":
        return AllExchangesOccurrence()
    raise InvalidSelectorError(SelectorCompileError(alias=_bounded_alias(text)))


def _http_field(expression: ValueExpression) -> HttpFieldPath:
    text = _literal_text(expression)
    if text is not None:
        return HttpFieldPath(name=text)
    if type(expression) is not ObjectExpr:
        raise InvalidSelectorError(SelectorCompileError())
    fields = {member.name: member.expression for member in expression.members}
    if not set(fields) <= {"name", "occurrence", "projection"}:
        raise InvalidSelectorError(SelectorCompileError())
    name = _literal_text(fields.get("name"))
    occurrence = _literal_integer(fields.get("occurrence"))
    projection_text = _literal_text(fields.get("projection"))
    if name is None or ("occurrence" in fields and occurrence is None):
        raise InvalidSelectorError(SelectorCompileError())
    try:
        projection = (
            HttpFieldProjection.RAW_VALUE
            if projection_text is None
            else HttpFieldProjection(projection_text)
        )
        return HttpFieldPath(
            name=name,
            occurrence=None if occurrence is None else Count(value=occurrence),
            projection=projection,
        )
    except (TypeError, ValueError) as error:
        raise InvalidSelectorError(SelectorCompileError()) from error


def _path(source: SelectorSource, expression: ValueExpression) -> SelectorPath:
    text = _literal_text(expression)
    if text is None:
        raise InvalidSelectorError(SelectorCompileError(alias=source.value))
    if text.startswith("$"):
        compiled = try_compile_jsonpath(text)
    elif text == "" or text.startswith("/"):
        compiled = try_compile_json_pointer(text)
    else:
        return StaticFieldPath(segments=tuple(text.split(".")))
    if type(compiled) is not PathCompileReady:
        raise InvalidSelectorError(SelectorCompileError(alias=source.value))
    path = compiled.path
    if type(path) not in {JsonPointerPath, JsonPathExpression}:
        raise InvalidSelectorError(SelectorCompileError(alias=source.value))
    return path


def _literal_text(value: ValueExpression | None) -> str | None:
    return (
        value.value.value
        if type(value) is LiteralExpr and type(value.value) is JsonString
        else None
    )


def _literal_integer(value: ValueExpression | None) -> int | None:
    return (
        value.value.value
        if type(value) is LiteralExpr and type(value.value) is JsonInteger
        else None
    )


def _bounded_alias(value: str | None) -> str | None:
    return value if value is not None and len(value) <= 64 else None
