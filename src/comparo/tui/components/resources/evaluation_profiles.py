"""Assertion, diff, and gate profile inspectors."""

from typing import ClassVar
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content

from comparo.tui.components.badge import BadgeTone
from comparo.tui.components.inspector import InspectorColumns
from comparo.tui.components.inspector import RecordInspector
from comparo.tui.components.list import Field
from comparo.tui.components.separator import Separator
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.typography import Hint
from comparo.tui.models.remaining_resource_presenters import RuleProfileOverview
from comparo.tui.models.remaining_resource_presenters import gate_profile_overview
from comparo.tui.models.remaining_resource_presenters import rule_profile_overview
from comparo.tui.models.resource import ResourceOverviewMode
from comparo.tui.models.resource import ResourcePresentation

from .base import BaseKindResourceDetails
from .profile_shared import ResourceContextStrip
from .profile_shared import ResourceSettingsBody
from .profile_shared import ResourceUsageBody
from .profile_shared import resolved_overview_fields
from .shared import resolution_status


class RuleProfileRulesBody(Vertical):
    """Stable rule identities and evaluation pipelines without runtime operands."""

    def __init__(self, overview: RuleProfileOverview) -> None:
        """Initialize from the pure rule-profile projection."""
        super().__init__(classes="profile-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render rule selection and pipeline semantics in authored order."""
        if not self._overview.rules:
            yield Hint(f"This {self._overview.family} profile declares no local rules.")
            return
        details = {}
        for rule in self._overview.rules:
            fields = [
                Field("Rule", Content(rule.id)),
                Field("Severity", rule.severity),
                Field("Completeness", rule.completeness),
                Field("Combine", Content(rule.combination)),
                Field("Quantifier", Content(rule.quantifier or "Not declared")),
                Field("Name", "Declared" if rule.name_declared else "Not declared"),
                Field("Description", "Declared" if rule.description_declared else "Not declared"),
            ]
            for side in rule.sides:
                fields.extend(
                    (
                        Field(f"{side.role} selector", Content(side.selector)),
                        Field(f"{side.role} pipeline", Content(side.pipeline)),
                    )
                )
            fields.append(Field("Tags", Content(", ".join(rule.tags) or "None")))
            details[rule.id] = tuple(fields)
        yield RecordInspector(
            (Column("id", "Rule"), Column("severity", "Severity", width=10)),
            tuple(Record(rule.id, (rule.id, rule.severity)) for rule in self._overview.rules),
            details,
            title="Selected rule",
            classes="rule-profile-table",
        )
        yield Hint("Runtime operands remain concealed in Overview; press s for masked source.")


class _RuleProfileResourceDetails(BaseKindResourceDetails):
    """Shared continuous document for assertion and diff profile semantics."""

    FAMILY: ClassVar[str]
    TONE: ClassVar[BadgeTone]
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render rules, composition, parameters, and direct consumers."""
        overview = rule_profile_overview(self.entry)
        resolved = resolved_overview_fields(self.resource)
        yield ResourceContextStrip(
            (self.FAMILY, self.TONE),
            (f"{len(overview.rules)} rules", BadgeTone.INFO),
            (f"{len(overview.composition)} composition controls", BadgeTone.NEUTRAL),
            (f"{len(overview.consumers)} consumers", BadgeTone.NEUTRAL),
        )
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        yield Separator(label="Rules", align="left", classes="resource-section-heading")
        yield RuleProfileRulesBody(overview)
        yield Separator(
            label="Composition & edits",
            align="left",
            classes="resource-section-heading",
        )
        yield ResourceSettingsBody(
            overview.composition,
            empty="No included profiles or rule edits are declared.",
            resolved=resolved,
        )
        yield Separator(label="Parameters", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.parameters,
            empty="This profile declares no executable parameters.",
            resolved=resolved,
        )
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield ResourceUsageBody(
            overview.consumers,
            kind=self.entry.summary.coordinate.kind,
        )


class AssertionProfileResourceDetails(_RuleProfileResourceDetails):
    """Per-Environment assertion contract inspector."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"AssertionProfile"})
    FAMILY: ClassVar[str] = "assertions"
    TONE: ClassVar[BadgeTone] = BadgeTone.SUCCESS


class DiffProfileResourceDetails(_RuleProfileResourceDetails):
    """Directed cross-Environment comparison pipeline inspector."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"DiffProfile"})
    FAMILY: ClassVar[str] = "diff"
    TONE: ClassVar[BadgeTone] = BadgeTone.WARNING


class GateProfileResourceDetails(BaseKindResourceDetails):
    """Release-verdict coverage and threshold policy inspector."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"GateProfile"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render gate scope, fail-closed requirements, and thresholds."""
        overview = gate_profile_overview(self.entry)
        resolved = resolved_overview_fields(self.resource)
        yield ResourceContextStrip(
            ("release verdict", BadgeTone.DANGER),
            (f"{len(overview.scope)} scope controls", BadgeTone.INFO),
            (f"{len(overview.requirements)} coverage checks", BadgeTone.WARNING),
            (f"{len(overview.consumers)} consumers", BadgeTone.NEUTRAL),
        )
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        with InspectorColumns():
            with Vertical():
                yield Separator(label="Scope", align="left", classes="resource-section-heading")
                yield ResourceSettingsBody(
                    overview.scope,
                    empty="Version defaults select the gate severities and Environment roles.",
                    resolved=resolved,
                )
            with Vertical():
                yield Separator(
                    label="Coverage requirements",
                    align="left",
                    classes="resource-section-heading",
                )
                yield ResourceSettingsBody(
                    overview.requirements,
                    empty="Version defaults provide fail-closed coverage requirements.",
                    resolved=resolved,
                )
        yield Separator(label="Thresholds", align="left", classes="resource-section-heading")
        yield ResourceSettingsBody(
            overview.thresholds,
            empty="No adverse-fact threshold overrides are declared.",
            resolved=resolved,
        )
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield ResourceUsageBody(overview.consumers, kind="GateProfile")


__all__ = [
    "AssertionProfileResourceDetails",
    "DiffProfileResourceDetails",
    "GateProfileResourceDetails",
    "RuleProfileRulesBody",
]
