"""A safe YAML 1.2-subset parser that never constructs application objects."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import cast

import yaml
from yaml import nodes
from yaml import tokens

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import ConfigurationParseResult
from comparo.core.configuration.models import MappingEntry
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.identity import SourceId
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import SourceLocation

_NULL_TAG = "tag:yaml.org,2002:null"
_BOOL_TAG = "tag:yaml.org,2002:bool"
_INT_TAG = "tag:yaml.org,2002:int"
_FLOAT_TAG = "tag:yaml.org,2002:float"
_STRING_TAG = "tag:yaml.org,2002:str"


class _Yaml12Loader(yaml.BaseLoader):
    """A compose-only loader with a deliberately small YAML 1.2 scalar vocabulary."""

    yaml_implicit_resolvers: dict[str | None, list[tuple[str, re.Pattern[str]]]] = {}  # noqa: RUF012


_NULL_PATTERN = re.compile(r"^(?:~|null|Null|NULL)?$")
_Yaml12Loader.add_implicit_resolver(  # type: ignore[no-untyped-call]
    _NULL_TAG,
    _NULL_PATTERN,
    "~nN",
)
_Yaml12Loader.yaml_implicit_resolvers[""] = [(_NULL_TAG, _NULL_PATTERN)]
_Yaml12Loader.add_implicit_resolver(
    _BOOL_TAG,
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    "tTfF",
)  # type: ignore[no-untyped-call]
_Yaml12Loader.add_implicit_resolver(
    _INT_TAG,
    re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"),
    "-+0123456789",
)  # type: ignore[no-untyped-call]
_Yaml12Loader.add_implicit_resolver(
    _FLOAT_TAG,
    re.compile(
        r"^[-+]?(?:(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?|"
        r"[0-9]+[eE][-+]?[0-9]+)$"
    ),
    "-+0123456789.",
)  # type: ignore[no-untyped-call]

_SYNTAX_ERROR = DiagnosticCode(value="configuration.yaml.syntax")
_FORBIDDEN_ANCHOR = DiagnosticCode(value="configuration.yaml.anchor-forbidden")
_FORBIDDEN_ALIAS = DiagnosticCode(value="configuration.yaml.alias-forbidden")
_FORBIDDEN_TAG = DiagnosticCode(value="configuration.yaml.tag-forbidden")
_FORBIDDEN_DIRECTIVE = DiagnosticCode(value="configuration.yaml.directive-forbidden")
_NON_STRING_KEY = DiagnosticCode(value="configuration.yaml.non-string-key")
_DUPLICATE_KEY = DiagnosticCode(value="configuration.yaml.duplicate-key")
_MERGE_KEY = DiagnosticCode(value="configuration.yaml.merge-key-forbidden")
_UNSUPPORTED_SCALAR = DiagnosticCode(value="configuration.yaml.scalar-unsupported")


def _location(source_id: SourceId, document: int, line: int, column: int) -> SourceLocation:
    return SourceLocation(
        source_id=source_id,
        document=PositiveCount(value=document),
        line=PositiveCount(value=line + 1),
        column=PositiveCount(value=column + 1),
    )


def _span(source_id: SourceId, document: int, start: yaml.Mark, end: yaml.Mark) -> SourceSpan:
    return SourceSpan(
        start=_location(source_id, document, start.line, start.column),
        end=_location(source_id, document, end.line, end.column),
    )


def _diagnostic(
    code: DiagnosticCode,
    span: SourceSpan,
    *,
    related: tuple[SourceSpan, ...] = (),
) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        primary=span,
        related=related,
    )


def _document_at(text: str, index: int) -> int:
    """Derive the one-based document number from preceding document markers."""
    markers = re.findall(r"(?m)^---(?:[ \t]*(?:#.*)?)?$", text[: index + 1])
    return max(1, len(markers))


def _scan_forbidden_syntax(
    source_id: SourceId,
    text: str,
) -> tuple[ConfigurationDiagnostic, ...]:
    diagnostics: list[ConfigurationDiagnostic] = []
    try:
        for token in yaml.scan(text, Loader=_Yaml12Loader):
            code: DiagnosticCode | None = None
            if type(token) is tokens.AnchorToken:
                code = _FORBIDDEN_ANCHOR
            elif type(token) is tokens.AliasToken:
                code = _FORBIDDEN_ALIAS
            elif type(token) is tokens.TagToken:
                code = _FORBIDDEN_TAG
            elif type(token) is tokens.DirectiveToken:
                code = _FORBIDDEN_DIRECTIVE
            if code is not None:
                document = _document_at(text, token.start_mark.index)
                diagnostics.append(
                    _diagnostic(
                        code,
                        _span(source_id, document, token.start_mark, token.end_mark),
                    )
                )
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        if not isinstance(mark, yaml.Mark):
            mark = yaml.Mark("source", 0, 0, 0, "", 0)
        document = _document_at(text, mark.index)
        diagnostics.append(_diagnostic(_SYNTAX_ERROR, _span(source_id, document, mark, mark)))
    return tuple(diagnostics)


def _scalar_value(node: nodes.ScalarNode) -> None | bool | int | Decimal | str:
    if node.tag == _NULL_TAG:
        return None
    if node.tag == _BOOL_TAG:
        return cast("str", node.value).lower() == "true"
    if node.tag == _INT_TAG:
        return int(node.value, 10)
    if node.tag == _FLOAT_TAG:
        return Decimal(node.value)
    if node.tag == _STRING_TAG:
        return cast("str", node.value)
    raise ValueError("unsupported scalar tag")


def _convert_node(
    source_id: SourceId,
    document: int,
    node: nodes.Node,
    diagnostics: list[ConfigurationDiagnostic],
) -> ConfigurationNode:
    node_span = _span(source_id, document, node.start_mark, node.end_mark)
    if type(node) is nodes.ScalarNode:
        scalar = node
        try:
            value = _scalar_value(scalar)
        except (ArithmeticError, ValueError):
            diagnostics.append(_diagnostic(_UNSUPPORTED_SCALAR, node_span))
            value = scalar.value
        return ScalarNode(value=value, span=node_span)
    if type(node) is nodes.SequenceNode:
        sequence = node
        return SequenceNode(
            items=tuple(
                _convert_node(source_id, document, item, diagnostics) for item in sequence.value
            ),
            span=node_span,
        )
    mapping = cast("nodes.MappingNode", node)
    entries: list[MappingEntry] = []
    first_names: dict[str, SourceSpan] = {}
    for raw_name, raw_value in mapping.value:
        name_span = _span(source_id, document, raw_name.start_mark, raw_name.end_mark)
        if type(raw_name) is not nodes.ScalarNode or raw_name.tag != _STRING_TAG:
            diagnostics.append(_diagnostic(_NON_STRING_KEY, name_span))
            continue
        name = raw_name.value
        if name == "<<":
            diagnostics.append(_diagnostic(_MERGE_KEY, name_span))
            continue
        if name in first_names:
            diagnostics.append(_diagnostic(_DUPLICATE_KEY, name_span, related=(first_names[name],)))
            continue
        first_names[name] = name_span
        entries.append(
            MappingEntry(
                name=name,
                name_span=name_span,
                value=_convert_node(source_id, document, raw_value, diagnostics),
            )
        )
    return MappingNode(entries=tuple(entries), span=node_span)


def _diagnostic_order(diagnostic: ConfigurationDiagnostic) -> tuple[int, int, str]:
    return (
        diagnostic.primary.start.line.value,
        diagnostic.primary.start.column.value,
        diagnostic.code.value,
    )


def _is_empty_document(root: nodes.Node | None) -> bool:
    """Recognize PyYAML's null-scalar representation of an empty document."""
    return root is None or (
        type(root) is nodes.ScalarNode and root.tag == _NULL_TAG and root.value == ""
    )


def parse_yaml_source(*, source_id: SourceId, text: str) -> ConfigurationParseResult:
    """Parse all YAML documents without constructors and aggregate safe diagnostics."""
    if type(source_id) is not SourceId:
        raise TypeError("YAML source requires a logical SourceId")
    if type(text) is not str:
        raise TypeError("YAML source text must be a string")
    forbidden = _scan_forbidden_syntax(source_id, text)
    if forbidden:
        return ConfigurationParseResult(diagnostics=tuple(sorted(forbidden, key=_diagnostic_order)))
    diagnostics: list[ConfigurationDiagnostic] = []
    documents: list[ParsedDocument] = []
    try:
        composed = yaml.compose_all(text, Loader=_Yaml12Loader)
        for document, root in enumerate(composed, start=1):
            if _is_empty_document(root):
                continue
            typed_root = cast("nodes.Node", root)
            documents.append(
                ParsedDocument(
                    number=document,
                    root=_convert_node(source_id, document, typed_root, diagnostics),
                )
            )
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        if not isinstance(mark, yaml.Mark):
            mark = yaml.Mark("source", 0, 0, 0, "", 0)
        document = _document_at(text, mark.index)
        diagnostics.append(_diagnostic(_SYNTAX_ERROR, _span(source_id, document, mark, mark)))
        documents.clear()
    return ConfigurationParseResult(
        documents=tuple(documents),
        diagnostics=tuple(sorted(diagnostics, key=_diagnostic_order)),
    )
