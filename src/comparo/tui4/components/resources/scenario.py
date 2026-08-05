"""Scenario overview for future stateful workflow declarations."""

from collections.abc import Mapping
from typing import ClassVar
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical

from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.remaining_resource_presenters import ScenarioOverview
from comparo.tui4.models.remaining_resource_presenters import scenario_overview
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourcePresentation

from .base import BaseKindResourceDetails
from .profile_shared import ResourceContextStrip
from .profile_shared import ResourceSettingsBody
from .profile_shared import ResourceUsageBody
from .profile_shared import resolved_overview_fields
from .profile_shared import resolved_overview_value
from .shared import resolution_status


class ScenarioStepsBody(Vertical):
    """Ordered step topology followed by complete value-safe step definitions."""

    def __init__(
        self,
        overview: ScenarioOverview,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize from the pure Scenario projection."""
        super().__init__(classes="profile-section-body")
        self._overview = overview
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render ordering and configuration without claiming runtime availability."""
        if not self._overview.steps:
            yield Hint("This Scenario has no inspectable steps.")
            return
        yield Table(
            columns=(
                Column("ordinal", "#", width=4),
                Column("step", "Step"),
                Column("request", "Request"),
                Column("after", "After"),
            ),
            records=tuple(
                Record(
                    f"step:{index}",
                    (
                        str(index + 1),
                        resolved_overview_value(
                            self._resolved,
                            (*step.definition.path, "id"),
                            step.id,
                        ),
                        resolved_overview_value(
                            self._resolved,
                            (*step.definition.path, "request"),
                            step.request,
                        ),
                        resolved_overview_value(
                            self._resolved,
                            (*step.definition.path, "after"),
                            step.after,
                        ),
                    ),
                )
                for index, step in enumerate(self._overview.steps)
            ),
            classes="scenario-steps-table",
        )
        yield ResourceSettingsBody(
            tuple(step.definition for step in self._overview.steps),
            empty="This Scenario has no inspectable step configuration.",
            resolved=self._resolved,
        )


class ScenarioResourceDetails(BaseKindResourceDetails):
    """Stateful workflow declaration, visible before runtime support ships."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Scenario"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render every authored Scenario capability in one continuous document."""
        overview = scenario_overview(self.entry)
        resolved = resolved_overview_fields(self.resource)
        yield ResourceContextStrip(
            ("future capability", BadgeTone.WARNING),
            (f"{len(overview.steps)} steps", BadgeTone.INFO),
            (f"{overview.matrix_count} matrices", BadgeTone.NEUTRAL),
            (f"{len(overview.consumers)} consumers", BadgeTone.NEUTRAL),
        )
        yield Hint(
            "Scenario execution is intentionally unavailable; this view describes the authored "
            "workflow contract only."
        )
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        yield Separator(
            label="Parameters & matrices", align="left", classes="resource-section-heading"
        )
        yield ResourceSettingsBody(
            (*overview.parameters, *overview.matrices),
            empty="No Scenario parameters or Matrix attachments are declared.",
            resolved=resolved,
        )
        yield Separator(label="Steps", align="left", classes="resource-section-heading")
        yield ScenarioStepsBody(overview, resolved)
        yield Separator(
            label="Evaluation & safety",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.evaluation,
            empty="No Scenario-level assertions, diffs, or safety profile is attached.",
            resolved=resolved,
        )
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield ResourceUsageBody(overview.consumers, kind="Scenario")


__all__ = ["ScenarioResourceDetails", "ScenarioStepsBody"]
