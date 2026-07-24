"""Tests for the constructor-free YAML 1.2-subset parser."""

from decimal import Decimal
from typing import NoReturn
from typing import cast

import pytest
import yaml

from comparo.core.configuration import yaml_parser
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationParseResult
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.yaml_parser import parse_yaml_source
from comparo.core.domain.identity import SourceId

_SOURCE = SourceId(value="project-config")


def _mapping(text: str) -> MappingNode:
    result = parse_yaml_source(source_id=_SOURCE, text=text)
    assert result.diagnostics == ()
    assert len(result.documents) == 1
    return cast("MappingNode", result.documents[0].root)


def test_parser_uses_exact_yaml_12_scalars_and_preserves_order_and_locations() -> None:
    root = _mapping(
        "nullValue: null\n"
        "trueValue: true\n"
        "legacyBool: yes\n"
        "integer: -42\n"
        "decimal: 1.250\n"
        "exponent: 2e3\n"
        "text: hello\n"
        "items: [one, 2]\n"
    )
    values = {entry.name: cast("ScalarNode", entry.value).value for entry in root.entries[:-1]}
    assert values == {
        "nullValue": None,
        "trueValue": True,
        "legacyBool": "yes",
        "integer": -42,
        "decimal": Decimal("1.250"),
        "exponent": Decimal("2E+3"),
        "text": "hello",
    }
    assert tuple(entry.name for entry in root.entries) == (
        "nullValue",
        "trueValue",
        "legacyBool",
        "integer",
        "decimal",
        "exponent",
        "text",
        "items",
    )
    sequence = cast("SequenceNode", root.entries[-1].value)
    assert tuple(cast("ScalarNode", item).value for item in sequence.items) == ("one", 2)
    assert root.entries[1].name_span.start.line.value == 2
    assert root.entries[1].name_span.start.column.value == 1


def test_parser_accepts_multiple_documents_and_ignores_empty_documents() -> None:
    result = parse_yaml_source(
        source_id=_SOURCE,
        text="---\n---\nkind: Project\n---\n\n---\nkind: Value\n",
    )
    assert result.diagnostics == ()
    assert tuple(document.number for document in result.documents) == (2, 4)
    assert all(type(document.root) is MappingNode for document in result.documents)
    assert result.documents[1].root.span.start.document.value == 4


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("value: &shared secret\nother: *shared\n", ("anchor-forbidden", "alias-forbidden")),
        ("value: !custom secret\n", ("tag-forbidden",)),
        ("%YAML 1.2\n---\nvalue: text\n", ("directive-forbidden",)),
    ],
)
def test_parser_rejects_yaml_features_that_create_ambiguity(
    text: str,
    codes: tuple[str, ...],
) -> None:
    result = parse_yaml_source(source_id=_SOURCE, text=text)
    assert result.documents == ()
    assert (
        tuple(diagnostic.code.value.rsplit(".", 1)[-1] for diagnostic in result.diagnostics)
        == codes
    )


def test_parser_reports_duplicate_merge_and_non_string_keys_without_values() -> None:
    secret = "super-secret-material"
    result = parse_yaml_source(
        source_id=_SOURCE,
        text=(f"name: first\nname: second\n<<: inherited\n42: hidden\ntoken: {secret}\n"),
    )
    assert tuple(diagnostic.code.value for diagnostic in result.diagnostics) == (
        "configuration.yaml.duplicate-key",
        "configuration.yaml.merge-key-forbidden",
        "configuration.yaml.non-string-key",
    )
    duplicate = result.diagnostics[0]
    assert duplicate.primary.start.line.value == 2
    assert duplicate.related[0].start.line.value == 1
    assert secret not in repr(result.diagnostics)


def test_parser_reports_syntax_errors_at_the_actual_document() -> None:
    result = parse_yaml_source(
        source_id=_SOURCE,
        text="---\nkind: Project\n---\nbroken: [\n",
    )
    assert result.documents == ()
    assert tuple(item.code.value for item in result.diagnostics) == ("configuration.yaml.syntax",)
    assert result.diagnostics[0].primary.start.document.value == 2


def test_parser_validates_boundary_types() -> None:
    with pytest.raises(TypeError, match="logical SourceId"):
        parse_yaml_source(source_id=cast("SourceId", "project"), text="value: one")
    with pytest.raises(TypeError, match="must be a string"):
        parse_yaml_source(source_id=_SOURCE, text=cast("str", b"value: one"))


def test_parser_defensively_converts_unknown_scalar_tags_to_safe_diagnostics() -> None:
    mark = yaml.Mark("source", 0, 0, 0, "", 0)
    node = yaml.nodes.ScalarNode(
        tag="tag:example.invalid,2026:unknown",
        value="authored-value",
        start_mark=mark,
        end_mark=mark,
    )
    diagnostics: list[ConfigurationDiagnostic] = []
    converted = yaml_parser._convert_node(_SOURCE, 1, node, diagnostics)
    assert cast("ScalarNode", converted).value == "authored-value"
    assert tuple(item.code.value for item in diagnostics) == (
        "configuration.yaml.scalar-unsupported",
    )


def test_parser_defensively_handles_markless_scanner_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_scan(*args: object, **kwargs: object) -> NoReturn:
        raise yaml.YAMLError

    monkeypatch.setattr(yaml, "scan", fail_scan)
    result = parse_yaml_source(source_id=_SOURCE, text="value: text")
    assert result.documents == ()
    assert result.diagnostics[0].code.value == "configuration.yaml.syntax"


def test_parser_preserves_scanner_error_coordinates() -> None:
    result = parse_yaml_source(source_id=_SOURCE, text="\tinvalid: indentation")
    assert result.documents == ()
    assert result.diagnostics[0].code.value == "configuration.yaml.syntax"
    assert result.diagnostics[0].primary.start.column.value == 1


def test_parser_defensively_handles_markless_composer_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_compose(*args: object, **kwargs: object) -> NoReturn:
        raise yaml.YAMLError

    monkeypatch.setattr(yaml, "compose_all", fail_compose)
    result = parse_yaml_source(source_id=_SOURCE, text="value: text")
    assert result.documents == ()
    assert result.diagnostics[0].code.value == "configuration.yaml.syntax"


def test_parser_tolerates_a_none_empty_document_from_the_yaml_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def empty_compose(*args: object, **kwargs: object) -> object:
        return iter((None,))

    monkeypatch.setattr(yaml, "compose_all", empty_compose)
    result = parse_yaml_source(source_id=_SOURCE, text="")
    assert result == ConfigurationParseResult()
