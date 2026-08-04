"""Tests for the strict v1alpha1 envelope AST and field catalog."""

from typing import cast

import pytest

from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.v1alpha1.ast import V1Alpha1FrontendResult
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Metadata
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Resource
from comparo.core.configuration.v1alpha1.frontend import parse_v1alpha1_documents
from comparo.core.configuration.v1alpha1.schema import RESOURCE_SCHEMAS
from comparo.core.configuration.v1alpha1.schema import FieldMode
from comparo.core.configuration.v1alpha1.schema import FieldSchema
from comparo.core.configuration.v1alpha1.schema import ResourceSchema
from comparo.core.configuration.v1alpha1.schema import schema_for
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.domain.metadata import Metadata

_SOURCE = SourceId(value="entry-config")


def _documents(text: str) -> tuple[ParsedDocument, ...]:
    parsed = parse_yaml_source(source_id=_SOURCE, text=text)
    assert parsed.diagnostics == ()
    return parsed.documents


def _resource(
    *,
    version: str = "comparo/v1alpha1",
    kind: str = "Project",
    metadata: str = "{id: demo, name: Demo, description: Useful, tags: [smoke]}",
    spec: str = "{}",
    extra: str = "",
) -> str:
    return f"apiVersion: {version}\nkind: {kind}\nmetadata: {metadata}\nspec: {spec}\n{extra}"


def test_frontend_parses_strict_metadata_and_every_project_kind() -> None:
    documents = _documents(_resource())
    result = parse_v1alpha1_documents(documents)
    assert result.diagnostics == ()
    resource = result.resources[0]
    assert resource.kind is ResourceKind.PROJECT
    assert resource.metadata.resource_id == ResourceId(value="demo")
    assert resource.metadata.details == Metadata(
        name="Demo",
        description="Useful",
        tags=("smoke",),
    )
    assert {schema.kind for schema in RESOURCE_SCHEMAS} == set(ResourceKind) - {
        ResourceKind.EXECUTION_POLICY
    }
    assert all(schema_for(schema.kind) is schema for schema in RESOURCE_SCHEMAS)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("plain", "configuration.envelope.mapping-required"),
        ("apiVersion: comparo/v1alpha1\n", "configuration.envelope.field-required"),
        (_resource(extra="unexpected: true\n"), "configuration.envelope.field-unknown"),
        (_resource(version="comparo/v1"), "configuration.version.prototype-unsupported"),
        (_resource(version="comparo/v9"), "configuration.version.unsupported"),
        (_resource(version="true"), "configuration.version.unsupported"),
        (_resource(kind="Unknown"), "configuration.kind.unknown"),
        (_resource(kind="ExecutionPolicy"), "configuration.kind.operator-owned"),
        (_resource(kind="true"), "configuration.kind.unknown"),
        (_resource(metadata="plain"), "configuration.metadata.invalid"),
        (_resource(metadata="{name: Demo}"), "configuration.envelope.field-required"),
        (_resource(metadata="{id: demo, name: 2}"), "configuration.metadata.invalid"),
        (
            _resource(metadata="{id: demo, name: Demo, description: 2}"),
            "configuration.metadata.invalid",
        ),
        (_resource(metadata="{id: demo, name: Demo, tags: bad}"), "configuration.metadata.invalid"),
        (
            _resource(metadata="{id: demo, name: Demo, tags: [ok, 2]}"),
            "configuration.metadata.invalid",
        ),
        (_resource(metadata="{id: Bad, name: Demo}"), "configuration.metadata.invalid"),
        (
            _resource(metadata="{id: demo, name: Demo, unknown: true}"),
            "configuration.envelope.field-unknown",
        ),
    ],
)
def test_frontend_aggregates_closed_value_free_diagnostics(text: str, code: str) -> None:
    result = parse_v1alpha1_documents(_documents(text))
    assert code in {diagnostic.code.value for diagnostic in result.diagnostics}
    assert "Demo" not in repr(result.diagnostics)


def test_frontend_and_ast_reject_untyped_direct_construction() -> None:
    valid = parse_v1alpha1_documents(_documents(_resource())).resources[0]
    metadata = valid.metadata
    diagnostic = ConfigurationDiagnostic(
        code=DiagnosticCode(value="configuration.test.invalid"),
        severity=DiagnosticSeverity.ERROR,
        primary=valid.span,
    )
    with pytest.raises(TypeError, match="ResourceId"):
        V1Alpha1Metadata(
            resource_id=cast("ResourceId", "demo"),
            details=metadata.details,
            span=metadata.span,
        )
    with pytest.raises(TypeError, match="details"):
        V1Alpha1Metadata(
            resource_id=metadata.resource_id,
            details=cast("Metadata", "Demo"),
            span=metadata.span,
        )
    with pytest.raises(ValueError, match="name is required"):
        V1Alpha1Metadata(
            resource_id=metadata.resource_id,
            details=Metadata(),
            span=metadata.span,
        )
    with pytest.raises(TypeError, match="SourceSpan"):
        V1Alpha1Metadata(
            resource_id=metadata.resource_id,
            details=metadata.details,
            span=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="ResourceKind"):
        V1Alpha1Resource(
            kind=cast("ResourceKind", "Project"),
            metadata=metadata,
            spec=valid.spec,
            span=valid.span,
        )
    with pytest.raises(TypeError, match="metadata"):
        V1Alpha1Resource(
            kind=valid.kind,
            metadata=cast("V1Alpha1Metadata", metadata.details),
            spec=valid.spec,
            span=valid.span,
        )
    with pytest.raises(TypeError, match="spec"):
        V1Alpha1Resource(
            kind=valid.kind,
            metadata=metadata,
            spec=object(),  # type: ignore[arg-type]
            span=valid.span,
        )
    with pytest.raises(TypeError, match="SourceSpan"):
        V1Alpha1Resource(
            kind=valid.kind,
            metadata=metadata,
            spec=valid.spec,
            span=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="resources"):
        V1Alpha1FrontendResult(resources=cast("tuple[V1Alpha1Resource, ...]", [valid]))
    with pytest.raises(TypeError, match="diagnostics"):
        V1Alpha1FrontendResult(
            diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", [diagnostic])
        )
    with pytest.raises(TypeError, match="ParsedDocument"):
        parse_v1alpha1_documents(cast("tuple[ParsedDocument, ...]", [object()]))


def test_field_catalog_rejects_contradictory_definitions() -> None:
    field = FieldSchema(name="field", mode=FieldMode.STATIC)
    assert field.required is False
    with pytest.raises(ValueError, match="non-empty"):
        FieldSchema(name="", mode=FieldMode.STATIC)
    with pytest.raises(TypeError, match="FieldMode"):
        FieldSchema(name="field", mode=cast("FieldMode", "static"))
    with pytest.raises(TypeError, match="flags"):
        FieldSchema(name="field", mode=FieldMode.STATIC, required=cast("bool", 1))
    with pytest.raises(TypeError, match="typed tuple"):
        FieldSchema(
            name="field",
            mode=FieldMode.REFERENCE,
            reference_kinds=cast("tuple[ResourceKind, ...]", [ResourceKind.VALUE]),
        )
    with pytest.raises(ValueError, match="declared together"):
        FieldSchema(name="field", mode=FieldMode.REFERENCE)
    with pytest.raises(ValueError, match="declared together"):
        FieldSchema(
            name="field",
            mode=FieldMode.STATIC,
            reference_kinds=(ResourceKind.VALUE,),
        )
    with pytest.raises(ValueError, match="exactly one"):
        FieldSchema(
            name="field",
            mode=FieldMode.REFERENCE_UNION_LIST,
            reference_kinds=(ResourceKind.REQUEST, ResourceKind.SCENARIO),
            inline=True,
        )
    schema = ResourceSchema(kind=ResourceKind.VALUE, fields=(field,))
    assert schema.fields == (field,)
    with pytest.raises(TypeError, match="ResourceKind"):
        ResourceSchema(kind=cast("ResourceKind", "Value"), fields=())
    with pytest.raises(TypeError, match="typed tuple"):
        ResourceSchema(kind=ResourceKind.VALUE, fields=cast("tuple[FieldSchema, ...]", [field]))
    with pytest.raises(ValueError, match="unique"):
        ResourceSchema(kind=ResourceKind.VALUE, fields=(field, field))
    with pytest.raises(TypeError, match="boolean"):
        ResourceSchema(kind=ResourceKind.VALUE, fields=(), opaque_spec=cast("bool", 1))
    with pytest.raises(ValueError, match="cannot declare"):
        ResourceSchema(kind=ResourceKind.VALUE, fields=(field,), opaque_spec=True)
    with pytest.raises(TypeError, match="ResourceKind"):
        schema_for(cast("ResourceKind", "Value"))
    with pytest.raises(ValueError, match="no project schema"):
        schema_for(ResourceKind.EXECUTION_POLICY)


def test_environment_health_checks_are_typed_request_references() -> None:
    """Keep Environment probes on the normal Request execution path."""
    environment = schema_for(ResourceKind.ENVIRONMENT)
    health_checks = next(field for field in environment.fields if field.name == "healthChecks")

    assert health_checks.mode is FieldMode.REFERENCE_LIST
    assert health_checks.reference_kinds == (ResourceKind.REQUEST,)
    assert not health_checks.inline
