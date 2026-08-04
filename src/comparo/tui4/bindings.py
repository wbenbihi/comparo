"""Central declarations for the terminal interface's default key bindings."""

from textual.binding import Binding
from textual.binding import BindingType

from comparo.tui4.types import ComparoScreen

FILTER_KEY = "/"
LEGEND_KEY = "l"
RELOAD_KEY = "ctrl+r"

PROJECT_VIEW_KEYS: dict[str, str] = {
    "overview": "o",
    "source": "s",
    "dependencies": "g",
}
PROJECT_RESOLUTION_KEY = "r"
EXECUTION_CONTEXT_KEY = "space"

RESOURCE_ACTION_KEYS: dict[str, str] = {
    "overview": "1",
    "authored": "2",
    "effective": "3",
    "resolved": "4",
    "use-in-compare": "u",
    "inspect-composition": "c",
    "plan-matrix-cells": "m",
}


def application_bindings() -> list[BindingType]:
    """Build global application and workspace bindings."""
    workspace_bindings = [
        Binding(str(index), f'workspace("{screen.value}")', screen.value, show=False)
        for index, screen in enumerate(ComparoScreen, start=1)
    ]
    return [Binding("q", "quit", "Quit"), *workspace_bindings]


def diff_view_bindings() -> list[BindingType]:
    """Build bindings for source and diff navigation."""
    return [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter,space", "toggle_fold", "Fold"),
        Binding("v", "toggle_view", "Unified/split"),
    ]


def filter_bindings() -> list[BindingType]:
    """Build bindings for the reusable filter input."""
    return [Binding("escape", "release", "Clear / close", show=False)]


def explorer_bindings() -> list[BindingType]:
    """Build bindings for the Explorer workspace."""
    return [
        Binding(FILTER_KEY, "focus_filter", "Filter", key_display=FILTER_KEY),
        Binding(LEGEND_KEY, "toggle_legend", "Legend", key_display=LEGEND_KEY),
        *(
            Binding(
                key,
                f'project_view("{view}")',
                view.replace("dependencies", "Graph").title(),
                key_display=key,
            )
            for view, key in PROJECT_VIEW_KEYS.items()
        ),
        Binding(
            PROJECT_RESOLUTION_KEY,
            "toggle_project_resolution",
            "Raw/resolved",
            key_display=PROJECT_RESOLUTION_KEY,
        ),
        Binding(
            EXECUTION_CONTEXT_KEY,
            "set_execution_environment",
            "Set context",
            key_display="Space",
            priority=True,
        ),
        Binding(RELOAD_KEY, "reload", "Reload", key_display="^r"),
    ]


def resource_detail_bindings() -> list[BindingType]:
    """Build configurable action bindings for the focused resource detail."""
    return [
        Binding(
            key,
            f'resource_action("{action_id}")',
            action_id.replace("-", " ").title(),
            show=False,
        )
        for action_id, key in RESOURCE_ACTION_KEYS.items()
    ]


def debug_bindings() -> list[BindingType]:
    """Build bindings for the component gallery."""
    return [Binding(FILTER_KEY, "focus_filter", "Filter", key_display=FILTER_KEY)]
