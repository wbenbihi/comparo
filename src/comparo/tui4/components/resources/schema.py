"""Schema overview focused on validation structure and consumers."""

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.content import Content

from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.resource import ResourcePresentation
from comparo.tui4.models.resource_presenters import SchemaOverview
from comparo.tui4.models.resource_presenters import schema_overview
from comparo.tui4.theme import style

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
                f"{property_count} {'property' if property_count == 1 else 'properties'}",
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
    """Recursive schema property table using dotted payload paths."""

    def __init__(self, overview: SchemaOverview) -> None:
        """Initialize from immutable structural metadata."""
        super().__init__(classes="schema-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render a scan-friendly validation contract."""
        if not self._overview.properties:
            yield Hint("This Schema does not declare object properties.")
            return
        yield Table(
            columns=(
                Column("path", "Property"),
                Column("type", "Type", width=14),
                Column("presence", "Presence", width=11),
                Column("constraints", "Constraints"),
            ),
            records=tuple(
                Record(
                    f"property:{index}",
                    (
                        Content.from_rich_text(Text(".".join(prop.path), style("fg"))),
                        Content.from_rich_text(Text(prop.type, style("kind.schema"))),
                        Content.from_rich_text(
                            Text(
                                "required" if prop.required else "optional",
                                style("warn" if prop.required else "muted"),
                            )
                        ),
                        Content.from_rich_text(
                            Text(", ".join(prop.constraints) or "—", style("muted"))
                        ),
                    ),
                )
                for index, prop in enumerate(self._overview.properties)
            ),
            classes="schema-properties-table",
        )


class SchemaCompositionBody(Vertical):
    """Schema combinators and root-level constraints outside the property tree."""

    def __init__(self, overview: SchemaOverview) -> None:
        """Initialize from immutable structural metadata."""
        super().__init__(classes="schema-section-body")
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Render composition and constraints as distinct semantic sections."""
        if self._overview.composition:
            yield Separator(label="Composition", align="left", classes="tight")
            yield ResourceSettingList(
                self._overview.composition,
                empty="",
                resolved={},
            )
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
        yield Table(
            columns=(Column("kind", "Kind", width=12), Column("resource", "Consumer")),
            records=tuple(
                Record(
                    f"consumer:{index}",
                    (item.target_kind, item.target_id),
                )
                for index, item in enumerate(self._overview.consumers)
            ),
            classes="schema-usage-table",
        )
        yield Hint("Press g for the complete directional dependency graph.")


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
        overview = schema_overview(self.entry)
        yield SchemaContextStrip(overview)
        yield Separator(label="Properties", align="left", classes="resource-section-heading")
        yield SchemaPropertiesBody(overview)
        yield Separator(
            label="Composition & constraints",
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
