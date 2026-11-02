"""Tests for source-located configuration frontend models."""

from decimal import Decimal
from typing import cast

import pytest

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationParseResult
from comparo.core.configuration.models import MappingEntry
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.configuration.models import is_configuration_node
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.identity import SourceId
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import SourceLocation


def _location(*, source: str = "project", document: int = 1, line: int = 1) -> SourceLocation:
    return SourceLocation(
        source_id=SourceId(value=source),
        document=PositiveCount(value=document),
        line=PositiveCount(value=line),
        column=PositiveCount(value=1),
    )


def _span(*, source: str = "project", document: int = 1) -> SourceSpan:
    return SourceSpan(
        start=_location(source=source, document=document),
        end=_location(source=source, document=document, line=2),
    )


def _scalar() -> ScalarNode:
    return ScalarNode(value="value", span=_span())


def test_source_span_requires_ordered_endpoints_in_one_document() -> None:
    assert _span().start.line.value == 1
    with pytest.raises(TypeError, match="endpoints"):
        SourceSpan(start=cast("SourceLocation", "line one"), end=_location())
    with pytest.raises(ValueError, match="same document"):
        SourceSpan(start=_location(), end=_location(document=2))
    with pytest.raises(ValueError, match="must not precede"):
        SourceSpan(start=_location(line=2), end=_location(line=1))


def test_configuration_nodes_form_a_closed_immutable_tree() -> None:
    values = (None, True, 2, Decimal("2.5"), "text")
    scalars = tuple(ScalarNode(value=value, span=_span()) for value in values)
    sequence = SequenceNode(items=scalars, span=_span())
    mapping = MappingNode(
        entries=(MappingEntry(name="items", name_span=_span(), value=sequence),),
        span=_span(),
    )

    assert all(is_configuration_node(node) for node in (*scalars, sequence, mapping))
    assert not is_configuration_node(object())
    with pytest.raises(TypeError, match="unsupported value"):
        ScalarNode(value=cast("str", 1.5), span=_span())
    with pytest.raises(TypeError, match="SourceSpan"):
        ScalarNode(value="value", span=cast("SourceSpan", "location"))
    with pytest.raises(TypeError, match="tuple of nodes"):
        SequenceNode(items=cast("tuple[ScalarNode, ...]", [scalars[0]]), span=_span())
    with pytest.raises(TypeError, match="requires a SourceSpan"):
        SequenceNode(items=(), span=cast("SourceSpan", "location"))
    with pytest.raises(TypeError, match="name must be a string"):
        MappingEntry(name=cast("str", 1), name_span=_span(), value=_scalar())
    with pytest.raises(TypeError, match="name requires"):
        MappingEntry(name="field", name_span=cast("SourceSpan", "location"), value=_scalar())
    with pytest.raises(TypeError, match="value must be a node"):
        MappingEntry(name="field", name_span=_span(), value=cast("ScalarNode", "value"))
    with pytest.raises(TypeError, match="tuple of entries"):
        MappingNode(entries=cast("tuple[MappingEntry, ...]", [mapping.entries[0]]), span=_span())
    with pytest.raises(ValueError, match="unique"):
        MappingNode(entries=(mapping.entries[0], mapping.entries[0]), span=_span())
    with pytest.raises(TypeError, match="requires a SourceSpan"):
        MappingNode(entries=(), span=cast("SourceSpan", "location"))


def test_parse_result_and_diagnostics_validate_closed_components() -> None:
    diagnostic = ConfigurationDiagnostic(
        code=DiagnosticCode(value="configuration.test.issue"),
        severity=DiagnosticSeverity.ERROR,
        primary=_span(),
        related=(_span(source="included"),),
    )
    document = ParsedDocument(number=1, root=_scalar())
    result = ConfigurationParseResult(documents=(document,), diagnostics=(diagnostic,))
    assert result.documents == (document,)
    with pytest.raises(TypeError, match="DiagnosticCode"):
        ConfigurationDiagnostic(
            code=cast("DiagnosticCode", "configuration.test.issue"),
            severity=DiagnosticSeverity.ERROR,
            primary=_span(),
        )
    with pytest.raises(TypeError, match="severity"):
        ConfigurationDiagnostic(
            code=diagnostic.code,
            severity=cast("DiagnosticSeverity", "error"),
            primary=_span(),
        )
    with pytest.raises(TypeError, match="primary"):
        ConfigurationDiagnostic(
            code=diagnostic.code,
            severity=DiagnosticSeverity.ERROR,
            primary=cast("SourceSpan", "location"),
        )
    with pytest.raises(TypeError, match="related"):
        ConfigurationDiagnostic(
            code=diagnostic.code,
            severity=DiagnosticSeverity.ERROR,
            primary=_span(),
            related=cast("tuple[SourceSpan, ...]", [_span()]),
        )
    with pytest.raises(TypeError, match="path"):
        ConfigurationDiagnostic(
            code=diagnostic.code,
            severity=DiagnosticSeverity.ERROR,
            primary=_span(),
            path=[],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="positive"):
        ParsedDocument(number=0, root=_scalar())
    with pytest.raises(TypeError, match="root"):
        ParsedDocument(number=1, root=cast("ScalarNode", "root"))
    with pytest.raises(TypeError, match="documents"):
        ConfigurationParseResult(documents=cast("tuple[ParsedDocument, ...]", [document]))
    with pytest.raises(TypeError, match="diagnostics"):
        ConfigurationParseResult(
            diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", [diagnostic])
        )
