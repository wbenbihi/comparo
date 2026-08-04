"""Reusable Value overview with masked context resolution."""

from collections.abc import Mapping
from typing import ClassVar
from typing import override

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.content import Content
from textual.widgets import TabbedContent
from textual.widgets import TabPane

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedResourceField
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourcePresentation
from comparo.tui4.models.resource_presenters import ValueOverview
from comparo.tui4.models.resource_presenters import compiled_exact_fields
from comparo.tui4.models.resource_presenters import value_overview
from comparo.tui4.theme import style

from .base import BaseKindResourceDetails
from .shared import ResourceSettingList
from .shared import resolution_status

_VALUE_TAB_VALUE = "value-expression"
_VALUE_TAB_INPUTS = "value-inputs"
_VALUE_TAB_USAGE = "value-usage"
_VALUE_TABS = frozenset({_VALUE_TAB_VALUE, _VALUE_TAB_INPUTS, _VALUE_TAB_USAGE})


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
                f"{len(overview.consumers)} consumers",
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
        super().__init__(classes="value-tab-body")
        self._overview = overview
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render the same hierarchy in raw and resolved modes."""
        if self._overview.value is None:
            yield Hint("This Value has no inspectable expression tree.")
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
        super().__init__(classes="value-tab-body")
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
        super().__init__(classes="value-tab-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render directional resource use without duplicating the graph view."""
        edges = (*self._overview.dependencies, *self._overview.consumers)
        if not edges:
            yield Hint("This Value is isolated from other resources.")
            return
        yield Table(
            columns=(
                Column("direction", "Direction", width=11),
                Column("kind", "Kind", width=12),
                Column("resource", "Resource"),
            ),
            records=tuple(
                Record(
                    f"edge:{index}",
                    (
                        Content.from_rich_text(Text(edge.direction, style("muted"))),
                        Content.from_rich_text(Text(edge.target_kind, style("kind.value"))),
                        Content.from_rich_text(Text(edge.target_id, style("fg"))),
                    ),
                )
                for index, edge in enumerate(edges)
            ),
            classes="value-usage-table",
        )
        yield Hint("Press g for the complete directional dependency graph.")


class ValueResourceDetails(BaseKindResourceDetails):
    """Reusable Value expression, runtime inputs, resolution, and usage inspection."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Value"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render one stable native-tab layout in both raw and resolved modes."""
        overview = value_overview(self.entry)
        yield ValueContextStrip(overview)
        if self.resource.overview_mode is ResourceOverviewMode.RESOLVED:
            yield resolution_status(
                self.resource,
                self.entry.summary.name or self.entry.summary.coordinate.resource_id,
            )
        with TabbedContent(initial=self._active_tab(), id="value-tabs"):
            yield TabPane(
                "Value",
                ValueExpressionBody(overview, self._resolved_fields()),
                id=_VALUE_TAB_VALUE,
            )
            yield TabPane("Inputs", ValueInputsBody(overview), id=_VALUE_TAB_INPUTS)
            yield TabPane("Usage", ValueUsageBody(overview), id=_VALUE_TAB_USAGE)

    def _active_tab(self) -> str:
        """Restore only a known Value section."""
        section = self.initial_section
        return section if section in _VALUE_TABS else _VALUE_TAB_VALUE

    @on(TabbedContent.TabActivated, "#value-tabs")
    def on_value_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Publish native tab selection for cross-recomposition retention."""
        event.stop()
        section = event.pane.id
        if section is not None and section in _VALUE_TABS:
            self.select_section(section)

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
