"""At-a-glance Request purpose, shape, inputs and linked contracts."""

from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content

from comparo.tui.components.inspector import InspectorColumns
from comparo.tui.components.inspector import InspectorSection
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.resourcelinks import ResourceLinksTable
from comparo.tui.components.typography import Hint
from comparo.tui.models.explorer import ResourceEntry
from comparo.tui.models.resource_presenters import RequestOverview
from comparo.tui.models.resource_presenters import resource_dependencies


class RequestSummary(Vertical):
    """Compact structural summary backed by the same projection as the HTTP tabs."""

    DEFAULT_CSS = "RequestSummary { width: 100%; height: auto; }"

    def __init__(self, entry: ResourceEntry, overview: RequestOverview) -> None:
        """Retain only the safe Request outline and its explicit relationships."""
        super().__init__()
        self._entry = entry
        self._overview = overview

    @override
    def compose(self) -> ComposeResult:
        """Expose what is configured without inferring a successful response or run."""
        overview = self._overview
        with InspectorColumns():
            yield InspectorSection(
                "Request shape",
                KeyValueList(
                    Field("Method", Content(self._entry.facts.method or "Not declared")),
                    Field("Declared target", Content(self._entry.facts.route or "Not declared")),
                    Field(
                        "Body",
                        Content(
                            ", ".join(item.name for item in overview.payload) or "No body declared"
                        ),
                    ),
                    Field("Runtime", f"{len(overview.behavior)} declared controls"),
                ),
            )
            yield InspectorSection(
                "Declared inputs",
                KeyValueList(
                    Field("Parameters", str(len(overview.parameters))),
                    Field("Query fields", str(len(overview.query))),
                    Field("Headers", str(len(overview.headers))),
                    Field("Cookies", str(len(overview.cookies))),
                    Field(
                        "Authentication", "Declared" if overview.authentication else "Not declared"
                    ),
                ),
            )
        edges = resource_dependencies(self._entry)
        yield InspectorSection(
            "Variants, contracts & consumers",
            ResourceLinksTable(edges, directions=True)
            if edges
            else Hint("No resource relationships are declared."),
        )
        yield Hint("Open an HTTP tab for field details; r switches the value layer to resolved.")
