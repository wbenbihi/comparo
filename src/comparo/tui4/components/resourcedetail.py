"""Kind-aware detail host for one selected resource."""

from __future__ import annotations

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from comparo.tui4.bindings import resource_detail_bindings
from comparo.tui4.components.diffview import DiffView
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.resourceactions import ResourceActionBar
from comparo.tui4.components.resources import BaseKindResourceDetails
from comparo.tui4.components.resources import resource_widget
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Caption
from comparo.tui4.components.typography import Hint
from comparo.tui4.fragments import glyph
from comparo.tui4.models.diff import build_source_document
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResourceActionId
from comparo.tui4.models.resource import ResourceDetail
from comparo.tui4.models.resource import ResourceRepresentation
from comparo.tui4.theme import kind_glyph
from comparo.tui4.theme import style


def _inspection_message(phase: InspectionPhase, message: str | None) -> str:
    """Return safe user-facing copy for a representation's load state."""
    if phase is InspectionPhase.LOADING:
        return "Loading bounded inspection…"
    if phase is InspectionPhase.FAILED:
        return f"Inspection failed: {message}"
    if phase is InspectionPhase.UNAVAILABLE:
        return message or "This representation is unavailable."
    return "Choose this representation to load it."


class ResourceSummary(Vertical):
    """Generic identity followed by the explicitly registered kind widget."""

    def __init__(self, resource: ResourceDetail) -> None:
        """Initialize the summary from immutable resource state."""
        super().__init__()
        self._resource = resource

    @property
    def entry(self) -> ResourceEntry:
        """Return the selected resource entry."""
        return self._resource.entry

    @override
    def compose(self) -> ComposeResult:
        """Compose shared metadata and the kind-owned details."""
        yield Static(
            self.entry.summary.description or "No description.",
            classes="resource-description",
        )
        yield Caption(self._provenance(), classes="resource-provenance")
        yield Separator(label="Resource", align="left")
        yield resource_widget(self._resource)

    def _provenance(self) -> str:
        """Build one compact, value-safe revision, source, and tag line."""
        summary = self.entry.summary
        revision = f"{summary.coordinate.fingerprint[:12]}…"
        source = summary.source
        tags = " ".join(f"#{tag}" for tag in summary.tags) or "untagged"
        return f"rev {revision} | {source.source} · line {source.line} | {tags}"


class OverviewRepresentation(VerticalScroll):
    """Scrollable identity and kind-specific overview."""

    def __init__(self, detail: ResourceDetail) -> None:
        """Initialize the overview representation."""
        super().__init__(classes="resource-representation")
        self._detail = detail

    @override
    def compose(self) -> ComposeResult:
        """Compose overview content."""
        yield ResourceSummary(self._detail)


class AuthoredRepresentation(Vertical):
    """Masked, revision-pinned authored YAML for exactly one resource document."""

    def __init__(self, detail: ResourceDetail) -> None:
        """Initialize the authored representation."""
        super().__init__(classes="resource-representation")
        self._detail = detail

    @override
    def compose(self) -> ComposeResult:
        """Render a bounded source window or its explicit lifecycle state."""
        inspection = self._detail.authored
        window = inspection.value
        if inspection.phase is not InspectionPhase.READY or window is None:
            yield Hint(_inspection_message(inspection.phase, inspection.message))
            return
        disclosure = "masked"
        if window.redaction_count:
            disclosure = f"masked · {window.redaction_count} redaction(s)"
        yield Caption(f"{window.source.source} · document {window.source.document} · {disclosure}")
        yield DiffView(
            build_source_document(
                window.content,
                language="yaml",
                first_lineno=window.resource_start_line + window.start_resource_line - 1,
                detect=False,
                pretty=False,
            ),
            classes="resource-source-view",
        )


class EffectiveRepresentation(VerticalScroll):
    """Value-safe compiled outline and dependency edges."""

    def __init__(self, detail: ResourceDetail) -> None:
        """Initialize the effective representation."""
        super().__init__(classes="resource-representation")
        self._detail = detail

    @override
    def compose(self) -> ComposeResult:
        """Render bounded effective fields and relationships."""
        inspection = self._detail.effective
        window = inspection.value
        if inspection.phase is not InspectionPhase.READY or window is None:
            yield Hint(_inspection_message(inspection.phase, inspection.message))
            return
        yield Caption(
            f"Value-safe compiled structure · {window.total_fields} fields · "
            f"{window.total_relationships} relationships"
        )
        yield Separator(label="Fields", align="left")
        yield Table(
            columns=(
                Column("path", "Path"),
                Column("category", "Category", width=14),
                Column("value", "Safe value"),
            ),
            records=tuple(
                Record(
                    f"field:{index}",
                    (".".join(field.path) or field.label, field.category, field.value),
                )
                for index, field in enumerate(window.fields)
            ),
            classes="resource-effective-table",
        )
        yield Separator(label="Relationships", align="left")
        if not window.relationships:
            yield Hint("This resource has no dependency edges.")
            return
        yield Table(
            columns=(
                Column("path", "Path"),
                Column("origin", "Origin", width=12),
                Column("target", "Target"),
            ),
            records=tuple(
                Record(
                    f"relationship:{index}",
                    (
                        ".".join(relationship.path) or "—",
                        relationship.origin,
                        f"{relationship.target.kind}/{relationship.target.resource_id}",
                    ),
                )
                for index, relationship in enumerate(window.relationships)
            ),
            classes="resource-relationship-table",
        )


class ResolvedRepresentation(VerticalScroll):
    """Context-bound resolved values when supplied by Compare preparation."""

    def __init__(self, detail: ResourceDetail) -> None:
        """Initialize the resolved representation."""
        super().__init__(classes="resource-representation")
        self._detail = detail

    @override
    def compose(self) -> ComposeResult:
        """Render masked resolved fields or explain the missing context."""
        inspection = self._detail.resolved
        value = inspection.value
        if inspection.phase is not InspectionPhase.READY or value is None:
            yield Hint(_inspection_message(inspection.phase, inspection.message))
            return
        yield KeyValueList(*value.fields)


class ResourceDetailOverview(Widget):
    """Generic host for actions, representations, and registered kind widgets."""

    BINDINGS: ClassVar[list[BindingType]] = resource_detail_bindings()

    detail: reactive[ResourceDetail | None] = reactive(None, recompose=True, init=False)

    class ActionRequested(Message):
        """The host requested an effectful or representation-changing action."""

        def __init__(self, host: ResourceDetailOverview, action: ResourceActionId) -> None:
            """Initialize the semantic action request."""
            self.host = host
            self.action = action
            super().__init__()

        @property
        @override
        def control(self) -> ResourceDetailOverview:
            """Return the detail host that emitted the request."""
            return self.host

    @override
    def compose(self) -> ComposeResult:
        """Compose the stable host chrome and active representation."""
        detail = self.detail
        if detail is None:
            yield Panel(Hint("Select a resource."), title="Overview")
            return
        yield Panel(
            ResourceActionBar(detail.actions),
            Separator(classes="tight"),
            self._representation(detail),
            title=self._title(detail),
            id="resource-detail-panel",
        )

    def _representation(self, detail: ResourceDetail) -> Widget:
        """Construct only the active representation widget."""
        if detail.representation is ResourceRepresentation.AUTHORED:
            return AuthoredRepresentation(detail)
        if detail.representation is ResourceRepresentation.EFFECTIVE:
            return EffectiveRepresentation(detail)
        if detail.representation is ResourceRepresentation.RESOLVED:
            return ResolvedRepresentation(detail)
        return OverviewRepresentation(detail)

    @staticmethod
    def _title(detail: ResourceDetail) -> Text:
        """Build the stable glyph/name/revision title."""
        summary = detail.entry.summary
        character, token = kind_glyph(summary.coordinate.kind)
        title = glyph(character, token)
        title.append(summary.name or summary.coordinate.resource_id)
        if summary.name:
            title.append(f" · {summary.coordinate.resource_id}", style("dim"))
        title.append(f" · {detail.representation.value}", style("dim"))
        return title

    def action_resource_action(self, action_id: str) -> None:
        """Translate a configurable key binding into the same semantic action."""
        try:
            action = ResourceActionId(action_id)
        except ValueError:
            return
        bar = self.query_one(ResourceActionBar)
        bar.request(action)

    def on_resource_action_bar_action_requested(
        self, event: ResourceActionBar.ActionRequested
    ) -> None:
        """Hide component mechanics from the workspace coordinator."""
        event.stop()
        self.post_message(self.ActionRequested(self, event.action))


__all__ = ["BaseKindResourceDetails", "ResourceDetailOverview"]
