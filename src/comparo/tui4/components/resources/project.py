"""Project-specific overview with explicit topology and configuration sections."""

from collections.abc import Mapping
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
from comparo.tui4.components.treemenu import TreeMenu
from comparo.tui4.components.typography import Caption
from comparo.tui4.components.typography import Hint
from comparo.tui4.components.typography import SectionTitle
from comparo.tui4.models.explorer import ProjectEnvironmentFact
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.navigation import MenuNode
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource_presenters import ProjectDependency
from comparo.tui4.models.resource_presenters import ProjectOverview
from comparo.tui4.models.resource_presenters import ProjectSetting
from comparo.tui4.models.resource_presenters import project_compiled_exact_fields
from comparo.tui4.models.resource_presenters import project_dependencies
from comparo.tui4.models.resource_presenters import project_overview
from comparo.tui4.theme import kind_glyph
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

    def __init__(
        self,
        setting: ProjectSetting,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize a row from a pure Project setting projection."""
        super().__init__(classes="project-setting-row")
        self._setting = setting
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render the setting summary followed by every nested field."""
        value = self._value(self._setting.path, self._setting.value)
        heading = Text(self._setting.name, style("label"))
        heading.append("  ")
        heading.append_text(value)
        heading.append(" ")
        heading.append_text(badge_text(self._setting.category, tone=BadgeTone.NEUTRAL))
        yield Static(heading, classes="project-setting-heading")
        for detail in self._setting.details:
            absolute_path = (*self._setting.path, *detail.path)
            value = self._value(absolute_path, detail.value)
            path = ".".join(detail.path) or "value"
            line = Text("  ↳ ", style("dim"))
            line.append(path, style("muted"))
            line.append("  ")
            line.append_text(value)
            line.append(" ")
            line.append_text(badge_text(detail.category, tone=BadgeTone.NEUTRAL))
            yield Static(line, classes="project-setting-detail")

    def _value(
        self,
        path: tuple[str, ...],
        fallback: str,
    ) -> Text:
        """Return an exact resolved value or a failed ancestor outcome."""
        selected = self._resolved.get(path)
        if selected is not None:
            return self._styled_value(
                selected.value,
                selected.tone,
                selected.reference_kind,
            )
        for length in range(len(path) - 1, 0, -1):
            ancestor = self._resolved.get(path[:length])
            if ancestor is not None and ancestor.value.startswith("<"):
                return self._styled_value(ancestor.value, ancestor.tone, None)
        return self._styled_value(fallback, None, None)

    @staticmethod
    def _styled_value(value: str, tone: str | None, reference_kind: str | None) -> Text:
        """Render one value with semantic type color and an optional target-kind glyph."""
        result = Text()
        if reference_kind is not None:
            character, token = kind_glyph(reference_kind)
            result.append(f"{character} ", style(token))
        result.append(value, style(tone or "fg"))
        return result


class ProjectSettingList(Vertical):
    """Explicit Project settings list with a semantic empty state."""

    def __init__(
        self,
        settings: tuple[ProjectSetting, ...],
        *,
        empty: str,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize the list without coupling it to a particular section."""
        super().__init__(classes="project-setting-list")
        self._settings = settings
        self._empty = empty
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render every projected setting or explain the empty section."""
        if not self._settings:
            yield Hint(self._empty)
            return
        for setting in self._settings:
            yield ProjectSettingRow(setting, self._resolved)


class ProjectDependencyTree(TreeMenu[str]):
    """One-hop Project dependency graph over the generic tree mechanics."""

    def __init__(self, dependencies: tuple[ProjectDependency, ...]) -> None:
        """Build directional graph branches from immutable dependency edges."""
        self._dependencies = {
            f"dependency:{index}": dependency for index, dependency in enumerate(dependencies)
        }
        branches: list[MenuNode[str]] = []
        for direction, title in (("uses", "Uses"), ("used-by", "Used by")):
            leaves = tuple(
                MenuNode(edge_id, dependency.target_id)
                for edge_id, dependency in self._dependencies.items()
                if dependency.direction == direction
            )
            if leaves:
                branches.append(MenuNode(f"direction:{direction}", title, children=leaves))
        super().__init__(*branches, classes="project-dependency-graph")

    @override
    def format_node(self, node: MenuNode[str]) -> Text:
        """Render graph branches and edges with kind, path, and origin."""
        dependency = self._dependencies.get(node.id)
        if dependency is None:
            return Text.assemble(
                (node.prompt.plain, style("label")),
                (f" {len(node.children)}", style("dim")),
            )
        character, token = kind_glyph(dependency.target_kind)
        label = Text(f"{character} ", style(token))
        label.append(f"{dependency.target_kind}/{dependency.target_id}", style("fg"))
        if dependency.path:
            label.append(f"  {'.'.join(dependency.path)}", style("muted"))
        label.append(" ")
        label.append_text(badge_text(dependency.origin, tone=BadgeTone.NEUTRAL))
        return label


class ProjectDependencyGraph(Vertical):
    """Project dependency representation with an explicit empty state."""

    def __init__(self, entry: ResourceEntry) -> None:
        """Initialize the graph from safe relationship projections."""
        super().__init__(classes="project-dependency-view")
        self._dependencies = project_dependencies(entry)

    @override
    def compose(self) -> ComposeResult:
        """Render the graph or explain that the Project is isolated."""
        if not self._dependencies:
            yield Hint("This Project has no dependency edges.")
            return
        yield ProjectDependencyTree(self._dependencies)


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
            yield self._resolution_status()

        yield Separator(label="Project sources", align="left")
        yield ProjectSettingList(
            overview.sources,
            empty="No data directory or additional sources are declared.",
            resolved=resolved,
        )

        yield Separator(label="Project variables", align="left")
        yield ProjectSettingList(
            overview.variables,
            empty="No project-level variables are declared.",
            resolved=resolved,
        )

        yield Separator(label="Project secrets", align="left")
        yield ProjectSettingList(
            overview.secrets,
            empty="No project-level secrets are declared.",
            resolved=resolved,
        )

        yield Separator(label="Execution defaults", align="left")
        if not overview.default_groups:
            yield Hint("No Project execution defaults are declared.")
        for group in overview.default_groups:
            yield SectionTitle(group.title, classes="project-default-group-title")
            yield ProjectSettingList(
                group.settings,
                empty=f"No {group.title.lower()} defaults are declared.",
                resolved=resolved,
            )

        if overview.additional:
            yield Separator(label="Additional configuration", align="left")
            yield ProjectSettingList(
                overview.additional,
                empty="No additional Project settings are declared.",
                resolved=resolved,
            )

    def _resolved_fields(self) -> dict[tuple[str, ...], ResolvedResourceField]:
        """Index the ready masked overlay without changing authored structure."""
        if self.resource.overview_mode is not ResourceOverviewMode.RESOLVED:
            return {}
        fields = {field.path: field for field in project_compiled_exact_fields(self.entry)}
        inspection = self.resource.resolved
        if inspection.phase is not InspectionPhase.READY or inspection.value is None:
            return fields
        fields.update((field.path, field) for field in inspection.value.fields)
        return fields

    def _resolution_status(self) -> Static:
        """Summarize context and lifecycle while preserving the raw page layout."""
        inspection = self.resource.resolved
        context = self._execution_context_label()
        if inspection.phase is InspectionPhase.LOADING:
            return Hint(f"Resolving masked values for {context}…")
        if inspection.phase is InspectionPhase.FAILED:
            return Hint(f"Resolved inspection failed for {context}: {inspection.message}")
        if inspection.phase is not InspectionPhase.READY or inspection.value is None:
            return Hint(inspection.message or f"Resolved values are unavailable for {context}.")
        resolved = inspection.value
        cell = f"{resolved.cell_id[:8]}…"
        trial = f"{resolved.trial_id[:8]}…"
        suffixes: list[str] = []
        if resolved.diagnostics:
            suffixes.append(f"{len(resolved.diagnostics)} unavailable")
        if resolved.incomplete:
            suffixes.append("first page")
        suffix = "" if not suffixes else f" · {' · '.join(suffixes)}"
        return Caption(
            f"Resolved for {context} · execution {resolved.execution_ordinal} · "
            f"cell {cell} · trial {trial} · masked{suffix}"
        )

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
