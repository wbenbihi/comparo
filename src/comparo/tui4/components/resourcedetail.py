"""Kind-aware detail column for one selected resource."""

from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from comparo.tui4.components.list import Field
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.typography import Hint
from comparo.tui4.fragments import glyph
from comparo.tui4.fragments import pill
from comparo.tui4.models.explorer import ResourceDetail
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.explorer import kind_fields
from comparo.tui4.theme import kind_glyph
from comparo.tui4.theme import style


class ResourceKindDetails(Vertical):
    """Render the existing kind-specific resource details."""

    def __init__(self, entry: ResourceEntry) -> None:
        """Initialize the section for one resource entry."""
        super().__init__()
        self._entry = entry

    @override
    def compose(self) -> ComposeResult:
        """Render project fields or the existing work-in-progress marker."""
        if self._entry.summary.coordinate.kind == "Project":
            yield KeyValueList(*kind_fields(self._entry))
        else:
            yield Separator(label="WIP", classes="warning")


class ResourceSummary(Vertical):
    """Display basic detail about resource."""

    def __init__(self, resource: ResourceDetail) -> None:
        """Initialize the summary from an immutable resource projection."""
        super().__init__()
        self._resource = resource

    @property
    def entry(self) -> ResourceEntry:
        """Return the selected resource entry."""
        return self._resource.entry

    @override
    def compose(self) -> ComposeResult:
        """Compose the generic summary and current kind-specific section."""
        yield Static(self.entry.summary.description or "N/A")
        yield Separator(label="Detail")
        yield KeyValueList(*self._fields(), separators=False)
        yield ResourceKindDetails(self.entry)

    def _tag_field(self) -> Field | None:
        """Build the optional Rich tag row."""
        if not self.entry.summary.tags:
            return None
        tags = Text(" · ").join(pill(tag, "pill.default") for tag in self.entry.summary.tags)
        return Field(key="tags", value=Content.from_rich_text(tags))

    def _fields(self) -> tuple[Field, ...]:
        """Build the stable identity rows shown for every resource."""
        fields = [
            Field(key="id", value=self.entry.summary.coordinate.resource_id),
            Field(key="kind", value=self.entry.summary.coordinate.kind),
            self._tag_field(),
        ]
        return tuple(field for field in fields if field is not None)


class ResourceDetailOverview(Widget):
    """Panel to explore Resource detail."""

    detail: reactive[ResourceDetail | None] = reactive(None, recompose=True, init=False)

    @override
    def compose(self) -> ComposeResult:
        """Sections chosen by the selected resource's kind."""
        detail = self.detail
        # Edge-Case no resource
        if detail is None:
            yield Panel(Hint("Select a resource."), title="Overview")
            return

        summary = detail.entry.summary
        character, token = kind_glyph(summary.coordinate.kind)
        title = glyph(character, token)
        title.append(summary.name or summary.coordinate.resource_id)
        if summary.name:
            title.append(f" · {summary.coordinate.resource_id}", style("dim"))

        yield Panel(ResourceSummary(resource=detail), title=title)
