"""Safety, redaction, and artifact profile inspectors."""

from typing import ClassVar
from typing import override

from textual.app import ComposeResult

from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.remaining_resource_presenters import artifact_profile_overview
from comparo.tui4.models.remaining_resource_presenters import redaction_profile_overview
from comparo.tui4.models.remaining_resource_presenters import safety_profile_overview
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourcePresentation

from .base import BaseKindResourceDetails
from .profile_shared import ResourceContextStrip
from .profile_shared import ResourceSettingsBody
from .profile_shared import ResourceUsageBody
from .profile_shared import resolved_overview_fields
from .shared import resolution_status


class SafetyProfileResourceDetails(BaseKindResourceDetails):
    """Project-controlled restrictions and response-data classification."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"SafetyProfile"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render capability families without implying operator authority."""
        overview = safety_profile_overview(self.entry)
        resolved = resolved_overview_fields(self.resource)
        yield ResourceContextStrip(
            ("project restrictions", BadgeTone.DANGER),
            (f"{len(overview.composition)} composition controls", BadgeTone.NEUTRAL),
            (f"{len(overview.consumers)} consumers", BadgeTone.INFO),
        )
        yield Hint(
            "SafetyProfile can only restrict a run; external ExecutionPolicy remains the "
            "operator authority ceiling."
        )
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        yield Separator(
            label="Composition",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.composition,
            empty="This profile does not include or extend another SafetyProfile.",
            resolved=resolved,
        )
        yield Separator(
            label="Remote schemas",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.schemas,
            empty="No remote Schema source restriction is declared.",
            resolved=resolved,
        )
        yield Separator(label="Classification", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.classification,
            empty="Version defaults classify response data for this profile.",
            resolved=resolved,
        )
        yield Separator(label="Budgets", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.budgets,
            empty="No additional resource ceilings are declared.",
            resolved=resolved,
        )
        if overview.unsupported:
            yield Separator(
                label=f"Reserved fields · {overview.unsupported_support.value}",
                align="left",
                classes="resource-section-heading",
            )
            yield Hint(
                "Network, filesystem, and ambient-environment fields are schema-reserved but "
                "not implemented; a selected execution fails closed."
            )
            yield ResourceSettingsBody(
                overview.unsupported,
                empty="",
                resolved=resolved,
            )
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield ResourceUsageBody(overview.consumers, kind="SafetyProfile")


class RedactionProfileResourceDetails(BaseKindResourceDetails):
    """Selector-based sanitization and conservative backstop inspection."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"RedactionProfile"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render sanitization structure while keeping raw patterns out of Overview."""
        overview = redaction_profile_overview(self.entry)
        resolved = resolved_overview_fields(self.resource)
        yield ResourceContextStrip(
            ("sanitization", BadgeTone.WARNING),
            (f"{len(overview.rules)} rule groups", BadgeTone.INFO),
            (f"{len(overview.patterns)} pattern groups", BadgeTone.NEUTRAL),
            (f"{len(overview.consumers)} consumers", BadgeTone.NEUTRAL),
        )
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        yield Separator(label="Rules", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.rules,
            empty="No selector-based redaction rules are declared.",
            resolved=resolved,
        )
        yield Separator(
            label=f"Patterns · {overview.patterns_support.value}",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.patterns,
            empty="No unsupported pattern declaration is present.",
            resolved=resolved,
        )
        if overview.patterns:
            yield Hint(
                "Pattern redaction is schema-reserved but not implemented; a selected execution "
                "fails closed. Pattern text remains concealed."
            )
        yield Separator(
            label=f"Backstop · {overview.backstop_support.value}",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.backstop,
            empty="No reserved backstop declaration is present.",
            resolved=resolved,
        )
        if overview.backstop:
            yield Hint("Backstop is validated but is not yet represented in runtime policy.")
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield ResourceUsageBody(overview.consumers, kind="RedactionProfile")


class ArtifactProfileResourceDetails(BaseKindResourceDetails):
    """Sanitized artifact capture and persistence inspection."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"ArtifactProfile"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render what may be captured, persisted, and retained."""
        overview = artifact_profile_overview(self.entry)
        resolved = resolved_overview_fields(self.resource)
        yield ResourceContextStrip(
            ("sanitized artifacts", BadgeTone.INFO),
            (f"{overview.capture_control_count} capture controls", BadgeTone.ACTIVE),
            (f"{overview.storage_control_count} storage controls", BadgeTone.NEUTRAL),
            (f"{len(overview.consumers)} consumers", BadgeTone.NEUTRAL),
        )
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        yield Separator(label="Capture", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.capture,
            empty="Version defaults determine sanitized capture coverage.",
            resolved=resolved,
        )
        yield Separator(label="Storage", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.storage,
            empty="Version defaults determine artifact persistence behavior.",
            resolved=resolved,
        )
        yield Separator(
            label=f"Retention · {overview.retention_support.value}",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.retention,
            empty="No reserved retention placeholder is declared.",
            resolved=resolved,
        )
        if overview.retention:
            yield Hint(
                "Only an empty retention placeholder is admitted; retention controls are not "
                "implemented and non-empty declarations fail closed."
            )
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield ResourceUsageBody(overview.consumers, kind="ArtifactProfile")


__all__ = [
    "ArtifactProfileResourceDetails",
    "RedactionProfileResourceDetails",
    "SafetyProfileResourceDetails",
]
