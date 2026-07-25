"""Workspace-preserving local navigation contracts."""

from __future__ import annotations

from typing import Any
from typing import cast

import msgspec
import pytest

from comparo.tui.navigation.routes import NavigationFrame
from comparo.tui.navigation.routes import NavigationHistory
from comparo.tui.navigation.routes import NavigationState
from comparo.tui.navigation.routes import Route
from comparo.tui.navigation.routes import WorkspaceId
from comparo.tui.navigation.routes import initial_navigation
from comparo.tui.navigation.routes import navigate_back
from comparo.tui.navigation.routes import push_route
from comparo.tui.navigation.routes import switch_workspace


def test_each_workspace_retains_an_independent_local_stack() -> None:
    state = initial_navigation()
    report = NavigationFrame(
        route=Route(workspace=WorkspaceId.REPORT, view="result", entity="run-123"),
        focus_key="execution:abc",
        scroll_key="row:abc",
    )
    state = push_route(state, report, maximum_frames_per_workspace=8)
    assert state.active is WorkspaceId.REPORT
    assert state.current == report

    explorer = NavigationFrame(
        route=Route(workspace=WorkspaceId.EXPLORER, view="resource", entity="Request/ping")
    )
    state = push_route(state, explorer, maximum_frames_per_workspace=8)
    assert state.active is WorkspaceId.EXPLORER
    assert state.current == explorer

    state = switch_workspace(state, WorkspaceId.REPORT)
    assert state.current == report
    state = navigate_back(state)
    assert state.current.route.view == "root"
    assert navigate_back(state) is state
    assert switch_workspace(state, WorkspaceId.REPORT) is state


def test_navigation_deduplicates_current_route_and_retains_bounded_recency() -> None:
    state = initial_navigation()
    first = NavigationFrame(
        route=Route(workspace=WorkspaceId.EXPLORER, view="resource", entity="Request/one")
    )
    state = push_route(state, first, maximum_frames_per_workspace=3)
    assert push_route(state, first, maximum_frames_per_workspace=3) is state

    for resource_id in ("two", "three", "four"):
        state = push_route(
            state,
            NavigationFrame(
                route=Route(
                    workspace=WorkspaceId.EXPLORER,
                    view="resource",
                    entity=f"Request/{resource_id}",
                )
            ),
            maximum_frames_per_workspace=3,
        )

    history = next(item for item in state.histories if item.workspace is WorkspaceId.EXPLORER)
    assert tuple(frame.route.entity for frame in history.frames) == (
        None,
        "Request/three",
        "Request/four",
    )


def test_navigation_models_reject_invalid_shapes() -> None:
    root = Route(workspace=WorkspaceId.EXPLORER, view="root")
    frame = NavigationFrame(route=root)
    history = NavigationHistory(workspace=WorkspaceId.EXPLORER, frames=(frame,))
    state = initial_navigation()

    with pytest.raises(TypeError):
        switch_workspace(state, cast("Any", "explorer"))
    with pytest.raises(TypeError):
        push_route(state, cast("Any", object()), maximum_frames_per_workspace=8)
    with pytest.raises(ValueError, match="root and detail"):
        push_route(state, frame, maximum_frames_per_workspace=1)
    with pytest.raises(ValueError, match="root and detail"):
        push_route(state, frame, maximum_frames_per_workspace=True)
    with pytest.raises(TypeError):
        switch_workspace(cast("Any", object()), WorkspaceId.EXPLORER)
    with pytest.raises(TypeError):
        navigate_back(cast("Any", object()))

    with pytest.raises(TypeError, match="workspace must be typed"):
        Route(workspace="explorer", view="root")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lowercase token"):
        msgspec.structs.replace(root, view="Not a route")
    with pytest.raises(ValueError, match="non-control"):
        msgspec.structs.replace(root, entity="bad\x1b")
    with pytest.raises(TypeError, match="frame route"):
        NavigationFrame(route=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="restoration"):
        msgspec.structs.replace(frame, focus_key="")
    with pytest.raises(TypeError, match="workspace must be typed"):
        NavigationHistory(workspace="explorer", frames=(frame,))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="typed immutable frames"):
        NavigationHistory(workspace=WorkspaceId.EXPLORER, frames=())
    with pytest.raises(ValueError, match="belong"):
        NavigationHistory(
            workspace=WorkspaceId.REPORT,
            frames=(frame,),
        )
    with pytest.raises(TypeError, match="active workspace"):
        msgspec.structs.replace(state, active="explorer")
    with pytest.raises(TypeError, match="immutable typed tuple"):
        msgspec.structs.replace(state, histories=[history])
    with pytest.raises(ValueError, match="exactly once"):
        NavigationState(active=WorkspaceId.EXPLORER, histories=(history,))
