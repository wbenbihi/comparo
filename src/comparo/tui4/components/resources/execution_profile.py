"""ExecutionProfile overview for reusable run selection and controls."""

from typing import ClassVar
from typing import override

from textual.app import ComposeResult

from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.separator import Separator
from comparo.tui4.models.remaining_resource_presenters import execution_profile_overview
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourcePresentation

from .base import BaseKindResourceDetails
from .profile_shared import ResourceContextStrip
from .profile_shared import ResourceSettingsBody
from .profile_shared import ResourceUsageBody
from .profile_shared import resolved_overview_fields
from .shared import resolution_status


class ExecutionProfileResourceDetails(BaseKindResourceDetails):
    """Named execution selection, topology, inputs, and evaluation attachments."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"ExecutionProfile"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render the complete reusable run declaration as a continuous document."""
        overview = execution_profile_overview(self.entry)
        resolved = resolved_overview_fields(self.resource)
        mode_tone = {
            "comparison fields": BadgeTone.CANDIDATE,
            "assertion environments": BadgeTone.SUCCESS,
        }.get(overview.authored_selection, BadgeTone.WARNING)
        trials = (
            "trials inherited"
            if overview.declared_trials is None
            else (
                f"{overview.declared_trials} authored trials"
                if len(str(overview.declared_trials)) <= 12
                else "trials declared"
            )
        )
        yield ResourceContextStrip(
            (overview.authored_selection, mode_tone),
            (f"{overview.declared_subject_count} authored subjects", BadgeTone.INFO),
            (f"{overview.declared_environment_count} authored envs", BadgeTone.NEUTRAL),
            (trials, BadgeTone.ACTIVE),
        )
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        yield Separator(label="Selection", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.selection,
            empty=(
                "No selection is declared in this profile; Project defaults or invocation "
                "overrides may supply it at execution."
            ),
            resolved=resolved,
        )
        yield Separator(
            label="Execution controls",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.controls,
            empty=(
                "No execution controls are declared here; Project defaults or invocation "
                "overrides may supply them."
            ),
            resolved=resolved,
        )
        yield Separator(label="Runtime inputs", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.inputs,
            empty="No execution-scoped parameters, variables, or secrets are declared.",
            resolved=resolved,
        )
        yield Separator(
            label="Evaluation & output",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.evaluation,
            empty="No execution-scoped evaluation or output profiles are attached.",
            resolved=resolved,
        )
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield ResourceUsageBody(overview.consumers, kind="ExecutionProfile")


__all__ = ["ExecutionProfileResourceDetails"]
