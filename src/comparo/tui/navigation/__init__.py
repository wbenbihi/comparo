"""Typed workspace and local-route navigation."""

from comparo.tui.navigation.routes import NavigationFrame as NavigationFrame
from comparo.tui.navigation.routes import NavigationHistory as NavigationHistory
from comparo.tui.navigation.routes import NavigationState as NavigationState
from comparo.tui.navigation.routes import Route as Route
from comparo.tui.navigation.routes import WorkspaceId as WorkspaceId
from comparo.tui.navigation.routes import initial_navigation as initial_navigation
from comparo.tui.navigation.routes import navigate_back as navigate_back
from comparo.tui.navigation.routes import push_route as push_route
from comparo.tui.navigation.routes import switch_workspace as switch_workspace

__all__ = (
    "NavigationFrame",
    "NavigationHistory",
    "NavigationState",
    "Route",
    "WorkspaceId",
    "initial_navigation",
    "navigate_back",
    "push_route",
    "switch_workspace",
)
