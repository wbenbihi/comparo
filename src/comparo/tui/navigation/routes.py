"""Pure immutable navigation with one retained history per workspace."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import final

import msgspec

_ROUTE_TOKEN = re.compile(r"[a-z][a-z0-9-]{0,62}")


class WorkspaceId(StrEnum):
    """The four persistent top-level Comparo workspaces."""

    EXPLORER = "explorer"
    COMPARE = "compare"
    REPORT = "report"
    SETTINGS = "settings"


@final
class Route(msgspec.Struct, frozen=True, kw_only=True):
    """One local workspace destination carrying only stable semantic keys."""

    workspace: WorkspaceId
    view: str
    entity: str | None = None

    def __post_init__(self) -> None:
        """Reject free-form paths and payload-bearing route identities."""
        if type(self.workspace) is not WorkspaceId:
            raise TypeError("route workspace must be typed")
        if type(self.view) is not str or _ROUTE_TOKEN.fullmatch(self.view) is None:
            raise ValueError("route view must be a bounded lowercase token")
        if self.entity is not None and (
            type(self.entity) is not str
            or not self.entity
            or len(self.entity) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in self.entity)
        ):
            raise ValueError("route entity must be bounded non-control text or absent")


@final
class NavigationFrame(msgspec.Struct, frozen=True, kw_only=True):
    """A local route plus semantic focus restoration keys."""

    route: Route
    focus_key: str | None = None
    scroll_key: str | None = None

    def __post_init__(self) -> None:
        """Require a typed route and bounded restoration keys."""
        if type(self.route) is not Route:
            raise TypeError("navigation frame route must be typed")
        for value in (self.focus_key, self.scroll_key):
            if value is not None and (type(value) is not str or not value or len(value) > 128):
                raise ValueError("navigation restoration keys must be bounded text or absent")


@final
class NavigationHistory(msgspec.Struct, frozen=True, kw_only=True):
    """One non-empty local route stack owned by a workspace."""

    workspace: WorkspaceId
    frames: tuple[NavigationFrame, ...]

    def __post_init__(self) -> None:
        """Require a non-empty, workspace-consistent immutable stack."""
        if type(self.workspace) is not WorkspaceId:
            raise TypeError("navigation history workspace must be typed")
        if (
            type(self.frames) is not tuple
            or not self.frames
            or any(type(frame) is not NavigationFrame for frame in self.frames)
        ):
            raise ValueError("navigation history requires typed immutable frames")
        if any(frame.route.workspace is not self.workspace for frame in self.frames):
            raise ValueError("navigation history frames must belong to their workspace")

    @property
    def current(self) -> NavigationFrame:
        """Return the active local frame."""
        return self.frames[-1]


@final
class NavigationState(msgspec.Struct, frozen=True, kw_only=True):
    """The active workspace plus one independent local stack for each tab."""

    active: WorkspaceId
    histories: tuple[NavigationHistory, ...]

    def __post_init__(self) -> None:
        """Require exact closed workspace coverage."""
        if type(self.active) is not WorkspaceId:
            raise TypeError("active workspace must be typed")
        if type(self.histories) is not tuple or any(
            type(history) is not NavigationHistory for history in self.histories
        ):
            raise TypeError("navigation histories must be an immutable typed tuple")
        workspaces = tuple(history.workspace for history in self.histories)
        if len(set(workspaces)) != len(workspaces) or set(workspaces) != set(WorkspaceId):
            raise ValueError("navigation state must contain each workspace exactly once")

    @property
    def current(self) -> NavigationFrame:
        """Return the active workspace's current local frame."""
        return _history(self, self.active).current


def initial_navigation() -> NavigationState:
    """Create the canonical four-workspace root navigation state."""
    return NavigationState(
        active=WorkspaceId.EXPLORER,
        histories=tuple(
            NavigationHistory(
                workspace=workspace,
                frames=(
                    NavigationFrame(
                        route=Route(workspace=workspace, view="root"),
                    ),
                ),
            )
            for workspace in WorkspaceId
        ),
    )


def switch_workspace(state: NavigationState, workspace: WorkspaceId) -> NavigationState:
    """Switch top-level context without discarding either local stack."""
    _require_state(state)
    if type(workspace) is not WorkspaceId:
        raise TypeError("workspace switch requires WorkspaceId")
    return state if state.active is workspace else msgspec.structs.replace(state, active=workspace)


def push_route(
    state: NavigationState,
    frame: NavigationFrame,
    *,
    maximum_frames_per_workspace: int,
) -> NavigationState:
    """Push one frame while retaining a caller-bounded root-and-recency history."""
    _require_state(state)
    if type(frame) is not NavigationFrame:
        raise TypeError("navigation push requires NavigationFrame")
    if type(maximum_frames_per_workspace) is not int or maximum_frames_per_workspace < 2:
        raise ValueError("navigation push requires room for root and detail frames")
    history = _history(state, frame.route.workspace)
    if history.frames[-1] == frame:
        return switch_workspace(state, frame.route.workspace)
    frames = (*history.frames, frame)
    if len(frames) > maximum_frames_per_workspace:
        frames = (frames[0], *frames[-(maximum_frames_per_workspace - 1) :])
    replacement = msgspec.structs.replace(history, frames=frames)
    return NavigationState(
        active=frame.route.workspace,
        histories=tuple(
            replacement if item.workspace is frame.route.workspace else item
            for item in state.histories
        ),
    )


def navigate_back(state: NavigationState) -> NavigationState:
    """Pop the active local frame while preserving its root."""
    _require_state(state)
    history = _history(state, state.active)
    if len(history.frames) == 1:
        return state
    replacement = msgspec.structs.replace(history, frames=history.frames[:-1])
    return msgspec.structs.replace(
        state,
        histories=tuple(
            replacement if item.workspace is state.active else item for item in state.histories
        ),
    )


def _history(state: NavigationState, workspace: WorkspaceId) -> NavigationHistory:
    return next(history for history in state.histories if history.workspace is workspace)


def _require_state(state: NavigationState) -> None:
    if type(state) is not NavigationState:
        raise TypeError("navigation operation requires NavigationState")


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
