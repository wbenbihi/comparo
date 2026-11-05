"""Schema overview focused on validation structure and consumers."""

from dataclasses import replace
from typing import ClassVar
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical

from comparo.tui.components.badge import Badge
from comparo.tui.components.badge import BadgeTone
from comparo.tui.components.resourcelinks import ResourceLinksTable
from comparo.tui.components.separator import Separator
from comparo.tui.components.typography import Hint
from comparo.tui.models.resource import ResourcePresentation
from comparo.tui.models.resource_presenters import SchemaOverview
from comparo.tui.models.resource_presenters import schema_overview
from comparo.tui.models.schema_document import schema_nodes

from .base import BaseKindResourceDetails
from .shared import ResourceSettingList


class SchemaContextStrip(Horizontal):
    """Compact schema source, root type, property, and required counts."""

    def __init__(self, overview: SchemaOverview) -> None:
        """Build semantic badges from the pure Schema projection."""
        property_count = len(overview.properties)
        required_count = sum(item.required for item in overview.properties)
        super().__init__(
            Badge(
                overview.source,
                tone=BadgeTone.INFO if overview.source == "remote" else BadgeTone.SUCCESS,
                classes="schema-context-badge",
            ),
            Badge(overview.root_type, tone=BadgeTone.ACTIVE, classes="schema-context-badge"),
            Badge(
                f"{property_count} schema nodes",
                tone=BadgeTone.NEUTRAL,
                classes="schema-context-badge",
            ),
            Badge(
                f"{required_count} required",
                tone=BadgeTone.WARNING if required_count else BadgeTone.NEUTRAL,
                classes="schema-context-badge",
            ),
            classes="schema-context-strip",
        )


class SchemaPropertiesBody(Vertical):
    """Expandable property structure with a synchronized definition pane."""

    def __init__(self, overview: SchemaOverview) -> None:
        """Initialize from immutable structural metadata."""
        super().__init__(classes="schema-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Keep every property accessible without flattening nested object structure."""
        if not self._overview.properties:
            yield Hint("This Schema does not declare object properties.")
            return
        from .schema_tree import SchemaPropertyBrowser

        yield SchemaPropertyBrowser(self._overview.properties)


class SchemaCompositionBody(Vertical):
    """Schema combinators and root-level constraints outside the property tree."""

    def __init__(self, overview: SchemaOverview) -> None:
        """Initialize from immutable structural metadata."""
        super().__init__(classes="schema-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render composition and constraints as distinct semantic sections."""
        if self._overview.constraints:
            yield Separator(label="Root constraints", align="left", classes="tight")
            yield ResourceSettingList(
                self._overview.constraints,
                empty="",
                resolved={},
            )
        if not self._overview.composition and not self._overview.constraints:
            yield Hint("No root composition or validation constraints are declared.")


class SchemaUsageBody(Vertical):
    """Schema consumers with full graph discovery delegated to the common g view."""

    def __init__(self, overview: SchemaOverview) -> None:
        """Initialize from immutable dependency metadata."""
        super().__init__(classes="schema-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render consumers without duplicating dependency navigation mechanics."""
        if not self._overview.consumers:
            yield Hint("This Schema has no declared consumers.")
            return
        yield ResourceLinksTable(self._overview.consumers, classes="schema-usage-table")
        yield Hint("Enter opens the selected resource · g shows all relationships.")


class SchemaResourceDetails(BaseKindResourceDetails):
    """JSON Schema structure, composition, constraints, and consumer inspection."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Schema"})
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation(
        common_representation_hints=True,
        visible_actions=frozenset(),
    )

    @override
    def compose(self) -> ComposeResult:
        """Render Schema anatomy as one continuous validation document."""
        overview = replace(
            schema_overview(self.entry),
            properties=schema_nodes(self.entry, self.resource.schema_catalog),
        )
        yield SchemaContextStrip(overview)
        if overview.source == "remote":
            yield Hint(
                "Remote schema document has not been fetched. No cached validation is claimed."
            )
        yield Separator(label="Schema structure", align="left", classes="resource-section-heading")
        yield SchemaPropertiesBody(overview)
        yield Separator(
            label="Root constraints",
            align="left",
            classes="resource-section-heading",
        )
        yield SchemaCompositionBody(overview)
        yield Separator(label="Usage", align="left", classes="resource-section-heading")
        yield SchemaUsageBody(overview)


__all__ = [
    "SchemaCompositionBody",
    "SchemaContextStrip",
    "SchemaPropertiesBody",
    "SchemaResourceDetails",
    "SchemaUsageBody",
]
