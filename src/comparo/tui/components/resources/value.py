"""Reusable Value overview with masked context resolution."""

from collections.abc import Mapping
from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.content import Content

from comparo.tui.components.badge import Badge
from comparo.tui.components.badge import BadgeTone
from comparo.tui.components.inspector import InspectorColumns
from comparo.tui.components.inspector import InspectorSection
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.resourcelinks import ResourceLinksTable
from comparo.tui.components.separator import Separator
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.table import Table
from comparo.tui.components.typography import Hint
from comparo.tui.models.resource import InspectionPhase
from comparo.tui.models.resource import ResolvedResourceField
from comparo.tui.models.resource import ResourceOverviewMode
from comparo.tui.models.resource import ResourcePresentation
from comparo.tui.models.resource_presenters import ValueOverview
from comparo.tui.models.resource_presenters import compiled_exact_fields
from comparo.tui.models.resource_presenters import value_overview
from comparo.tui.theme import style

from .base import BaseKindResourceDetails
from .shared import ResourceSettingList
from .shared import resolution_status


class ValueContextStrip(Horizontal):
    """Compact reusable Value shape, directive, dependency, and consumer counts."""

    def __init__(self, overview: ValueOverview) -> None:
        """Build semantic badges from the pure Value projection."""
        shape = "unavailable"
        if overview.value is not None:
            shape = overview.value.value.partition(" ")[0]
        super().__init__(
            Badge(shape, tone=BadgeTone.INFO, classes="value-context-badge"),
            Badge(
                f"{len(overview.directives)} runtime inputs",
                tone=BadgeTone.ACTIVE if overview.directives else BadgeTone.NEUTRAL,
                classes="value-context-badge",
            ),
            Badge(
                f"uses {len(overview.dependencies)} resources",
                tone=BadgeTone.WARNING if overview.dependencies else BadgeTone.NEUTRAL,
                classes="value-context-badge",
            ),
            Badge(
                f"used by {len(overview.consumers)} resources",
                tone=BadgeTone.SUCCESS if overview.consumers else BadgeTone.NEUTRAL,
                classes="value-context-badge",
            ),
            classes="value-context-strip",
        )


class ValueExpressionBody(Vertical):
    """Complete authored or masked-resolved reusable Value tree."""

    def __init__(
        self,
        overview: ValueOverview,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize from the pure Value projection and optional exact overlay."""
        super().__init__(classes="value-section-body")
        self._overview = overview
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render the same hierarchy in raw and resolved modes."""
        if self._overview.value is None:
            yield Hint("This Value has no inspectable expression tree.")
            return
        if self._resolved:
            yield KeyValueList(
                *(
                    Field(
                        ".".join(field.path[1:]) or "value",
                        Content.from_rich_text(Text(field.value, style(field.tone or "fg"))),
                    )
                    for field in self._resolved.values()
                )
            )
            return
        yield ResourceSettingList(
            (self._overview.value,),
            empty="This Value has no inspectable expression tree.",
            resolved=self._resolved,
        )


class ValueInputsBody(Vertical):
    """Directive locations and reusable-Value dependencies."""

    def __init__(self, overview: ValueOverview) -> None:
        """Initialize from immutable Value topology."""
        super().__init__(classes="value-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render exact expression paths without attempting resolution locally."""
        if not self._overview.directives:
            yield Hint("This Value has no runtime directives.")
            return
        yield Table(
            columns=(Column("path", "Path"), Column("expression", "Expression")),
            records=tuple(
                Record(
                    f"directive:{index}",
                    (
                        Content.from_rich_text(Text(".".join(item.path) or "value", style("fg"))),
                        Content.from_rich_text(Text(item.expression, style("kind.value"))),
                    ),
                )
                for index, item in enumerate(self._overview.directives)
            ),
            classes="value-inputs-table",
        )


class ValueUsageBody(Vertical):
    """Inbound and outbound resource edges for one reusable Value."""

    def __init__(self, overview: ValueOverview) -> None:
        """Initialize from immutable Value topology."""
        super().__init__(classes="value-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render directional resource use without duplicating the graph view."""
        edges = (*self._overview.dependencies, *self._overview.consumers)
        if not edges:
            yield Hint("This Value is isolated from other resources.")
            return
        yield ResourceLinksTable(edges, directions=True, classes="value-usage-table")
        yield Hint("Enter opens the selected resource · g shows all relationships.")


class ValueResourceDetails(BaseKindResourceDetails):
    """Reusable Value expression, runtime inputs, resolution, and usage inspection."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Value"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render one continuous Value document in raw and resolved modes."""
        overview = value_overview(self.entry)
        yield ValueContextStrip(overview)
        with InspectorColumns():
            yield InspectorSection("Expression", ValueExpressionBody(overview, {}))
            with InspectorSection("Resolved value"):
                if self.resource.overview_mode is ResourceOverviewMode.RAW:
                    yield Hint("Press r to resolve this Value.")
                else:
                    yield resolution_status(
                        self.resource,
                        self.entry.summary.name or self.entry.summary.coordinate.resource_id,
                    )
                    if self.resource.resolved.phase is InspectionPhase.READY:
                        yield ValueExpressionBody(overview, self._resolved_fields())
        yield Separator(label="Runtime inputs", align="left", classes="resource-section-heading")
        yield ValueInputsBody(overview)
        yield Separator(
            label="Dependencies & usage",
            align="left",
            classes="resource-section-heading",
        )
        yield ValueUsageBody(overview)

    def _resolved_fields(self) -> dict[tuple[str, ...], ResolvedResourceField]:
        """Index exact references and masked resolver fields for the stable layout."""
        if self.resource.overview_mode is not ResourceOverviewMode.RESOLVED:
            return {}
        fields = {field.path: field for field in compiled_exact_fields(self.entry)}
        inspection = self.resource.resolved
        if inspection.phase is InspectionPhase.READY and inspection.value is not None:
            fields.update((field.path, field) for field in inspection.value.fields)
        return fields


__all__ = [
    "ValueContextStrip",
    "ValueExpressionBody",
    "ValueInputsBody",
    "ValueResourceDetails",
    "ValueUsageBody",
]
