"""Project-specific overview with explicit topology and configuration sections."""

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Grid
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widgets import Static

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeEmphasis
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.badge import badge_text
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.typography import Hint
from comparo.tui4.components.typography import SectionTitle
from comparo.tui4.models.explorer import ProjectEnvironmentFact
from comparo.tui4.models.resource_presenters import ProjectOverview
from comparo.tui4.models.resource_presenters import ProjectSetting
from comparo.tui4.models.resource_presenters import project_overview
from comparo.tui4.theme import style

from .base import BaseKindResourceDetails


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


class ProjectSettingRow(Vertical):
    """One complete Project setting and all of its nested safe fields."""

    def __init__(self, setting: ProjectSetting) -> None:
        """Initialize a row from a pure Project setting projection."""
        super().__init__(classes="project-setting-row")
        self._setting = setting

    @override
    def compose(self) -> ComposeResult:
        """Render the setting summary followed by every nested field."""
        heading = Text(self._setting.name, style("label"))
        heading.append("  ")
        heading.append(self._setting.value, style("fg"))
        heading.append(" ")
        heading.append_text(badge_text(self._setting.category, tone=BadgeTone.NEUTRAL))
        yield Static(heading, classes="project-setting-heading")
        for detail in self._setting.details:
            path = ".".join(detail.path) or "value"
            line = Text("  ↳ ", style("dim"))
            line.append(path, style("muted"))
            line.append("  ")
            line.append(detail.value, style("fg"))
            line.append(" ")
            line.append_text(badge_text(detail.category, tone=BadgeTone.NEUTRAL))
            yield Static(line, classes="project-setting-detail")


class ProjectSettingList(Vertical):
    """Explicit Project settings list with a semantic empty state."""

    def __init__(self, settings: tuple[ProjectSetting, ...], *, empty: str) -> None:
        """Initialize the list without coupling it to a particular section."""
        super().__init__(classes="project-setting-list")
        self._settings = settings
        self._empty = empty

    @override
    def compose(self) -> ComposeResult:
        """Render every projected setting or explain the empty section."""
        if not self._settings:
            yield Hint(self._empty)
            return
        for setting in self._settings:
            yield ProjectSettingRow(setting)


class ProjectResourceDetails(BaseKindResourceDetails):
    """Complete safe Project manifest overview with explicit semantic sections."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Project"})

    @override
    def compose(self) -> ComposeResult:
        """Render topology cards and every available Project configuration field."""
        overview: ProjectOverview = project_overview(self.entry)
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

        yield Separator(label="Project sources", align="left")
        yield ProjectSettingList(
            overview.sources,
            empty="No data directory or additional sources are declared.",
        )

        yield Separator(label="Project variables", align="left")
        yield ProjectSettingList(
            overview.variables,
            empty="No project-level variables are declared.",
        )

        yield Separator(label="Project secrets", align="left")
        yield ProjectSettingList(
            overview.secrets,
            empty="No project-level secrets are declared.",
        )

        yield Separator(label="Execution defaults", align="left")
        if not overview.default_groups:
            yield Hint("No Project execution defaults are declared.")
        for group in overview.default_groups:
            yield SectionTitle(group.title, classes="project-default-group-title")
            yield ProjectSettingList(
                group.settings,
                empty=f"No {group.title.lower()} defaults are declared.",
            )

        if overview.additional:
            yield Separator(label="Additional configuration", align="left")
            yield ProjectSettingList(
                overview.additional,
                empty="No additional Project settings are declared.",
            )


__all__ = ["ProjectResourceDetails"]
