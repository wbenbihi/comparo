"""WorkspaceScreen — the contract every tui3 workspace fulfils."""

from __future__ import annotations

from textual.containers import Vertical


class WorkspaceScreen(Vertical):
    """Base workspace: chrome info + key routing hooks."""

    def crumb(self) -> str:
        """Navrow crumb."""
        return ""

    def tier(self) -> str:
        """Navrow tier badge."""
        return ""

    def hints(self) -> list[tuple[str, str]]:
        """Footer keys."""
        return [("q", "quit")]

    def focus_default(self) -> None:
        """Focus the natural widget."""

    def on_ws_key(self, key: str) -> bool:
        """Try a workspace key; return True when handled."""
        return False
