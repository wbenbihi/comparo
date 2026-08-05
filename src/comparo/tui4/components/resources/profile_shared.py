"""Reusable presentation primitives for profile-like resource documents."""

from collections.abc import Mapping
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceDetail
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource_presenters import ResourceDependency
from comparo.tui4.models.resource_presenters import ResourceSetting
from comparo.tui4.models.resource_presenters import compiled_exact_fields

from .shared import ResourceSettingList

type ContextFact = tuple[str, BadgeTone]


class ResourceContextStrip(Horizontal):
    """Compact kind-owned facts using the shared semantic badge system."""

    def __init__(self, *facts: ContextFact) -> None:
        """Build a bounded context row from already projected facts."""
        super().__init__(
            *(Badge(label, tone=tone, classes="resource-context-badge") for label, tone in facts),
            classes="resource-context-strip",
        )


class ResourceSettingsBody(Vertical):
    """One continuous semantic settings section with an explicit empty state."""

    def __init__(
        self,
        settings: tuple[ResourceSetting, ...],
        *,
        empty: str,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField] | None = None,
    ) -> None:
        """Initialize from value-safe presenter products only."""
        super().__init__(classes="profile-section-body")
        self._settings = settings
        self._empty = empty
        self._resolved = {} if resolved is None else resolved

    @override
    def compose(self) -> ComposeResult:
        """Render shared setting rows without introducing local parsing."""
        yield ResourceSettingList(
            self._settings,
            empty=self._empty,
            resolved=self._resolved,
        )


def resolved_overview_fields(
    resource: ResourceDetail,
) -> dict[tuple[str, ...], ResolvedResourceField]:
    """Merge safe compiled structure with a masked live overlay only in resolved mode."""
    if resource.overview_mode is not ResourceOverviewMode.RESOLVED:
        return {}
    fields = {field.path: field for field in compiled_exact_fields(resource.entry)}
    inspection = resource.resolved
    if inspection.phase is InspectionPhase.READY and inspection.value is not None:
        fields.update((field.path, field) for field in inspection.value.fields)
    return fields


def resolved_overview_value(
    resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    path: tuple[str, ...],
    fallback: str,
) -> str:
    """Read one already-masked overlay field without interpreting source configuration."""
    field = resolved.get(path)
    return fallback if field is None else field.value


class ResourceUsageBody(Vertical):
    """Inbound consumers; full bidirectional topology remains on the common graph view."""

    def __init__(self, consumers: tuple[ResourceDependency, ...], *, kind: str) -> None:
        """Initialize from safe dependency edges."""
        super().__init__(classes="profile-section-body")
        self._consumers = consumers
        self._kind = kind

    @override
    def compose(self) -> ComposeResult:
        """Render every direct consumer or a truthful isolated state."""
        if not self._consumers:
            yield Hint(f"This {self._kind} has no declared consumers.")
            return
        yield Table(
            columns=(Column("kind", "Kind", width=18), Column("resource", "Consumer")),
            records=tuple(
                Record(f"consumer:{index}", (edge.target_kind, edge.target_id))
                for index, edge in enumerate(self._consumers)
            ),
            classes="resource-usage-table",
        )
        yield Hint("Press g for the complete directional dependency graph.")


__all__ = [
    "ResourceContextStrip",
    "ResourceSettingsBody",
    "ResourceUsageBody",
    "resolved_overview_fields",
    "resolved_overview_value",
]
