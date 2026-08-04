"""Shared contract for kind-specific resource detail widgets."""

from __future__ import annotations

from typing import ClassVar
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message

from comparo.tui4.components.list import Field
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.resource import ResourceDetail
from comparo.tui4.models.resource import ResourcePresentation
from comparo.tui4.models.resource_presenters import generic_fields


class BaseKindResourceDetails(Vertical):
    """Base widget for a resource kind's overview projection.

    Subclasses only select a pure field projection or add a focused structural
    component. Registration stays explicit in ``registry.py``.
    """

    KINDS: ClassVar[frozenset[str]] = frozenset()
    PRESENTATION: ClassVar[ResourcePresentation] = ResourcePresentation()

    class SectionChanged(Message):
        """A kind widget selected a reusable internal overview section."""

        def __init__(self, widget: BaseKindResourceDetails, section: str) -> None:
            """Initialize one bounded semantic section selection."""
            if not section or len(section) > 64 or not section.isprintable():
                raise ValueError("resource overview section must be bounded printable text")
            self.widget = widget
            self.kind = widget.entry.summary.coordinate.kind
            self.section = section
            super().__init__()

        @property
        @override
        def control(self) -> BaseKindResourceDetails:
            """Return the kind widget that owns the selected section."""
            return self.widget

    def __init__(self, resource: ResourceDetail, *, section: str | None = None) -> None:
        """Initialize the widget from immutable resource state."""
        super().__init__(classes="resource-kind-details")
        self._resource = resource
        self._section = section

    @property
    def resource(self) -> ResourceDetail:
        """Return the complete immutable resource state."""
        return self._resource

    @property
    def entry(self) -> ResourceEntry:
        """Return the selected catalog entry."""
        return self.resource.entry

    @property
    def entries(self) -> ResourceEntry:
        """Keep the original read-only accessor while callers migrate to ``entry``."""
        return self.entry

    @property
    def initial_section(self) -> str | None:
        """Return the host-retained section for this resource kind, when available."""
        return self._section

    def select_section(self, section: str) -> None:
        """Publish one semantic section selection without owning host state."""
        if section != self._section:
            self._section = section
            self.post_message(self.SectionChanged(self, section))

    @property
    def fields(self) -> tuple[Field, ...]:
        """Return the safe generic structural fallback."""
        return generic_fields(self.entry)

    @override
    def compose(self) -> ComposeResult:
        """Render aligned fields or an explicit empty-state hint."""
        if self.fields:
            yield KeyValueList(*self.fields)
        else:
            yield Hint("No additional structural information is available.")
