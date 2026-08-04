"""Central declarations for the terminal interface's default key bindings."""

from textual.binding import Binding
from textual.binding import BindingType

from comparo.tui4.types import ComparoScreen

FILTER_KEY = "/"
LEGEND_KEY = "l"
RELOAD_KEY = "ctrl+r"

RESOURCE_VIEW_KEYS: dict[str, str] = {
    "overview": "o",
    "source": "s",
    "dependencies": "g",
}
RESOURCE_RESOLUTION_KEY = "r"
EXECUTION_CONTEXT_KEY = "space"
HEALTH_CHECK_KEY = "h"

RESOURCE_ACTION_KEYS: dict[str, str] = {
    "overview": "1",
    "authored": "2",
    "effective": "3",
    "resolved": "4",
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
                f'resource_view("{view}")',
                view.replace("dependencies", "Graph").title(),
                key_display=key,
            )
            for view, key in RESOURCE_VIEW_KEYS.items()
        ),
        Binding(
            RESOURCE_RESOLUTION_KEY,
            "toggle_resource_resolution",
            "Raw/resolved",
            key_display=RESOURCE_RESOLUTION_KEY,
        ),
        Binding(
            EXECUTION_CONTEXT_KEY,
            "set_execution_environment",
            "Set context",
            key_display="Space",
            priority=True,
        ),
        Binding(
            HEALTH_CHECK_KEY,
            "run_health_check",
            "Health check",
            key_display=HEALTH_CHECK_KEY,
        ),
        Binding(RELOAD_KEY, "reload", "Reload", key_display="^r"),
    ]


# Compatibility aliases for callers that imported the original Project-only names.
PROJECT_VIEW_KEYS = RESOURCE_VIEW_KEYS
PROJECT_RESOLUTION_KEY = RESOURCE_RESOLUTION_KEY


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
