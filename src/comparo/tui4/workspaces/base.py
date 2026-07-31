from typing import cast
from typing import ClassVar

from textual.app import App
from textual.containers import Vertical

from comparo.tui4.types import ComparoScreen
from comparo.tui4.backend import TuiBackend


class Workspace(Vertical):
    """Base for all main-area workspaces."""

    SCREEN: ClassVar[ComparoScreen]

    def __init__(self, backend: TuiBackend | None = None) -> None:
        """Init the workspace."""
        super().__init__(id=self.SCREEN, classes="Workspace")
        self._backend = backend

    @property
    def backend(self) -> TuiBackend:
        """The process backend owned by the app."""
        self.app: App[None]
        return cast(TuiBackend, getattr(self.app, "backend"))

    def on_show(self) -> None:
        """On Show Callback."""
        self.focus_default()

    def focus_default(self) -> None:
        """Focus the natural widget; override per workspace."""
