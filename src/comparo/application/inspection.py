"""Stable secret-free inspection facade for diagnostics and admitted Plans."""

from __future__ import annotations

import re
from typing import final

import msgspec

from comparo.application.plan_inspection import inspect_plan as inspect_plan
from comparo.application.plan_inspection_authority import (
    PlanArtifactAuthorityInspection as PlanArtifactAuthorityInspection,
)
from comparo.application.plan_inspection_authority import (
    PlanHttpAuthorityInspection as PlanHttpAuthorityInspection,
)
from comparo.application.plan_inspection_authority import (
    PlanSecretEgressRuleInspection as PlanSecretEgressRuleInspection,
)
from comparo.application.plan_inspection_authority import (
    PlanSecretEgressSourceInspection as PlanSecretEgressSourceInspection,
)
from comparo.application.plan_inspection_authority import (
    PlanValueAuthorityInspection as PlanValueAuthorityInspection,
)
from comparo.application.plan_inspection_identity import ResourceCoordinate as ResourceCoordinate
from comparo.application.plan_inspection_profiles import PlanGateInspection as PlanGateInspection
from comparo.application.plan_inspection_profiles import (
    PlanGateThresholdInspection as PlanGateThresholdInspection,
)
from comparo.application.plan_inspection_profiles import (
    PlanProfileAttachmentInspection as PlanProfileAttachmentInspection,
)
from comparo.application.plan_inspection_profiles import (
    PlanProfilesInspection as PlanProfilesInspection,
)
from comparo.application.plan_inspection_profiles import (
    PlanValidationInspection as PlanValidationInspection,
)
from comparo.application.plan_inspection_topology import BudgetInspection as BudgetInspection
from comparo.application.plan_inspection_topology import (
    EnvironmentPairInspection as EnvironmentPairInspection,
)
from comparo.application.plan_inspection_topology import (
    InlineSourceCoordinate as InlineSourceCoordinate,
)
from comparo.application.plan_inspection_topology import (
    MatrixAxisInspection as MatrixAxisInspection,
)
from comparo.application.plan_inspection_topology import (
    PlanCardinalityInspection as PlanCardinalityInspection,
)
from comparo.application.plan_inspection_topology import PlanInspection as PlanInspection
from comparo.application.plan_inspection_topology import (
    PlanningSourceInspection as PlanningSourceInspection,
)
from comparo.application.plan_inspection_topology import PlanSliceInspection as PlanSliceInspection
from comparo.application.plan_inspection_topology import (
    ProvenanceInspection as ProvenanceInspection,
)
from comparo.application.plan_sources import SensitiveSourceInspection as SensitiveSourceInspection
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.diagnostics import IndexPathSegment
from comparo.core.domain.provenance import SourceLocation

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@final
class SourceCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free source coordinate suitable for every frontend."""

    source: str
    document: int
    line: int
    column: int

    def __post_init__(self) -> None:
        """Require one logical source ID and one-based exact coordinates."""
        if type(self.source) is not str or not self.source or len(self.source) > 1024:
            raise ValueError("source coordinate requires bounded non-empty source text")
        if any(
            type(item) is not int or item < 1 for item in (self.document, self.line, self.column)
        ):
            raise ValueError("source coordinate positions must be positive exact integers")


@final
class ApplicationDiagnostic(msgspec.Struct, frozen=True, kw_only=True):
    """Normalized value-free issue exposed by application services."""

    code: str
    severity: str
    stage: str
    source: SourceCoordinate | None = None
    resource: tuple[str, str, str] | None = None
    path: tuple[str, ...] = ()
    occurrences: int | None = None

    def __post_init__(self) -> None:
        """Keep frontend diagnostics bounded, typed, and free of arbitrary prose."""
        for value, label in ((self.code, "code"), (self.stage, "stage")):
            if (
                type(value) is not str
                or not value
                or len(value) > 255
                or _SAFE_TOKEN.fullmatch(value) is None
            ):
                raise ValueError(f"application diagnostic {label} must be a bounded token")
        if self.severity not in {"error", "warning", "info"}:
            raise ValueError("application diagnostic severity is unsupported")
        if self.source is not None and type(self.source) is not SourceCoordinate:
            raise TypeError("application diagnostic source must be typed or absent")
        if self.resource is not None and (
            type(self.resource) is not tuple
            or len(self.resource) != 3
            or any(type(item) is not str or not item or len(item) > 255 for item in self.resource)
        ):
            raise ValueError("application diagnostic resource must be a complete coordinate")
        if type(self.path) is not tuple or any(
            type(item) is not str or not item or len(item) > 255 for item in self.path
        ):
            raise ValueError("application diagnostic path must contain bounded text segments")
        if self.occurrences is not None and (
            type(self.occurrences) is not int or self.occurrences < 0
        ):
            raise ValueError("application diagnostic occurrences must be non-negative")


def inspect_configuration_diagnostic(
    diagnostic: ConfigurationDiagnostic,
) -> ApplicationDiagnostic:
    """Normalize one compiler diagnostic without opening its authored source."""
    if type(diagnostic) is not ConfigurationDiagnostic:
        raise TypeError("configuration inspection requires ConfigurationDiagnostic")
    return ApplicationDiagnostic(
        code=diagnostic.code.value,
        severity=diagnostic.severity.value,
        stage="configuration",
        source=_source(diagnostic.primary.start),
        path=tuple(_path_segment(segment) for segment in diagnostic.path),
    )


def inspect_policy_diagnostic(
    diagnostic: ConfigurationDiagnostic,
) -> ApplicationDiagnostic:
    """Normalize one external-policy diagnostic without exposing its authored source."""
    inspected = inspect_configuration_diagnostic(diagnostic)
    return msgspec.structs.replace(inspected, stage="policy")


def inspect_diagnostic(diagnostic: Diagnostic) -> ApplicationDiagnostic:
    """Normalize one domain diagnostic using only its closed safe context."""
    if type(diagnostic) is not Diagnostic:
        raise TypeError("diagnostic inspection requires Diagnostic")
    context = diagnostic.context
    resource = context.resource
    return ApplicationDiagnostic(
        code=diagnostic.code.value,
        severity=diagnostic.severity.value,
        stage=diagnostic.stage.value,
        source=None if context.location is None else _source(context.location),
        resource=(
            None
            if resource is None
            else (
                resource.namespace.value,
                resource.kind.value,
                resource.resource_id.value,
            )
        ),
        path=tuple(_path_segment(segment) for segment in context.field_path),
        occurrences=(None if context.occurrences is None else context.occurrences.value),
    )


def _source(value: SourceLocation) -> SourceCoordinate:
    return SourceCoordinate(
        source=value.source_id.value,
        document=value.document.value,
        line=value.line.value,
        column=value.column.value,
    )


def _path_segment(value: object) -> str:
    if type(value) is FieldPathSegment:
        return value.name
    if type(value) is IndexPathSegment:
        return str(value.index.value)
    raise TypeError("unsupported diagnostic path segment")


__all__ = (
    "ApplicationDiagnostic",
    "BudgetInspection",
    "EnvironmentPairInspection",
    "InlineSourceCoordinate",
    "MatrixAxisInspection",
    "PlanArtifactAuthorityInspection",
    "PlanCardinalityInspection",
    "PlanGateInspection",
    "PlanGateThresholdInspection",
    "PlanHttpAuthorityInspection",
    "PlanInspection",
    "PlanProfileAttachmentInspection",
    "PlanProfilesInspection",
    "PlanSecretEgressRuleInspection",
    "PlanSecretEgressSourceInspection",
    "PlanSliceInspection",
    "PlanValidationInspection",
    "PlanValueAuthorityInspection",
    "PlanningSourceInspection",
    "ProvenanceInspection",
    "ResourceCoordinate",
    "SensitiveSourceInspection",
    "SourceCoordinate",
    "inspect_configuration_diagnostic",
    "inspect_diagnostic",
    "inspect_plan",
    "inspect_policy_diagnostic",
)
