"""Project-specific overview with explicit topology and configuration sections."""

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Grid
from textual.containers import Horizontal
from textual.widgets import Static

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeEmphasis
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.typography import Hint
from comparo.tui4.components.typography import SectionTitle
from comparo.tui4.models.explorer import ProjectEnvironmentFact
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource_presenters import ProjectOverview
from comparo.tui4.models.resource_presenters import compiled_exact_fields
from comparo.tui4.models.resource_presenters import project_overview

from .base import BaseKindResourceDetails
from .shared import ResourceDependencyGraph
from .shared import ResourceDependencyTree
from .shared import ResourceSettingList
from .shared import resolution_status

# Compatibility names for the original Project-specific component surface.
ProjectDependencyGraph = ResourceDependencyGraph
ProjectDependencyTree = ResourceDependencyTree


class ProjectEnvironmentRow(Horizontal):
    """One Environment name paired with its explicit locality classification."""

    def __init__(self, environment: ProjectEnvironmentFact) -> None:
        """Initialize the row from safe catalog facts."""
        locality = environment.locality or "unspecified"
        tone = {
            "local": BadgeTone.SUCCESS,
            "live": BadgeTone.DANGER,
        }.get(locality, BadgeTone.NEUTRAL)
        emphasis = BadgeEmphasis.SOLID if locality == "live" else BadgeEmphasis.QUIET
        super().__init__(
            Static(
                Text(environment.name or environment.resource_id),
                classes="project-environment-name",
            ),
            Badge(locality, tone=tone, emphasis=emphasis),
            classes="project-environment-row",
        )


class ProjectEnvironmentCard(Panel):
    """One side of the Project's default comparison topology."""

    def __init__(
        self,
        title: str,
        environments: tuple[ProjectEnvironmentFact, ...],
        *,
        role: str,
    ) -> None:
        """Initialize a baseline or candidate card."""
        children = (
            tuple(ProjectEnvironmentRow(environment) for environment in environments)
            if environments
            else (Hint("Not configured."),)
        )
        super().__init__(
            *children,
            title=title,
            classes=f"project-environment-card project-{role}-card",
        )


class ProjectResourceDetails(BaseKindResourceDetails):
    """Complete safe Project manifest overview with explicit semantic sections."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Project"})

    @override
    def compose(self) -> ComposeResult:
        """Render topology cards and every available Project configuration field."""
        overview: ProjectOverview = project_overview(self.entry)
        resolved = self._resolved_fields()
        baseline = () if overview.baseline is None else (overview.baseline,)
        candidate_title = "Candidate" if len(overview.candidates) <= 1 else "Candidates"

        yield Separator(label="Default comparison", align="left", classes="tight")
        yield Grid(
            ProjectEnvironmentCard("Baseline", baseline, role="baseline"),
            ProjectEnvironmentCard(
                candidate_title,
                overview.candidates,
                role="candidate",
            ),
            classes="project-environment-cards",
        )
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(self.resource, self._execution_context_label())

        yield Separator(label="Project sources", align="left")
        yield ResourceSettingList(
            overview.sources,
            empty="No data directory or additional sources are declared.",
            resolved=resolved,
        )

        yield Separator(label="Project variables", align="left")
        yield ResourceSettingList(
            overview.variables,
            empty="No project-level variables are declared.",
            resolved=resolved,
        )

        yield Separator(label="Project secrets", align="left")
        yield ResourceSettingList(
            overview.secrets,
            empty="No project-level secrets are declared.",
            resolved=resolved,
        )

        yield Separator(label="Execution defaults", align="left")
        if not overview.default_groups:
            yield Hint("No Project execution defaults are declared.")
        for group in overview.default_groups:
            yield SectionTitle(group.title, classes="project-default-group-title")
            yield ResourceSettingList(
                group.settings,
                empty=f"No {group.title.lower()} defaults are declared.",
                resolved=resolved,
            )

        if overview.additional:
            yield Separator(label="Additional configuration", align="left")
            yield ResourceSettingList(
                overview.additional,
                empty="No additional Project settings are declared.",
                resolved=resolved,
            )

    def _resolved_fields(self) -> dict[tuple[str, ...], ResolvedResourceField]:
        """Index the ready masked overlay without changing authored structure."""
        if self.resource.overview_mode is not ResourceOverviewMode.RESOLVED:
            return {}
        fields = {field.path: field for field in compiled_exact_fields(self.entry)}
        inspection = self.resource.resolved
        if inspection.phase is not InspectionPhase.READY or inspection.value is None:
            return fields
        fields.update((field.path, field) for field in inspection.value.fields)
        return fields

    def _execution_context_label(self) -> str:
        """Return the selected Environment's best available display label."""
        environment = self.resource.execution_environment
        if environment is None:
            return "No execution context"
        defaults = self.entry.project_environments
        if defaults is None:
            return environment.resource_id
        environments = (
            *((defaults.baseline,) if defaults.baseline is not None else ()),
            *defaults.candidates,
        )
        context = next(
            (item for item in environments if item.resource_id == environment.resource_id),
            None,
        )
        return environment.resource_id if context is None else context.name or context.resource_id


__all__ = ["ProjectDependencyGraph", "ProjectResourceDetails"]
