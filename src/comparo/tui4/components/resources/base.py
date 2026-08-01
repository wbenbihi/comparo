"""Shared contract for kind-specific resource detail widgets."""

from typing import ClassVar
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical

from comparo.tui4.components.list import Field
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.resource import ResourceDetail
from comparo.tui4.models.resource_presenters import generic_fields


class BaseKindResourceDetails(Vertical):
    """Base widget for a resource kind's overview projection.

    Subclasses only select a pure field projection or add a focused structural
    component. Registration stays explicit in ``registry.py``.
    """

    KINDS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, resource: ResourceDetail) -> None:
        """Initialize the widget from immutable resource state."""
        super().__init__(classes="resource-kind-details")
        self._resource = resource

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
