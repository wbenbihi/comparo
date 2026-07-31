"""ResourceOverview — kind-aware detail column for one selected resource."""

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.content import Content
from textual.widget import Text
from textual.widgets import Static
from textual.containers import Vertical

from comparo.tui4.components.separator import Separator
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.explorer import ResourceDetail, Field
from comparo.tui4.models.explorer import identity_fields
from comparo.tui4.models.explorer import kind_fields
from comparo.tui4.fragments import entity_label, glyph
from comparo.tui4.theme import kind_glyph
from comparo.tui4.theme import style
from comparo.tui4.fragments import pill


class BaseResourceKindDetail(Vertical):
    """Base Class for specifiic Kind detail."""

    def __init__(self, resource: ResourceDetail, *args, **kwargs) -> None:
        """Init."""
        super().__init__(*args, **kwargs)
        self._resource = resource

    @property
    def entries(self):
        """Retrieve entries."""
        return self._resource.entry


class ResourceProjectDetail(BaseResourceKindDetail):
    """Class for Project Kind detail."""

    def compose(self):
        """Render."""

        yield KeyValueList(*kind_fields(self._resource.entry))


class ResourceWIPDetail(BaseResourceKindDetail):
    """Display basic detail about resource."""

    def compose(self):
        """Render."""
        yield Separator(label="WIP", classes="warning")


_KIND_MAPPER = {"Project": ResourceProjectDetail}


class ResourceSummary(Vertical):
    """Display basic detail about resource."""

    def __init__(self, resource: ResourceDetail, *args, **kwargs) -> None:
        """Init."""
        super().__init__(*args, **kwargs)
        self._resource = resource

    @property
    def entries(self):
        """Retrieve entries."""
        return self._resource.entry

    def compose(self) -> ComposeResult:
        """."""
        yield Static()
        yield Static(self.get_field_description().value)
        yield Separator(label="Detail")
        yield KeyValueList(*self.get_fields(), separators=False)
        yield _KIND_MAPPER.get(self.get_field_kind().value, ResourceWIPDetail)(
            resource=self._resource
        )

    def get_field_description(self) -> Field:
        """Return Description."""
        return Field(key="description", value=self.entries.summary.description or "N/A")

    def get_field_tag(self) -> Field | None:
        """Retrieve tags."""
        if not self._resource.entry.summary.tags:
            return None

        tags = Text()
        for tag in self._resource.entry.summary.tags:
            tags.append_text(pill(tag, "pill.default"))
            tags.append(" · ")
        return Field(key="tags", value=Content.from_rich_text(tags))

    def get_field_id(self) -> Field:
        """Retrieve id."""
        return Field(key="id", value=self.entries.summary.coordinate.resource_id)

    def get_field_kind(self) -> Field:
        """Retrieve Kind."""
        return Field(key="kind", value=self.entries.summary.coordinate.kind)

    def get_fields(self) -> list[Field]:
        """Retrieve the required fields."""
        fields: list[Field | None] = [
            self.get_field_id(),
            self.get_field_kind(),
            self.get_field_tag(),
        ]
        return [f for f in fields if isinstance(f, Field)]


class ResourceDetailOverview(Widget):
    """Panel to explore Resource detail."""

    detail: reactive[ResourceDetail | None] = reactive(None, recompose=True, init=False)

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

        yield Panel(ResourceSummary(resource=detail), title=title, hint="HINT TO DO")
