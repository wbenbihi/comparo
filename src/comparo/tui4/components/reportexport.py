"""Explicit destination and format selection for a saved report export."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.screen import ModalScreen

from comparo.tui4.components.actionbar import Action
from comparo.tui4.components.actionbar import ActionBar
from comparo.tui4.components.menu import MenuItem
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.selectfield import SelectField
from comparo.tui4.components.typography import Caption
from comparo.tui4.components.valueinput import ValueInput


@dataclass(frozen=True)
class ExportDestination:
    """The user's selected output; the backend owns validation and writing."""

    path: Path
    format: str


class ReportExport(ModalScreen[ExportDestination | None]):
    """Choose a new file without silently overwriting an existing export."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    ReportExport { align: center middle; background: $background 80%; }
    ReportExport > Panel { width: 84; max-width: 95%; height: auto; padding: 1 2; }
    ReportExport SelectField { margin: 1 0; }
    ReportExport Caption { height: auto; margin-bottom: 1; }
    ReportExport ActionBar { margin-top: 1; }
    """

    def __init__(self, destination: Path) -> None:
        """Retain the suggested path until the editor is mounted."""
        super().__init__()
        self.destination = destination

    @override
    def compose(self) -> ComposeResult:
        yield Panel(
            Caption("Export this saved report. Existing files are never overwritten."),
            SelectField(
                "Format",
                tuple(
                    MenuItem(value, label)
                    for value, label in (
                        ("json", "JSON"),
                        ("markdown", "Markdown"),
                        ("junit", "JUnit XML"),
                        ("sarif", "SARIF"),
                    )
                ),
                value="json",
            ),
            ValueInput("Destination", maximum_length=4096),
            ActionBar(
                (Action("save", "Export", primary=True), Action("cancel", "Cancel", key="Esc"))
            ),
            title="EXPORT REPORT",
        )

    def on_mount(self) -> None:
        """Expose the exact destination before any write is requested."""
        self.query_one(ValueInput).value = str(self.destination)
        self.query_one(ValueInput).focus()

    @on(SelectField.Changed)
    def _format_changed(self, event: SelectField.Changed) -> None:
        event.stop()
        field = self.query_one(ValueInput)
        if field.value:
            extension = {"json": ".json", "markdown": ".md", "junit": ".xml", "sarif": ".sarif"}[
                event.value
            ]
            field.value = str(Path(field.value).with_suffix(extension))

    @on(ActionBar.Requested)
    def _action(self, event: ActionBar.Requested) -> None:
        event.stop()
        if event.action == "cancel":
            self.action_cancel()
        else:
            value = self.query_one(ValueInput).value.strip()
            if value:
                self.dismiss(ExportDestination(Path(value), self.query_one(SelectField).value))

    def action_cancel(self) -> None:
        """Close without exporting."""
        self.dismiss(None)
