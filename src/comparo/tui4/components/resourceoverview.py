"""ResourceOverview — kind-aware detail column for one selected resource."""

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive

from comparo.tui4.components.diffview import DiffView
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.diff import build_source_document
from comparo.tui4.models.explorer import CONSUMER_COLUMNS
from comparo.tui4.models.explorer import RELATIONSHIP_COLUMNS
from comparo.tui4.models.explorer import ResourceDetail
from comparo.tui4.models.explorer import consumer_records
from comparo.tui4.models.explorer import dependency_records
from comparo.tui4.models.explorer import effective_fields
from comparo.tui4.models.explorer import effective_subtitle
from comparo.tui4.models.explorer import identity_fields
from comparo.tui4.models.explorer import kind_fields
from comparo.tui4.models.explorer import kind_section_title
from comparo.tui4.models.explorer import outline_fields
from comparo.tui4.models.explorer import source_subtitle


class ResourceOverview(VerticalScroll):
    """Kind-aware sections; set ``detail`` and the column recomposes."""

    detail: reactive[ResourceDetail | None] = reactive(None, recompose=True, init=False)

    def compose(self) -> ComposeResult:
        """Sections chosen by the selected resource's kind."""
        detail = self.detail
        if detail is None:
            yield Panel(Hint("Select a resource."), title="Overview")
            return
        entry = detail.entry
        kind = entry.summary.coordinate.kind

        yield Panel(KeyValueList(*identity_fields(entry)), title="Overview", hint="snapshot")

        specific = kind_fields(entry)
        if specific:
            yield Panel(
                KeyValueList(*specific),
                title=kind_section_title(kind),
                hint="snapshot",
            )

        dependencies = dependency_records(entry)
        if dependencies:
            yield Panel(
                Table(
                    columns=RELATIONSHIP_COLUMNS,
                    records=dependencies,
                    classes="overview-table",
                ),
                title="Dependencies",
                hint="snapshot",
            )

        consumers = consumer_records(entry)
        if consumers:
            yield Panel(
                Table(
                    columns=CONSUMER_COLUMNS,
                    records=consumers,
                    classes="overview-table",
                ),
                title="Used by",
                hint="snapshot",
            )

        structure = outline_fields(entry)
        if structure:
            yield Panel(KeyValueList(*structure), title="Structure", hint="snapshot · secret-free")

        source = detail.source
        yield Panel(
            DiffView(
                document=(
                    build_source_document(
                        source.value.content, "yaml", source.value.start_line, pretty=False
                    )
                    if source.state == "ready" and source.value is not None
                    else None
                ),
                id="overview-source",
            ),
            title="Authored",
            hint=source_subtitle(source),
        )

        effective = detail.effective
        yield Panel(
            KeyValueList(
                *(
                    effective_fields(effective.value)
                    if effective.state == "ready" and effective.value is not None
                    else ()
                )
            ),
            title="Effective",
            hint=effective_subtitle(effective),
        )
