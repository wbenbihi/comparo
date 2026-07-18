"""A structured run report and the reporter port that renders it.

The engine produces a :class:`RunReport`; adapters (or plugins) render it to
whatever format CI wants. The core stays format-agnostic — it never imports a
reporter, only the :class:`Reporter` protocol.
"""

import dataclasses
from collections.abc import Callable
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
        """Whether the run passes the gate (fail-closed on an empty run)."""
        return diff_passed(len(self.cells), self.drift, self.errors)


def diff_passed(calls: int, drift: int, errors: int) -> bool:
    """Whether a diff run passes its gate.

    A run that compared nothing (``calls == 0``) verified nothing, so it can never
    be a pass — it fails closed, mirroring :attr:`ExecutionResult.passed`.
    """
    return calls > 0 and drift == 0 and errors == 0


def diff_gate(calls: int, drift: int, errors: int) -> str:
    """The tri-state gate verdict (``PASS`` / ``FAIL`` / ``ERROR``) for a diff run."""
    if errors:
        return "ERROR"
    return "PASS" if diff_passed(calls, drift, errors) else "FAIL"


class Reporter(Protocol):
    """Renders a :class:`RunReport` to a string, written to ``filename``."""

    filename: str

    def render(self, report: RunReport) -> str:
        """Render *report* to a string."""
        ...


def build_report(
    baseline: str,
    candidate: str,
    cells: list[CellDiff],
    redact: Callable[[str], str] = str,
) -> RunReport:
    """Build a :class:`RunReport` from diff results.

    Args:
        baseline: The baseline environment name.
        candidate: The candidate environment name.
        cells: The per-cell diff results.
        redact: Masks known secret values in drift details / error text before
            they leave the process (a server can echo a secret into a drifted
            field). Defaults to ``str`` (identity) when there is nothing to mask.

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
        # Redact the path too: a server can echo a secret as a JSON key, which
        # becomes a field path — masking only the value would still leak it.
        drifts = [
            DriftEntry(redact(field.path), redact(field.detail), field.mode)
            for field in cell.drifts
        ]
        entries.append(
            CellReport(
                request_id=redact(cell.request.metadata.id or cell.request.metadata.name),
                # A matrix case value can equal a declared secret; the case key
                # (``token=<value>``) then carries it, so mask it like the paths.
                cell_key=redact(cell.cell_key),
                state=state,
                drifts=drifts,
                skipped=cell.skipped,
                error=redact(cell.error) if cell.error is not None else None,
            )
        )
    # Redact env names too: JSON/Markdown reporters echo them, and on the vanishing
    # chance a name equals a declared secret the whole-value backstop masks it.
    return RunReport(redact(baseline), redact(candidate), entries)
