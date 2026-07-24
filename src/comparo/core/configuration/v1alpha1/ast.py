"""Version-specific source AST for ``comparo/v1alpha1`` resources."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.configuration.models import is_configuration_node
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.metadata import Metadata


@final
class V1Alpha1Metadata(msgspec.Struct, frozen=True, kw_only=True):
    """Strict authored resource metadata with stable and human identities separated."""

    resource_id: ResourceId
    details: Metadata
    span: SourceSpan

    def __post_init__(self) -> None:
        """Require exact domain metadata and source coordinates."""
        if type(self.resource_id) is not ResourceId:
            raise TypeError("v1alpha1 metadata requires a ResourceId")
        if type(self.details) is not Metadata:
            raise TypeError("v1alpha1 metadata details must be Metadata")
        if self.details.name is None:
            raise ValueError("v1alpha1 metadata name is required")
        if type(self.span) is not SourceSpan:
            raise TypeError("v1alpha1 metadata requires a SourceSpan")


@final
class V1Alpha1Resource(msgspec.Struct, frozen=True, kw_only=True):
    """One validated common envelope with a still source-located versioned spec."""

    kind: ResourceKind
    metadata: V1Alpha1Metadata
    spec: ConfigurationNode
    span: SourceSpan

    def __post_init__(self) -> None:
        """Require exact closed envelope components."""
        if type(self.kind) is not ResourceKind:
            raise TypeError("v1alpha1 resource kind must be a ResourceKind")
        if type(self.metadata) is not V1Alpha1Metadata:
            raise TypeError("v1alpha1 resource metadata must be V1Alpha1Metadata")
        if not is_configuration_node(self.spec):
            raise TypeError("v1alpha1 resource spec must be a configuration node")
        if type(self.span) is not SourceSpan:
            raise TypeError("v1alpha1 resource requires a SourceSpan")


@final
class V1Alpha1FrontendResult(msgspec.Struct, frozen=True, kw_only=True):
    """Every valid resource and every safe envelope diagnostic from a source set."""

    resources: tuple[V1Alpha1Resource, ...] = ()
    diagnostics: tuple[ConfigurationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require immutable typed result collections."""
        if type(self.resources) is not tuple or any(
            type(resource) is not V1Alpha1Resource for resource in self.resources
        ):
            raise TypeError("frontend resources must be a tuple of V1Alpha1Resource values")
        if type(self.diagnostics) is not tuple or any(
            type(diagnostic) is not ConfigurationDiagnostic for diagnostic in self.diagnostics
        ):
            raise TypeError("frontend diagnostics must be a tuple of diagnostics")
