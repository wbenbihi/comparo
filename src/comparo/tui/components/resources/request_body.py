"""Request body document with a synchronized focused-path inspector."""

from collections.abc import Mapping
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content

from comparo.tui.components.actionbar import Action
from comparo.tui.components.actionbar import ActionBar
from comparo.tui.components.inspector import InspectorColumns
from comparo.tui.components.inspector import InspectorSection
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.table import Table
from comparo.tui.components.typography import Caption
from comparo.tui.components.typography import Hint
from comparo.tui.models.body_document import body_document
from comparo.tui.models.body_document import json_path
from comparo.tui.models.explorer import ResourceEntry
from comparo.tui.models.resource import ResolvedResourceField
from comparo.tui.theme import style


class RequestBody(Vertical):
    """Numbered JSON lines and field facts using the shared navigation components."""

    DEFAULT_CSS = """
    RequestBody { height: auto; }
    RequestBody Table { height: 24; min-height: 8; }
    RequestBody Table > .datatable--header { display: none; }
    """

    def __init__(
        self, entry: ResourceEntry, resolved: Mapping[tuple[str, ...], ResolvedResourceField]
    ) -> None:
        """Receive only application-projected fields and source metadata."""
        super().__init__(classes="request-tab-body")
        self.entry = entry
        self.lines = body_document(entry, resolved)
        body_type = resolved.get(("request", "bodyType"))
        self.content_type = (
            "application/json" if body_type and body_type.value == "json" else "not resolved"
        )

    @override
    def compose(self) -> ComposeResult:
        if not self.lines:
            yield Hint("This Request has no body.", classes="request-tab-empty")
            return
        yield Caption("↑/↓ Navigate body · select a line to inspect its path")
        with InspectorColumns():
            table = Table(
                columns=(Column("line", "", width=4), Column("document", "Body")),
                records=tuple(
                    Record(
                        str(index),
                        (
                            str(index + 1),
                            Content.from_rich_text(Text(line.text, style(line.tone or "fg"))),
                        ),
                    )
                    for index, line in enumerate(self.lines)
                ),
                id="request-body-document",
            )
            table.show_header = False
            table.zebra_stripes = False
            yield table
            yield InspectorSection(
                "Focused path", KeyValueList(*self._facts(0), id="request-body-path")
            )
        masked = len({line.path for line in self.lines if line.tone == "value.masked"})
        size = len("\n".join(line.text for line in self.lines).encode())
        yield Caption(
            f"Content-Type {self.content_type} · Masked fields {masked} · "
            f"Schema: not checked · Preview size {size} B"
        )
        yield ActionBar((Action("copy-body", "Copy masked body"),))

    def _facts(self, index: int) -> tuple[Field, ...]:
        line = self.lines[index]
        source = self.entry.source.filename or self.entry.summary.source.source
        origins = tuple(
            origin.replace(self.entry.summary.source.source, source) for origin in line.origins
        )
        return (
            Field("Path", Content(json_path(line.path))),
            Field("Value", Content.from_rich_text(Text(line.value, style(line.tone or "fg")))),
            Field("Directive", Content(line.directive)),
            Field(
                "Declaration",
                Content("\n".join(origins) or f"{source}:{self.entry.summary.source.line}"),
            ),
            Field("Origin order", Content(" → ".join(origins) or "Resource declaration")),
        )

    def on_table_row_highlighted(self, event: Table.RowHighlighted[str]) -> None:
        """Follow keyboard and pointer selection without changing resource context."""
        event.stop()
        self.query_one("#request-body-path", KeyValueList).entries = self._facts(int(event.row))

    def on_action_bar_requested(self, event: ActionBar.Requested) -> None:
        """Copy only the already masked document visible in the inspector."""
        event.stop()
        if event.action == "copy-body":
            self.app.copy_to_clipboard("\n".join(line.text for line in self.lines))
            self.notify("Masked body copied")
