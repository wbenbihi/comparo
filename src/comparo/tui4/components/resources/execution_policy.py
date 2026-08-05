"""ExecutionPolicy inspector for operator-owned authority catalogs."""

from typing import ClassVar
from typing import override

from textual.app import ComposeResult

from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.remaining_resource_presenters import execution_policy_overview
from comparo.tui4.models.resource import ResourcePresentation

from .base import BaseKindResourceDetails
from .profile_shared import ResourceContextStrip
from .profile_shared import ResourceSettingsBody


class ExecutionPolicyResourceDetails(BaseKindResourceDetails):
    """External operator authority, never project-controlled configuration."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"ExecutionPolicy"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render an operator policy when an authority catalog supplies one."""
        overview = execution_policy_overview(self.entry)
        yield ResourceContextStrip(
            ("operator-owned", BadgeTone.DANGER),
            ("authority ceiling", BadgeTone.WARNING),
            ("external source", BadgeTone.INFO),
        )
        yield Hint(
            "ExecutionPolicy is loaded outside project discovery. Project resources cannot grant "
            "or expand this authority."
        )
        yield Separator(label="Planning", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.planning,
            empty="No planning authority projection is available.",
        )
        yield Separator(label="HTTP authority", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.http,
            empty="No HTTP authority projection is available.",
        )
        yield Separator(label="Value sources", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.values,
            empty="No external value-source authority projection is available.",
        )
        yield Separator(
            label="Artifact persistence",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.artifacts,
            empty="No artifact authority projection is available.",
        )


__all__ = ["ExecutionPolicyResourceDetails"]
