"""InspectorHost — keyed content switching without screens touching Textual."""

from __future__ import annotations

from collections.abc import Mapping

from textual.containers import Vertical
from textual.widget import Widget


class InspectorHost(Vertical):
    """Holds one widget per key; exactly one is visible at a time."""

    def __init__(self, children: Mapping[str, Widget], initial: str) -> None:
        """Initialize the component from its view-model inputs."""
        self._keys = dict(children)
        self._current = initial
        super().__init__(*children.values(), classes="inspector-host")

    def on_mount(self) -> None:
        """Apply initial state once mounted."""
        self.show(self._current)

    def show(self, key: str) -> None:
        """Make the widget registered under ``key`` the visible one."""
        if key not in self._keys:
            return
        self._current = key
        for name, widget in self._keys.items():
            widget.display = name == key

    @property
    def current(self) -> str:
        """Key of the currently visible widget."""
        return self._current
