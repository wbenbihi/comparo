"""Environment overview with explicit connection, input, and policy sections."""

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widgets import Static

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeEmphasis
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.badge import badge_text
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourcePresentation
from comparo.tui4.models.resource_presenters import EnvironmentOverview
from comparo.tui4.models.resource_presenters import compiled_exact_fields
from comparo.tui4.models.resource_presenters import environment_overview
from comparo.tui4.theme import kind_glyph
from comparo.tui4.theme import style

from .base import BaseKindResourceDetails
from .shared import ResourceSettingList
from .shared import resolution_status


class EnvironmentContextStrip(Horizontal):
    """Execution role, locality, and active-context state for one Environment."""

    def __init__(self, resource: BaseKindResourceDetails) -> None:
        """Build the strip from catalog facts and the shared execution context."""
        entry = resource.entry
        coordinate = entry.summary.coordinate
        active = resource.resource.execution_environment == coordinate
        children: list[Static] = []
        if active:
            children.append(
                Badge(
                    "exec",
                    tone=BadgeTone.ACTIVE,
                    emphasis=BadgeEmphasis.SOLID,
                    classes="environment-context-badge",
                )
            )
        role = entry.facts.role
        if role is not None:
            children.append(
                Badge(
                    role,
                    tone=(BadgeTone.BASELINE if role == "baseline" else BadgeTone.CANDIDATE),
                    classes="environment-context-badge",
                )
            )
        locality = entry.facts.locality or "unspecified"
        children.append(
            Badge(
                locality,
                tone=(
                    BadgeTone.SUCCESS
                    if locality == "local"
                    else BadgeTone.DANGER
                    if locality == "live"
                    else BadgeTone.NEUTRAL
                ),
                emphasis=(BadgeEmphasis.SOLID if locality == "live" else BadgeEmphasis.QUIET),
                classes="environment-context-badge",
            )
        )
        if not active:
            children.append(
                Hint("Space  Set execution context", classes="environment-context-hint")
            )
        super().__init__(*children, classes="environment-context-strip")


class EnvironmentHealthStatus(Vertical):
    """Declared probes and the latest sanitized health-check result."""

    def __init__(self, resource: BaseKindResourceDetails, overview: EnvironmentOverview) -> None:
        """Initialize from references and independently managed run state."""
        super().__init__(classes="environment-health-status")
        self._resource = resource.resource
        self._checks = overview.health_checks

    @override
    def compose(self) -> ComposeResult:
        """Render probe identities, action hint, and latest terminal outcome."""
        if not self._checks:
            yield Hint("No health checks are declared.")
            return
        resolved = self._resource.overview_mode is ResourceOverviewMode.RESOLVED
        for check in self._checks:
            line = Text("h  ", style("label"))
            if resolved:
                character, token = kind_glyph(check.kind)
                line.append(f"{character} ", style(token))
                line.append(f"{check.kind}/{check.resource_id}", style("value.reference"))
            else:
                line.append(f"$use {check.kind}/{check.resource_id}", style("fg"))
            yield Static(line, classes="environment-health-check")

        inspection = self._resource.health
        if inspection.phase is InspectionPhase.LOADING:
            yield Static(
                badge_text(
                    "checking",
                    tone=BadgeTone.INFO,
                    emphasis=BadgeEmphasis.STATUS,
                ),
                classes="environment-health-result",
            )
            return
        if inspection.phase is InspectionPhase.FAILED:
            yield Static(
                Text.assemble(
                    badge_text(
                        "error",
                        tone=BadgeTone.DANGER,
                        emphasis=BadgeEmphasis.STATUS,
                    ),
                    (f"  {inspection.message}", style("muted")),
                ),
                classes="environment-health-result",
            )
            return
        result = inspection.value
        if inspection.phase is not InspectionPhase.READY or result is None:
            yield Hint("Press h to run the declared health check.")
            return
        tone = BadgeTone.SUCCESS if result.healthy else BadgeTone.DANGER
        label = "healthy" if result.healthy else "unhealthy"
        yield Static(
            Text.assemble(
                badge_text(label, tone=tone, emphasis=BadgeEmphasis.STATUS),
                (
                    f"  {result.request_count} request(s) · {result.completion} · "
                    f"gate {result.gate} · run {result.run_id[:8]}…",
                    style("muted"),
                ),
            ),
            classes="environment-health-result",
        )


class EnvironmentResourceDetails(BaseKindResourceDetails):
    """Complete Environment topology, inputs, defaults, policies, and probes."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Environment"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render every Environment field in a stable semantic hierarchy."""
        overview = environment_overview(self.entry)
        resolved = self._resolved_fields()

        yield EnvironmentContextStrip(self)
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )

        yield Separator(label="Connection", align="left", classes="tight")
        yield ResourceSettingList(
            overview.connection,
            empty="No connection settings are declared.",
            resolved=resolved,
        )

        yield Separator(label="Runtime inputs", align="left")
        yield ResourceSettingList(
            overview.runtime_inputs,
            empty="No Environment variables or secrets are declared.",
            resolved=resolved,
        )

        yield Separator(label="Request defaults", align="left")
        yield ResourceSettingList(
            overview.request_defaults,
            empty="No headers, cookies, or authentication defaults are declared.",
            resolved=resolved,
        )

        yield Separator(label="Policies", align="left")
        yield ResourceSettingList(
            overview.policies,
            empty="No Environment-specific policy profiles are attached.",
            resolved=resolved,
        )

        yield Separator(label="Health", align="left")
        yield EnvironmentHealthStatus(self, overview)

        if overview.additional:
            yield Separator(label="Additional configuration", align="left")
            yield ResourceSettingList(
                overview.additional,
                empty="No additional Environment settings are declared.",
                resolved=resolved,
            )

    def _resolved_fields(self) -> dict[tuple[str, ...], ResolvedResourceField]:
        """Index the masked overlay while preserving the authored layout."""
        if self.resource.overview_mode is not ResourceOverviewMode.RESOLVED:
            return {}
        fields = {field.path: field for field in compiled_exact_fields(self.entry)}
        inspection = self.resource.resolved
        if inspection.phase is InspectionPhase.READY and inspection.value is not None:
            fields.update((field.path, field) for field in inspection.value.fields)
        return fields


__all__ = [
    "EnvironmentContextStrip",
    "EnvironmentHealthStatus",
    "EnvironmentResourceDetails",
]
