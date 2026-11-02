"""Responsive layout for the shared Analyze evidence panels."""

from collections.abc import Sequence

from textual.content import Content
from textual.widget import Widget

from comparo.tui.components.diffview import DiffView
from comparo.tui.components.table import Table
from comparo.tui.components.typography import Caption


def body_labels(view: Widget, labels: Sequence[Content]) -> None:
    """Keep comparison identity visible in split and unified presentations."""
    sided = len(labels) == 2
    split = view.query_one(DiffView).view_mode == "split"
    left = labels[0] if labels else Content("")
    if sided and not split:
        left = Content("Unified diff · ") + labels[0] + Content(" → ") + labels[1]
    view.query_one("#result-body-left", Caption).update(left)
    view.query_one("#result-body-right", Caption).update(labels[1] if sided else "")
    view.query_one("#result-body-right").display = sided and split
    view.query_one("#result-body-labels").set_class(not sided or not split, "single-body")


def fit_tables(view: Widget, lens: str) -> None:
    """Distribute available space by the active lens's information hierarchy."""
    table = view.query_one("#result-related", Table)
    table.fit_proportions(
        (0.36, 0.17, 0.17, 0.17, 0.13) if lens == "requests" else (0.28, 0.24, 0.16, 0.13, 0.19)
    )
    index = view.query_one("#result-index", Table)
    shares = {
        "issues": (0.66, 0.34),
        "requests": (0.37, 0.17, 0.17, 0.17, 0.12),
        "rules": (0.6, 0.4),
        "fields": (0.63, 0.37),
        "timeline": (0.45, 0.33, 0.22),
    }[lens]
    index.fit_proportions(shares)
