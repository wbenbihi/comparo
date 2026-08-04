"""Immutable state and semantic actions for one selected resource."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.tui4.models.explorer import ResourceEntry

if TYPE_CHECKING:
    from comparo.application import AuthoredResourceWindow
    from comparo.application import EffectiveResourceWindow


class ResourceRepresentation(StrEnum):
    """Distinct resource representations exposed by the application boundary."""

    OVERVIEW = "overview"
    AUTHORED = "authored"
    EFFECTIVE = "effective"
    RESOLVED = "resolved"
    DEPENDENCIES = "dependencies"


class ResourceOverviewMode(StrEnum):
    """Value layer displayed inside the resource Overview representation."""

    RAW = "raw"
    RESOLVED = "resolved"


class InspectionPhase(StrEnum):
    """Lifecycle of one independently loaded resource representation."""

    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ResourceInspection[T]:
    """Typed latest-wins state for one bounded inspection product."""

    phase: InspectionPhase = InspectionPhase.IDLE
    value: T | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        """Prevent contradictory UI states from reaching resource widgets."""
        if self.phase is InspectionPhase.READY and self.value is None:
            raise ValueError("a ready inspection requires a value")
        if self.phase is not InspectionPhase.READY and self.value is not None:
            raise ValueError("only a ready inspection may carry a value")
        if self.phase in {InspectionPhase.FAILED, InspectionPhase.UNAVAILABLE}:
            if not self.message:
                raise ValueError("failed and unavailable inspections require a message")
        elif self.message is not None:
            raise ValueError("only failed and unavailable inspections may carry a message")


@dataclass(frozen=True)
class ResolvedResourceField:
    """One exact field-path value safe to overlay on an authored structure."""

    path: tuple[str, ...]
    value: str
    tone: str | None = None
    reference_kind: str | None = None


@dataclass(frozen=True)
class ResolvedRequestTarget:
    """Canonical sanitized method and URL for one prepared Request occurrence."""

    method: str
    url: str

    def __post_init__(self) -> None:
        """Require bounded printable values already sanitized by the application boundary."""
        if (
            not self.method
            or len(self.method) > 32
            or not self.method.isprintable()
            or self.method != self.method.strip()
        ):
            raise ValueError("resolved Request method must be bounded printable text")
        if (
            not self.url
            or len(self.url) > 8192
            or not self.url.isprintable()
            or self.url != self.url.strip()
        ):
            raise ValueError("resolved Request URL must be bounded printable text")


@dataclass(frozen=True)
class ResolvedMatrixPlan:
    """Compiled Matrix cases for one exact Compare subject and binding."""

    subject: ResourceCoordinate
    binding: str
    cases: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require one bounded non-empty Matrix axis projection."""
        if type(self.subject) is not ResourceCoordinate:
            raise TypeError("resolved Matrix plan subject must be a ResourceCoordinate")
        if self.subject.kind not in {"Request", "Scenario"}:
            raise ValueError("resolved Matrix plan subject must be executable")
        if (
            type(self.binding) is not str
            or not self.binding
            or len(self.binding) > 255
            or not self.binding.isprintable()
        ):
            raise ValueError("resolved Matrix binding must be bounded printable text")
        if (
            type(self.cases) is not tuple
            or not self.cases
            or len(set(self.cases)) != len(self.cases)
            or any(
                type(case) is not str or not case or len(case) > 255 or not case.isprintable()
                for case in self.cases
            )
        ):
            raise ValueError("resolved Matrix cases must be unique bounded printable text")


@dataclass(frozen=True)
class ResolvedMatrixSelection:
    """One exact Matrix binding and case selected by a resolved occurrence."""

    binding: str
    matrix: ResourceCoordinate
    case_id: str

    def __post_init__(self) -> None:
        """Require a complete bounded Matrix assignment."""
        if type(self.matrix) is not ResourceCoordinate or self.matrix.kind != "Matrix":
            raise TypeError("resolved Matrix selection must identify a Matrix resource")
        if any(
            type(value) is not str or not value or len(value) > 255 or not value.isprintable()
            for value in (self.binding, self.case_id)
        ):
            raise ValueError("resolved Matrix selection must contain bounded printable text")


@dataclass(frozen=True)
class ResolvedResourceView:
    """Frontend projection for one context-bound masked value overlay."""

    environment: ResourceCoordinate
    subject: ResourceCoordinate
    execution_ordinal: int
    total_occurrences: int
    cell_id: str
    trial_id: str
    fields: tuple[ResolvedResourceField, ...]
    matrices: tuple[ResolvedMatrixSelection, ...] = ()
    request_target: ResolvedRequestTarget | None = None
    matrix_plan: ResolvedMatrixPlan | None = None
    diagnostics: tuple[str, ...] = ()
    incomplete: bool = False

    def __post_init__(self) -> None:
        """Require exact executable context and coherent optional kind products."""
        if (
            type(self.environment) is not ResourceCoordinate
            or self.environment.kind != "Environment"
        ):
            raise TypeError("resolved resource view requires an Environment coordinate")
        if type(self.subject) is not ResourceCoordinate or self.subject.kind not in {
            "Request",
            "Scenario",
        }:
            raise TypeError("resolved resource view requires an executable subject coordinate")
        if (
            type(self.execution_ordinal) is not int
            or type(self.total_occurrences) is not int
            or self.execution_ordinal < 0
            or self.total_occurrences < 1
            or self.execution_ordinal >= self.total_occurrences
        ):
            raise ValueError("resolved resource view occurrence coordinates are inconsistent")
        if type(self.matrices) is not tuple or any(
            type(item) is not ResolvedMatrixSelection for item in self.matrices
        ):
            raise TypeError("resolved resource Matrix assignments must be immutable and typed")
        bindings = tuple(item.binding for item in self.matrices)
        if len(bindings) != len(set(bindings)):
            raise ValueError("resolved resource Matrix bindings must be unique")
        if self.matrix_plan is not None and type(self.matrix_plan) is not ResolvedMatrixPlan:
            raise TypeError("resolved resource Matrix plan must be typed")
        if self.matrix_plan is not None and self.matrix_plan.subject != self.subject:
            raise ValueError("resolved Matrix plan subject differs from its execution context")


@dataclass(frozen=True)
class EnvironmentHealthCheckResult:
    """Sanitized terminal outcome of an Environment health-check run."""

    run_id: str
    request_count: int
    completion: str
    gate: str
    adverse_facts: int
    healthy: bool

    def __post_init__(self) -> None:
        """Require a bounded, internally coherent value-safe summary."""
        if type(self.run_id) is not str or not self.run_id or len(self.run_id) > 128:
            raise ValueError("health-check run ID must be bounded non-empty text")
        if type(self.request_count) is not int or self.request_count < 1:
            raise ValueError("health-check request count must be positive")
        if type(self.completion) is not str or not self.completion or len(self.completion) > 64:
            raise ValueError("health-check completion must be bounded non-empty text")
        if type(self.gate) is not str or not self.gate or len(self.gate) > 64:
            raise ValueError("health-check gate must be bounded non-empty text")
        if type(self.adverse_facts) is not int or self.adverse_facts < 0:
            raise ValueError("health-check adverse count must be non-negative")
        if type(self.healthy) is not bool:
            raise TypeError("health-check healthy state must be bool")
        expected = (
            self.completion == "complete" and self.gate != "failed" and self.adverse_facts == 0
        )
        if self.healthy != expected:
            raise ValueError("health-check state contradicts its terminal facts")


class ResourceActionId(StrEnum):
    """Semantic resource actions understood by the Explorer coordinator."""

    OVERVIEW = "overview"
    AUTHORED = "authored"
    EFFECTIVE = "effective"
    RESOLVED = "resolved"
    PLAN_MATRIX_CELLS = "plan-matrix-cells"


@dataclass(frozen=True)
class ResourcePresentation:
    """Kind-owned chrome policy consumed by the generic resource host."""

    common_representation_hints: bool = False
    visible_actions: frozenset[ResourceActionId] | None = None


@dataclass(frozen=True)
class ResourceAction:
    """One renderable action and its capability decision."""

    id: ResourceActionId
    label: str
    enabled: bool = True
    disabled_reason: str | None = None

    def __post_init__(self) -> None:
        """Require an explanation whenever an action is disabled."""
        if self.enabled == (self.disabled_reason is not None):
            raise ValueError("disabled resource actions require exactly one reason")


@dataclass(frozen=True)
class ResourceDetail:
    """Immutable state for all independently loaded views of one resource."""

    key: str
    entry: ResourceEntry
    representation: ResourceRepresentation
    overview_mode: ResourceOverviewMode
    execution_environment: ResourceCoordinate | None
    authored: ResourceInspection[AuthoredResourceWindow]
    effective: ResourceInspection[EffectiveResourceWindow]
    resolved: ResourceInspection[ResolvedResourceView]
    health: ResourceInspection[EnvironmentHealthCheckResult]
    actions: tuple[ResourceAction, ...]


def resolution_environment(detail: ResourceDetail) -> ResourceCoordinate | None:
    """Return the Environment that owns resolution for this resource."""
    if detail.entry.summary.coordinate.kind == "Environment":
        return detail.entry.summary.coordinate
    return detail.execution_environment


def _resource_actions(entry: ResourceEntry) -> tuple[ResourceAction, ...]:
    """Build stable actions without pretending unavailable context exists."""
    kind = entry.summary.coordinate.kind
    actions = [
        ResourceAction(ResourceActionId.OVERVIEW, "Overview"),
        ResourceAction(ResourceActionId.AUTHORED, "Authored"),
        ResourceAction(ResourceActionId.EFFECTIVE, "Effective"),
        ResourceAction(ResourceActionId.RESOLVED, "Resolved"),
    ]
    if kind == "Matrix":
        actions.append(
            ResourceAction(
                ResourceActionId.PLAN_MATRIX_CELLS,
                "Plan cells",
                enabled=False,
                disabled_reason="Matrix cells depend on the complete Compare scope.",
            )
        )
    return tuple(actions)


def initial_detail(
    entry: ResourceEntry,
    execution_environment: ResourceCoordinate | None = None,
) -> ResourceDetail:
    """Build the selected resource's immutable detail projection."""
    return ResourceDetail(
        key=entry.key,
        entry=entry,
        representation=ResourceRepresentation.OVERVIEW,
        overview_mode=ResourceOverviewMode.RAW,
        execution_environment=execution_environment,
        authored=ResourceInspection(),
        effective=ResourceInspection(),
        resolved=ResourceInspection(
            phase=InspectionPhase.UNAVAILABLE,
            message=(
                "No execution Environment is available."
                if execution_environment is None and entry.summary.coordinate.kind != "Environment"
                else "Masked resolved values have not been inspected for this context."
            ),
        ),
        health=ResourceInspection(),
        actions=_resource_actions(entry),
    )


def bind_execution_environment(
    detail: ResourceDetail,
    environment: ResourceCoordinate | None,
) -> ResourceDetail:
    """Rebind contextual inspection state without retaining stale resolved values."""
    if detail.execution_environment == environment:
        return detail
    previous_resolution_environment = resolution_environment(detail)
    rebound = replace(detail, execution_environment=environment)
    if previous_resolution_environment == resolution_environment(rebound):
        return rebound
    return replace(
        rebound,
        resolved=ResourceInspection(
            phase=InspectionPhase.UNAVAILABLE,
            message=(
                "No execution Environment is available."
                if environment is None
                else "Masked resolved values have not been inspected for this context."
            ),
        ),
    )


def select_representation(
    detail: ResourceDetail, representation: ResourceRepresentation
) -> ResourceDetail:
    """Return resource state focused on one representation."""
    return replace(detail, representation=representation)


def toggle_overview_mode(detail: ResourceDetail) -> ResourceDetail:
    """Toggle raw/resolved Overview content and return to Overview."""
    mode = (
        ResourceOverviewMode.RESOLVED
        if detail.overview_mode is ResourceOverviewMode.RAW
        else ResourceOverviewMode.RAW
    )
    return replace(
        detail,
        representation=ResourceRepresentation.OVERVIEW,
        overview_mode=mode,
    )


def loading_authored(detail: ResourceDetail) -> ResourceDetail:
    """Mark the authored representation as loading and select it."""
    return replace(
        detail,
        representation=ResourceRepresentation.AUTHORED,
        authored=ResourceInspection(phase=InspectionPhase.LOADING),
    )


def loaded_authored(detail: ResourceDetail, value: AuthoredResourceWindow) -> ResourceDetail:
    """Attach a bounded masked authored resource window."""
    return replace(
        detail,
        authored=ResourceInspection(phase=InspectionPhase.READY, value=value),
    )


def failed_authored(detail: ResourceDetail, message: str) -> ResourceDetail:
    """Attach a safe authored-inspection failure code."""
    return replace(
        detail,
        authored=ResourceInspection(phase=InspectionPhase.FAILED, message=message),
    )


def loading_resolved(detail: ResourceDetail) -> ResourceDetail:
    """Select resolved Overview mode and start a context-bound inspection."""
    return replace(
        detail,
        representation=ResourceRepresentation.OVERVIEW,
        overview_mode=ResourceOverviewMode.RESOLVED,
        resolved=ResourceInspection(phase=InspectionPhase.LOADING),
    )


def loaded_resolved(detail: ResourceDetail, value: ResolvedResourceView) -> ResourceDetail:
    """Attach one masked resolved projection for the current execution context."""
    if resolution_environment(detail) != value.environment:
        raise ValueError("resolved view Environment differs from resource context")
    return replace(
        detail,
        resolved=ResourceInspection(phase=InspectionPhase.READY, value=value),
    )


def failed_resolved(detail: ResourceDetail, message: str) -> ResourceDetail:
    """Attach a value-free resolved inspection failure code."""
    return replace(
        detail,
        resolved=ResourceInspection(phase=InspectionPhase.FAILED, message=message),
    )


def unavailable_resolved(detail: ResourceDetail, message: str) -> ResourceDetail:
    """Select resolved Overview mode with an honest unavailable explanation."""
    return replace(
        detail,
        representation=ResourceRepresentation.OVERVIEW,
        overview_mode=ResourceOverviewMode.RESOLVED,
        resolved=ResourceInspection(phase=InspectionPhase.UNAVAILABLE, message=message),
    )


def loading_effective(detail: ResourceDetail) -> ResourceDetail:
    """Mark the effective representation as loading and select it."""
    return replace(
        detail,
        representation=ResourceRepresentation.EFFECTIVE,
        effective=ResourceInspection(phase=InspectionPhase.LOADING),
    )


def loaded_effective(detail: ResourceDetail, value: EffectiveResourceWindow) -> ResourceDetail:
    """Attach a bounded value-safe effective resource window."""
    return replace(
        detail,
        effective=ResourceInspection(phase=InspectionPhase.READY, value=value),
    )


def failed_effective(detail: ResourceDetail, message: str) -> ResourceDetail:
    """Attach a safe effective-inspection failure code."""
    return replace(
        detail,
        effective=ResourceInspection(phase=InspectionPhase.FAILED, message=message),
    )


def with_health_check(
    detail: ResourceDetail,
    inspection: ResourceInspection[EnvironmentHealthCheckResult],
) -> ResourceDetail:
    """Attach workspace-owned health state to a selected Environment detail."""
    return replace(detail, health=inspection)
