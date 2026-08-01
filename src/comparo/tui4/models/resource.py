"""Immutable state and semantic actions for one selected resource."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING

from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.fields import Field

if TYPE_CHECKING:
    from comparo.application import AuthoredResourceWindow
    from comparo.application import EffectiveResourceWindow


class ResourceRepresentation(StrEnum):
    """Distinct resource representations exposed by the application boundary."""

    OVERVIEW = "overview"
    AUTHORED = "authored"
    EFFECTIVE = "effective"
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
class ResolvedResourceView:
    """Frontend projection reserved for a context-bound resolved value tree."""

    fields: tuple[Field, ...]


class ResourceActionId(StrEnum):
    """Semantic resource actions understood by the Explorer coordinator."""

    OVERVIEW = "overview"
    AUTHORED = "authored"
    EFFECTIVE = "effective"
    RESOLVED = "resolved"
    USE_IN_COMPARE = "use-in-compare"
    INSPECT_COMPOSITION = "inspect-composition"
    PLAN_MATRIX_CELLS = "plan-matrix-cells"


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
    authored: ResourceInspection[AuthoredResourceWindow]
    effective: ResourceInspection[EffectiveResourceWindow]
    resolved: ResourceInspection[ResolvedResourceView]
    actions: tuple[ResourceAction, ...]


def _resource_actions(entry: ResourceEntry) -> tuple[ResourceAction, ...]:
    """Build stable actions without pretending unavailable context exists."""
    kind = entry.summary.coordinate.kind
    actions = [
        ResourceAction(ResourceActionId.OVERVIEW, "Overview"),
        ResourceAction(ResourceActionId.AUTHORED, "Authored"),
        ResourceAction(ResourceActionId.EFFECTIVE, "Effective"),
        ResourceAction(ResourceActionId.RESOLVED, "Resolved"),
    ]
    if kind == "Request":
        actions.extend(
            (
                ResourceAction(ResourceActionId.USE_IN_COMPARE, "Use in Compare"),
                ResourceAction(
                    ResourceActionId.INSPECT_COMPOSITION,
                    "Composition",
                    enabled=False,
                    disabled_reason="Prepare this request in Compare first.",
                ),
            )
        )
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


def initial_detail(entry: ResourceEntry) -> ResourceDetail:
    """Build the selected resource's immutable detail projection."""
    return ResourceDetail(
        key=entry.key,
        entry=entry,
        representation=ResourceRepresentation.OVERVIEW,
        authored=ResourceInspection(),
        effective=ResourceInspection(),
        resolved=ResourceInspection(
            phase=InspectionPhase.UNAVAILABLE,
            message="Choose an execution context in Compare first.",
        ),
        actions=_resource_actions(entry),
    )


def select_representation(
    detail: ResourceDetail, representation: ResourceRepresentation
) -> ResourceDetail:
    """Return resource state focused on one representation."""
    return replace(detail, representation=representation)


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
