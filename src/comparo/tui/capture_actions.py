"""Open the shared capture inspector from exact Analyze owner coordinates."""

from typing import Protocol
from typing import cast

from textual.app import App

from comparo.application import ComparisonResultRow
from comparo.application import ExecutionResultRow
from comparo.application import ResultProjection
from comparo.tui.backend import TuiBackend
from comparo.tui.components.captureinspector import CaptureInspector
from comparo.tui.evidence_access import EvidenceAccess


class _BackendOwner(Protocol):
    backend: TuiBackend


def inspect_capture(
    app: App[None],
    projection: ResultProjection,
    owner: ExecutionResultRow | ComparisonResultRow | None,
) -> None:
    """Preserve baseline/candidate identity, attempts, and exchanges in capture selection."""
    if owner is None:
        app.notify("Select a Request or occurrence first.")
        return
    references = (
        (owner.baseline, owner.candidate)
        if isinstance(owner, ComparisonResultRow)
        else (owner.reference,)
    )
    rows = [
        next(row for row in projection.executions if row.reference == reference)
        for reference in references
    ]
    captures = tuple(
        (
            (("Baseline" if index == 0 else "Candidate") + " · " if len(rows) == 2 else "")
            + f"{row.environment.resource_id} · {row.subject.resource_id} · "
            + f"attempt {message.attempt_ordinal + 1} / exchange {message.exchange_ordinal + 1} · "
            + message.reference.message.value,
            message.reference,
        )
        for index, row in enumerate(rows)
        for message in row.messages
    )
    backend = cast(_BackendOwner, app).backend
    if not captures or backend.artifact_root is None:
        app.notify("No persisted HTTP capture is available for this selection.", severity="warning")
        return
    app.push_screen(
        CaptureInspector(
            EvidenceAccess(backend.artifact_root, backend.launch.limits.report_archive.artifact),
            captures,
        )
    )
