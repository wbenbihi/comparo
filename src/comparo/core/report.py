"""A structured run report and the reporter port that renders it.

The engine produces a :class:`RunReport`; adapters (or plugins) render it to
whatever format CI wants. The core stays format-agnostic — it never imports a
reporter, only the :class:`Reporter` protocol.
"""

import dataclasses
from typing import Protocol

from comparo.core.compare import CellDiff


@dataclasses.dataclass(frozen=True, slots=True)
class DriftEntry:
    """One drifted path within a cell."""

    path: str
    detail: str
    mode: str


@dataclasses.dataclass(frozen=True, slots=True)
class CellReport:
    """The report entry for one request cell."""

    request_id: str
    cell_key: str
    state: str
    drifts: list[DriftEntry]
    skipped: int
    error: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class RunReport:
    """A whole diff run, ready to render or serialize."""

    baseline: str
    candidate: str
    cells: list[CellReport]

    @property
    def same(self) -> int:
        """How many cells matched."""
        return sum(1 for cell in self.cells if cell.state == "same")

    @property
    def drift(self) -> int:
        """How many cells drifted."""
        return sum(1 for cell in self.cells if cell.state == "drift")

    @property
    def errors(self) -> int:
        """How many cells errored."""
        return sum(1 for cell in self.cells if cell.state == "error")

    @property
    def skipped(self) -> int:
        """The total number of deliberately-skipped fields."""
        return sum(cell.skipped for cell in self.cells)

    @property
    def passed(self) -> bool:
        """Whether the run passes the gate (no drift, no errors)."""
        return self.drift == 0 and self.errors == 0


class Reporter(Protocol):
    """Renders a :class:`RunReport` to a string, written to ``filename``."""

    filename: str

    def render(self, report: RunReport) -> str:
        """Render *report* to a string."""
        ...


def build_report(baseline: str, candidate: str, cells: list[CellDiff]) -> RunReport:
    """Build a :class:`RunReport` from diff results.

    Args:
        baseline: The baseline environment name.
        candidate: The candidate environment name.
        cells: The per-cell diff results.

    Returns:
        The structured run report.
    """
    entries: list[CellReport] = []
    for cell in cells:
        if cell.error is not None:
            state = "error"
        elif cell.drifted:
            state = "drift"
        else:
            state = "same"
        drifts = [DriftEntry(field.path, field.detail, field.mode) for field in cell.drifts]
        entries.append(
            CellReport(
                request_id=cell.request.metadata.id or cell.request.metadata.name,
                cell_key=cell.cell_key,
                state=state,
                drifts=drifts,
                skipped=cell.skipped,
                error=cell.error,
            )
        )
    return RunReport(baseline, candidate, entries)
