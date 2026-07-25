"""Secret-free inspection products for compiled, planned, and failed operations."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import FieldPathSegment
from comparo.core.domain.diagnostics import IndexPathSegment
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.planning import PlanSlice
from comparo.core.domain.planning import PlanSliceCardinality
from comparo.core.domain.planning import RunPlanCardinality
from comparo.core.domain.primitives import Count
from comparo.core.domain.provenance import SourceLocation
from comparo.core.planning.models import PreparedRun


@final
class SourceCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free source coordinate suitable for every frontend."""

    source: str
    document: int
    line: int
    column: int


@final
class ResourceCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """Stable resource identity plus exact compiled-definition fingerprint."""

    namespace: str
    kind: str
    resource_id: str
    fingerprint: str


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


@final
class PlanCardinalityInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Exact admitted work counts without eagerly expanding cells."""

    cells: int
    trials: int
    environment_executions: int
    comparisons: int
    maximum_attempts: int


@final
class MatrixAxisInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One compact Matrix axis and its stable case identities."""

    binding: str
    matrix: ResourceCoordinate
    cases: tuple[str, ...]


@final
class EnvironmentPairInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One directed baseline-to-candidate comparison."""

    baseline: ResourceCoordinate
    candidate: ResourceCoordinate


@final
class PlanSliceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One executable subject's compact, value-unresolved recipe."""

    subject: ResourceCoordinate
    matrices: tuple[MatrixAxisInspection, ...]
    trials: int
    environments: tuple[ResourceCoordinate, ...]
    comparisons: tuple[EnvironmentPairInspection, ...]
    maximum_attempts_per_execution: int
    cardinality: PlanCardinalityInspection


@final
class BudgetInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Requested, effective, and operator-capped planning budget."""

    resource: str
    requested: int
    effective: int
    cap: int


@final
class ProvenanceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Ordered value-free origins for one effective planning field."""

    field: str
    sources: tuple[str, ...]


@final
class PlanInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Complete secret-free preview shared by human and machine renderers."""

    api_version: str
    run_id: str
    invocation_id: str
    fingerprint: str
    ordering: str
    policy: ResourceCoordinate
    cardinality: PlanCardinalityInspection
    slices: tuple[PlanSliceInspection, ...]
    budgets: tuple[BudgetInspection, ...]
    provenance: tuple[ProvenanceInspection, ...]


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


def inspect_plan(prepared: PreparedRun) -> PlanInspection:
    """Project an admitted run to a deterministic, unresolved inspection document."""
    if type(prepared) is not PreparedRun:
        raise TypeError("plan inspection requires PreparedRun")
    plan = prepared.plan
    return PlanInspection(
        api_version="comparo.plan/v1alpha1",
        run_id=str(plan.run_id.value),
        invocation_id=str(prepared.invocation.invocation_id.value),
        fingerprint=plan.fingerprint.digest.hex_value,
        ordering=plan.ordering.value,
        policy=_definition(prepared.authority.policy),
        cardinality=_cardinality(plan.cardinality),
        slices=tuple(_slice(item) for item in plan.slices),
        budgets=tuple(
            BudgetInspection(
                resource=item.resource.value,
                requested=cast("Count", item.requested).value,
                effective=cast("Count", item.effective).value,
                cap=cast("Count", item.cap).value,
            )
            for item in plan.budgets
        ),
        provenance=tuple(
            ProvenanceInspection(
                field=item.field,
                sources=tuple(source.kind.value for source in item.sources),
            )
            for item in prepared.effective.provenance
        ),
    )


def _slice(value: PlanSlice) -> PlanSliceInspection:
    return PlanSliceInspection(
        subject=_definition(value.cells.subject),
        matrices=tuple(
            MatrixAxisInspection(
                binding=axis.binding.value,
                matrix=_definition(axis.matrix),
                cases=tuple(case.value for case in axis.cases),
            )
            for axis in value.cells.matrix.axes
        ),
        trials=value.trial_count.value,
        environments=tuple(_definition(item) for item in value.environments),
        comparisons=tuple(
            EnvironmentPairInspection(
                baseline=_definition(item.baseline),
                candidate=_definition(item.candidate),
            )
            for item in value.comparisons
        ),
        maximum_attempts_per_execution=value.maximum_attempts_per_execution.value,
        cardinality=_cardinality(value.cardinality),
    )


def _definition(value: DefinitionRef) -> ResourceCoordinate:
    identity = value.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=value.fingerprint.digest.hex_value,
    )


def _cardinality(
    value: PlanSliceCardinality | RunPlanCardinality,
) -> PlanCardinalityInspection:
    return PlanCardinalityInspection(
        cells=value.cells.value,
        trials=value.trials.value,
        environment_executions=value.environment_executions.value,
        comparisons=value.comparisons.value,
        maximum_attempts=value.maximum_attempts.value,
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
    "MatrixAxisInspection",
    "PlanCardinalityInspection",
    "PlanInspection",
    "PlanSliceInspection",
    "ProvenanceInspection",
    "ResourceCoordinate",
    "SourceCoordinate",
    "inspect_configuration_diagnostic",
    "inspect_diagnostic",
    "inspect_plan",
    "inspect_policy_diagnostic",
)
