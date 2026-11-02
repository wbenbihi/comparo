"""Tests for generated schemas and conservative migration classification."""

import json
from pathlib import Path
from typing import cast

import pytest

from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.migrations import MigrationResult
from comparo.core.configuration.migrations import classify_migration
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.schema_generation import generate_v1alpha1_schema
from comparo.core.configuration.schema_generation import render_v1alpha1_schema
from comparo.core.configuration.v1alpha1.schema import RESOURCE_SCHEMAS
from comparo.core.configuration.v1alpha1.schema import FieldMode
from comparo.core.configuration.v1alpha1.schema import FieldSchema
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId

_SOURCE = SourceId(value="migration")


def _documents(version: str, *, root: str | None = None) -> tuple[ParsedDocument, ...]:
    text = root or (
        f"apiVersion: {version}\nkind: Project\nmetadata: {{id: demo, name: Demo}}\nspec: {{}}\n"
    )
    return parse_yaml_source(source_id=_SOURCE, text=text).documents


def test_generated_schema_covers_every_supported_kind_and_strict_envelope() -> None:
    schema = generate_v1alpha1_schema()
    definitions = cast("dict[str, object]", schema["$defs"])
    expected = {resource.kind.value for resource in RESOURCE_SCHEMAS}
    assert expected <= set(definitions)
    assert len(cast("list[object]", schema["oneOf"])) == len(expected)
    project = cast("dict[str, object]", definitions[ResourceKind.PROJECT.value])
    assert project["additionalProperties"] is False
    assert render_v1alpha1_schema().endswith("\n")
    assert render_v1alpha1_schema() == render_v1alpha1_schema()
    checked_in = (
        Path(__file__).parents[3] / "src" / "comparo" / "schemas" / "comparo-v1alpha1.schema.json"
    )
    assert checked_in.read_text(encoding="utf-8") == render_v1alpha1_schema()


def test_environment_health_check_schema_rejects_duplicate_requests() -> None:
    """Keep the generated editor contract aligned with compiler selection semantics."""
    definitions = cast("dict[str, object]", generate_v1alpha1_schema()["$defs"])
    environment = cast("dict[str, object]", definitions[ResourceKind.ENVIRONMENT.value])
    properties = cast("dict[str, object]", environment["properties"])
    spec = cast("dict[str, object]", properties["spec"])
    spec_properties = cast("dict[str, object]", spec["properties"])
    health_checks = cast("dict[str, object]", spec_properties["healthChecks"])

    assert health_checks["uniqueItems"] is True


@pytest.mark.parametrize(
    ("mode", "inline"),
    [
        (FieldMode.STATIC, False),
        (FieldMode.STRUCTURED, False),
        (FieldMode.RESOLVABLE, False),
        (FieldMode.ASSERTION_RULES, False),
        (FieldMode.DIFF_RULES, False),
        (FieldMode.REFERENCE, False),
        (FieldMode.REFERENCE, True),
        (FieldMode.REFERENCE_LIST, False),
        (FieldMode.REFERENCE_UNION_LIST, False),
        (FieldMode.SCHEMA, True),
    ],
)
def test_generated_schema_handles_every_field_compilation_mode(
    mode: FieldMode,
    inline: bool,
) -> None:
    kinds = (
        (ResourceKind.VALUE,)
        if mode
        in {
            FieldMode.REFERENCE,
            FieldMode.REFERENCE_LIST,
            FieldMode.REFERENCE_UNION_LIST,
            FieldMode.SCHEMA,
        }
        else ()
    )
    fields = (
        (FieldSchema(name="nested", mode=FieldMode.STATIC),) if mode is FieldMode.STRUCTURED else ()
    )
    field = FieldSchema(
        name="field",
        mode=mode,
        reference_kinds=kinds,
        inline=inline,
        fields=fields,
    )
    assert field.mode is mode
    assert generate_v1alpha1_schema()["$schema"] == ("https://json-schema.org/draft/2020-12/schema")


def test_structured_field_schema_rejects_incoherent_members() -> None:
    with pytest.raises(TypeError, match="structured field members"):
        FieldSchema(
            name="field",
            mode=FieldMode.STRUCTURED,
            fields=cast("tuple[FieldSchema, ...]", [object()]),
        )
    with pytest.raises(ValueError, match="structured field modes"):
        FieldSchema(name="field", mode=FieldMode.STRUCTURED)
    with pytest.raises(ValueError, match="structured field modes"):
        FieldSchema(
            name="field",
            mode=FieldMode.STATIC,
            fields=(FieldSchema(name="nested", mode=FieldMode.STATIC),),
        )
    with pytest.raises(TypeError, match="flags"):
        FieldSchema(
            name="field",
            mode=FieldMode.REFERENCE_LIST,
            reference_kinds=(ResourceKind.VALUE,),
            unique_items=cast("bool", 1),
        )
    with pytest.raises(ValueError, match="reference-list"):
        FieldSchema(name="field", mode=FieldMode.STATIC, unique_items=True)


def test_migration_is_identity_for_v1alpha1_and_explicitly_refuses_prototype() -> None:
    current = _documents("comparo/v1alpha1")
    assert classify_migration(current) == MigrationResult(documents=current)
    prototype = classify_migration(_documents("comparo/v1"))
    assert prototype.documents == ()
    assert prototype.diagnostics[0].code.value == ("configuration.migration.prototype-unsupported")
    unknown = classify_migration(_documents("comparo/v9"))
    assert unknown.diagnostics[0].code.value == "configuration.migration.source-unsupported"
    malformed = classify_migration(_documents("ignored", root="plain"))
    assert malformed.diagnostics[0].code.value == "configuration.migration.source-unsupported"
    missing = classify_migration(_documents("ignored", root="kind: Project"))
    assert missing.diagnostics[0].code.value == "configuration.migration.source-unsupported"
    assert classify_migration(()) == MigrationResult()


def test_migration_result_and_boundary_reject_incoherent_types() -> None:
    document = _documents("comparo/v1alpha1")[0]
    diagnostic = ConfigurationDiagnostic(
        code=DiagnosticCode(value="configuration.migration.test"),
        severity=DiagnosticSeverity.ERROR,
        primary=document.root.span,
    )
    with pytest.raises(TypeError, match="documents"):
        MigrationResult(documents=cast("tuple[ParsedDocument, ...]", [document]))
    with pytest.raises(TypeError, match="diagnostics"):
        MigrationResult(diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", [diagnostic]))
    with pytest.raises(ValueError, match="alongside"):
        MigrationResult(documents=(document,), diagnostics=(diagnostic,))
    with pytest.raises(TypeError, match="ParsedDocument"):
        classify_migration(cast("tuple[ParsedDocument, ...]", [object()]))


def test_migration_behavior_matches_golden_fixtures() -> None:
    fixtures = Path(__file__).parents[2] / "fixtures" / "configuration" / "migrations"
    golden = cast(
        "dict[str, str]",
        json.loads((fixtures / "golden.json").read_text(encoding="utf-8")),
    )
    actual: dict[str, str] = {}
    for name in sorted(golden):
        parsed = parse_yaml_source(
            source_id=_SOURCE,
            text=(fixtures / name).read_text(encoding="utf-8"),
        )
        result = classify_migration(parsed.documents)
        actual[name] = result.diagnostics[0].code.value if result.diagnostics else "identity"
    assert actual == golden
