"""Shared glyph and label grammar for transport, rule and gate states."""

from collections import Counter
from collections.abc import Iterable

from rich.text import Text
from textual.content import Content

from comparo.tui.theme import style

_STATES = {
    "queued": ("·", "Queued", "queued"),
    "running": ("◐", "Running", "running"),
    "observed": ("✓", "Completed", "passed"),
    "complete": ("✓", "Complete", "passed"),
    "completed": ("✓", "Completed", "passed"),
    "passed": ("✓", "Passed", "passed"),
    "satisfied": ("✓", "Satisfied", "passed"),
    "equivalent": ("✓", "Equivalent", "passed"),
    "evaluated": ("✓", "Evaluated", "passed"),
    "violated": ("✗", "Violated", "failed"),
    "drifted": ("Δ", "Drifted", "drift"),
    "failed": ("✗", "Failed", "failed"),
    "error": ("!", "Error", "error"),
    "unavailable": ("!", "Unavailable", "error"),
    "blocked": ("!", "Blocked", "error"),
    "incomplete": ("!", "Incomplete", "advisory"),
    "partial": ("!", "Partial", "advisory"),
    "rejected": ("!", "Rejected", "error"),
    "internal-error": ("!", "Internal error", "error"),
    "advisory": ("~", "Advisory", "advisory"),
    "skipped": ("⊘", "Skipped", "skipped"),
    "cancelled": ("⊘", "Cancelled", "cancelled"),
    "not-evaluated": ("—", "Not evaluated", "queued"),
    "pending": ("·", "Pending", "queued"),
}


def status_text(value: str) -> Text:
    """Preserve the producer's state with a non-color-only semantic label."""
    character, label, token = _STATES.get(value, ("·", value.replace("-", " ").title(), "queued"))
    return Text(f"{character} {label}", style(f"status.{token}"))


def status_cell(value: str) -> Content:
    """Render status in the shared record table without parsing markup."""
    return Content.from_rich_text(status_text(value))


def fact_counts(outcomes: Iterable[str]) -> Content:
    """Count exact retained fact outcomes without deriving a new verdict."""
    counts = Counter(outcomes)
    if not counts:
        return status_cell("pending")
    text = Text()
    for state, count in counts.items():
        if text.plain:
            text.append(" · ")
        text.append(f"{count} ")
        text.append_text(status_text(state))
    return Content.from_rich_text(text)


def duration(nanoseconds: int | None) -> str:
    """Show measured duration without turning absence into zero."""
    if nanoseconds is None:
        return "—"
    return (
        f"{nanoseconds / 1_000_000:.0f} ms"
        if nanoseconds < 1_000_000_000
        else f"{nanoseconds / 1_000_000_000:.2f} s"
    )


def compact_facts(outcomes: Iterable[str]) -> Content:
    """Short exact fact counters for aligned comparison columns, not a new verdict."""
    labels = {
        "satisfied": ("✓", "held", "ok"),
        "equivalent": ("✓", "same", "ok"),
        "violated": ("✗", "broken", "err"),
        "drifted": ("Δ", "drift", "err"),
        "error": ("!", "error", "error-op"),
        "not-evaluated": ("—", "N/E", "muted"),
    }
    counts = Counter(outcomes)
    text = Text()
    for outcome, count in counts.items():
        mark, label, token = labels.get(outcome, ("·", outcome, "muted"))
        if text.plain:
            text.append(" · ")
        text.append(f"{mark} {count} {label}", style(token))
    return Content.from_rich_text(text) if counts else Content("—")
