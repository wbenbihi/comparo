"""Matrix-specific resource overview."""

from typing import ClassVar
from typing import override

from textual.app import ComposeResult

from comparo.tui4.components.list import Field
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.resource_presenters import matrix_dimensions
from comparo.tui4.models.resource_presenters import matrix_fields

from .base import BaseKindResourceDetails


class MatrixResourceDetails(BaseKindResourceDetails):
    """Declared Matrix structure with execution-cell boundary made explicit."""

    KINDS: ClassVar[frozenset[str]] = frozenset({"Matrix"})

    @property
    @override
    def fields(self) -> tuple[Field, ...]:
        """Return Matrix shape and consumer rows."""
        return matrix_fields(self.entry)

    @override
    def compose(self) -> ComposeResult:
        """Render declared dimensions without locally compiling execution cells."""
        if self.fields:
            yield KeyValueList(*self.fields)
        dimensions = matrix_dimensions(self.entry)
        yield Separator(label="Declared dimensions", align="left", classes="tight")
        if not dimensions:
            yield Hint("No declared axes or explicit case collection was found.")
            return
        yield Table(
            columns=(
                Column("name", "Name"),
                Column("role", "Role", width=10),
                Column("shape", "Declared shape"),
            ),
            records=tuple(
                Record(
                    f"{dimension.role}:{dimension.id}",
                    (dimension.id, dimension.role, dimension.shape),
                )
                for dimension in dimensions
            ),
            classes="resource-matrix-table",
        )
        yield Hint("Expanded cells are computed from the complete Compare scope.")
