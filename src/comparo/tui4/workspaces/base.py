"""Shared base class for top-level workspaces."""

from typing import ClassVar
from typing import Protocol
from typing import cast

from textual.containers import Vertical

from comparo.tui4.backend import TuiBackend
from comparo.tui4.types import ComparoScreen


class _BackendOwner(Protocol):
    """Minimal application shape required by a workspace."""

    backend: TuiBackend


class Workspace(Vertical):
    """Base for all main-area workspaces."""

    SCREEN: ClassVar[ComparoScreen]

    def __init__(self) -> None:
        """Initialize the workspace with its stable screen identifier."""
        super().__init__(id=self.SCREEN, classes="Workspace")

    @property
    def backend(self) -> TuiBackend:
        """The process backend owned by the app."""
        return cast(_BackendOwner, self.app).backend

    def on_show(self) -> None:
        """Restore the workspace's natural focus when it becomes visible."""
        self.focus_default()

    def focus_default(self) -> None:
        """Focus the natural widget; override per workspace."""
