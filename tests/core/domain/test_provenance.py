"""Tests for value-free provenance chains."""

import pytest

from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import SourceLocation
from comparo.core.domain.provenance import ValueProvenance


def _location() -> SourceLocation:
    return SourceLocation(
        source_id="project-config",
        document=PositiveCount(value=1),
        line=PositiveCount(value=2),
        column=PositiveCount(value=3),
    )


def test_source_location_requires_safe_identifier_and_typed_coordinates() -> None:
    assert _location().line.value == 2
    with pytest.raises(ValueError, match="non-blank text"):
        SourceLocation(
            source_id=" ",
            document=PositiveCount(value=1),
            line=PositiveCount(value=1),
            column=PositiveCount(value=1),
        )
    with pytest.raises(TypeError):
        SourceLocation(
            source_id="project-config",
            document=1,  # type: ignore[arg-type]
            line=PositiveCount(value=1),
            column=PositiveCount(value=1),
        )


def test_provenance_entry_validates_typed_optional_components() -> None:
    resource = ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.PROJECT,
        resource_id=ResourceId(value="api"),
    )
    entry = ProvenanceEntry(
        kind=ProvenanceKind.PROJECT,
        resource=resource,
        location=_location(),
    )
    assert entry.resource == resource
    with pytest.raises(TypeError):
        ProvenanceEntry(kind="project")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProvenanceEntry(kind=ProvenanceKind.PROJECT, resource="Project/api")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProvenanceEntry(kind=ProvenanceKind.PROJECT, location="line 2")  # type: ignore[arg-type]


def test_value_provenance_is_non_empty_typed_and_immutable() -> None:
    entry = ProvenanceEntry(kind=ProvenanceKind.VERSIONED_DEFAULT)
    assert ValueProvenance(entries=(entry,)).entries == (entry,)
    with pytest.raises(ValueError, match="at least one"):
        ValueProvenance(entries=())
    with pytest.raises(TypeError):
        ValueProvenance(entries=[entry])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ValueProvenance(entries=("default",))  # type: ignore[arg-type]
