"""Shared bounded body presentation for live and archived Analyze views."""

from collections.abc import Sequence

from comparo.application import ArtifactBodyWindow
from comparo.tui4.models.diff import DiffDocument
from comparo.tui4.models.diff import build_diff_document
from comparo.tui4.models.diff import build_source_document


def body_preview(windows: Sequence[ArtifactBodyWindow]) -> tuple[DiffDocument, str]:
    """Render authenticated captures with explicit coverage and literal terminal-safe text."""
    if len(windows) not in {1, 2}:
        raise ValueError("body preview requires one or two windows")
    texts = [window.content.decode("utf-8", errors="replace") for window in windows]
    texts = [
        "".join(char if char.isprintable() or char in "\n\t" else "�" for char in text)
        for text in texts
    ]
    document = build_diff_document(*texts) if len(texts) == 2 else build_source_document(texts[0])
    message = " · ".join(
        f"{window.reference.message.value}: {window.source_state}/{window.extent}"
        f" · {len(window.content)} of "
        f"{window.total_bytes if window.total_bytes is not None else '?'} bytes"
        f" · {window.redaction_count} redactions"
        + (f" · {window.reason}" if window.reason else "")
        + (" · preview truncated at 64 KiB" if window.next_offset is not None else "")
        for window in windows
    )
    return document, message
