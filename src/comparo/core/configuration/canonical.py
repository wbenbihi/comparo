"""Version-neutral, directive-free compiled configuration graph."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.provenance import SourceLocation
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import is_exact_value


@final
class LinkedResource(msgspec.Struct, frozen=True, kw_only=True):
    """A context-typed resource reference resolved to an exact definition revision."""

    target: DefinitionRef

    def __post_init__(self) -> None:
        """Require a complete linked target identity and fingerprint."""
        if type(self.target) is not DefinitionRef:
            raise TypeError("linked resource target must be a DefinitionRef")


@final
class RemoteSchema(msgspec.Struct, frozen=True, kw_only=True):
    """An absolute HTTP(S) schema identity whose loading remains policy-controlled."""

    uri: str

    def __post_init__(self) -> None:
        """Require a non-payload HTTP(S) URI that is safe to retain in artifacts."""
        if type(self.uri) is not str or not self.uri.startswith(("https://", "http://")):
            raise ValueError("remote schema must be a bounded absolute HTTP(S) URI")
        try:
            NamedCapabilityTarget(
                kind=CapabilityTargetKind.SCHEMA_SOURCE,
                identifier=self.uri,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("remote schema must be a bounded non-payload HTTP(S) URI") from error


@final
class CanonicalArray(msgspec.Struct, frozen=True, kw_only=True):
    """An ordered canonical collection that may contain links or inline definitions."""

    items: tuple[CanonicalValue, ...]

    def __post_init__(self) -> None:
        """Require immutable canonical values."""
        if type(self.items) is not tuple or any(
            not is_canonical_value(item) for item in self.items
        ):
            raise TypeError("canonical array items must be an immutable tuple of values")


@final
class CanonicalMember(msgspec.Struct, frozen=True, kw_only=True):
    """One deterministic canonical object member."""

    name: str
    value: CanonicalValue

    def __post_init__(self) -> None:
        """Require a static field name and canonical value."""
        if type(self.name) is not str:
            raise TypeError("canonical member name must be text")
        if not is_canonical_value(self.value):
            raise TypeError("canonical member must contain a canonical value")


@final
class CanonicalObject(msgspec.Struct, frozen=True, kw_only=True):
    """A source-ordered, duplicate-free canonical object."""

    members: tuple[CanonicalMember, ...]

    def __post_init__(self) -> None:
        """Require typed members and unique names."""
        if type(self.members) is not tuple or any(
            type(member) is not CanonicalMember for member in self.members
        ):
            raise TypeError("canonical object members must be an immutable typed tuple")
        names = tuple(member.name for member in self.members)
        if len(names) != len(set(names)):
            raise ValueError("canonical object member names must be unique")


@final
class InlineResource(msgspec.Struct, frozen=True, kw_only=True):
    """An anonymous typed resource definition owned by a compiled parent resource."""

    kind: ResourceKind
    origin: InlineDefinitionOrigin
    spec: CanonicalObject

    def __post_init__(self) -> None:
        """Require exact kind, diagnostic origin, and canonical spec."""
        if type(self.kind) is not ResourceKind:
            raise TypeError("inline resource kind must be a ResourceKind")
        if type(self.origin) is not InlineDefinitionOrigin:
            raise TypeError("inline resource origin must be an InlineDefinitionOrigin")
        if type(self.spec) is not CanonicalObject:
            raise TypeError("inline resource spec must be a CanonicalObject")


type CanonicalValue = (
    ExactValue
    | ValueExpression
    | LinkedResource
    | RemoteSchema
    | CanonicalArray
    | CanonicalObject
    | InlineResource
)


def is_canonical_value(value: object) -> TypeGuard[CanonicalValue]:
    """Narrow arbitrary input to the closed compiled configuration value union."""
    return (
        is_exact_value(value)
        or is_value_expression(value)
        or type(value)
        in {
            LinkedResource,
            RemoteSchema,
            CanonicalArray,
            CanonicalObject,
            InlineResource,
        }
    )


@final
class CompiledResource(msgspec.Struct, frozen=True, kw_only=True):
    """One immutable resource revision in the canonical definition graph."""

    definition: DefinitionRef
    metadata: Metadata
    spec: CanonicalValue
    location: SourceLocation

    def __post_init__(self) -> None:
        """Require coherent identity, metadata, canonical content, and safe location."""
        if type(self.definition) is not DefinitionRef:
            raise TypeError("compiled resource definition must be a DefinitionRef")
        if type(self.metadata) is not Metadata:
            raise TypeError("compiled resource metadata must be Metadata")
        if not is_canonical_value(self.spec):
            raise TypeError("compiled resource spec must be canonical")
        if type(self.location) is not SourceLocation:
            raise TypeError("compiled resource location must be a SourceLocation")


@final
class CompiledProject(msgspec.Struct, frozen=True, kw_only=True):
    """A complete coherent project graph ready for deterministic planning."""

    api_version: str
    namespace: NamespaceId
    project: CompiledResource
    resources: tuple[CompiledResource, ...]

    def __post_init__(self) -> None:
        """Enforce one Project, unique identities, and deterministic resource ordering."""
        if self.api_version != "comparo/v1alpha1":
            raise ValueError("compiled project API version must be comparo/v1alpha1")
        if type(self.namespace) is not NamespaceId:
            raise TypeError("compiled project namespace must be a NamespaceId")
        if type(self.project) is not CompiledResource:
            raise TypeError("compiled project manifest must be a CompiledResource")
        if self.project.definition.identity.kind is not ResourceKind.PROJECT:
            raise ValueError("compiled project manifest must have Project kind")
        if type(self.resources) is not tuple or any(
            type(resource) is not CompiledResource for resource in self.resources
        ):
            raise TypeError("compiled project resources must be an immutable typed tuple")
        identities = tuple(resource.definition.identity for resource in self.resources)
        if any(identity.kind is ResourceKind.PROJECT for identity in identities):
            raise ValueError("compiled project resources cannot contain another Project")
        if len(identities) != len(set(identities)):
            raise ValueError("compiled project resource identities must be unique")
        ordering = tuple(
            (identity.kind.value, identity.resource_id.value) for identity in identities
        )
        if ordering != tuple(sorted(ordering)):
            raise ValueError("compiled project resources must use canonical identity order")

    def find(self, kind: ResourceKind, resource_id: ResourceId) -> CompiledResource | None:
        """Find one exact resource without accepting unqualified cross-kind lookup."""
        if type(kind) is not ResourceKind or type(resource_id) is not ResourceId:
            raise TypeError("compiled project lookup requires typed kind and resource ID")
        return next(
            (
                resource
                for resource in self.resources
                if resource.definition.identity.kind is kind
                and resource.definition.identity.resource_id == resource_id
            ),
            None,
        )


@final
class CompilationResult(msgspec.Struct, frozen=True, kw_only=True):
    """A fail-closed compiled project result with aggregated safe diagnostics."""

    project: CompiledProject | None = None
    diagnostics: tuple[ConfigurationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require typed immutable outcomes and never return a project with errors."""
        if self.project is not None and type(self.project) is not CompiledProject:
            raise TypeError("compilation project must be a CompiledProject")
        if type(self.diagnostics) is not tuple or any(
            type(diagnostic) is not ConfigurationDiagnostic for diagnostic in self.diagnostics
        ):
            raise TypeError("compilation diagnostics must be an immutable typed tuple")
        if self.project is not None and self.diagnostics:
            raise ValueError("compilation cannot return a project alongside diagnostics")
