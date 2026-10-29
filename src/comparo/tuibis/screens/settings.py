"""Settings workspace — app-level preferences, never project config."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.containers import Horizontal

from comparo.tuibis import fixtures_misc
from comparo.tuibis.viewmodels import KeyHint
from comparo.tuibis.widgets.cursor_list import CursorList
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.lines import LineBlock
from comparo.tuibis.widgets.lines import line_panel
from comparo.tuibis.widgets.panel import Panel

_DARK = "comparo-dark"
_LIGHT = "comparo-light"


class SettingsWorkspace(Horizontal):
    """Composition only: section nav + the active section's detail."""

    BINDINGS: ClassVar = [
        Binding("t", "toggle_theme", "Toggle theme", show=False),
    ]

    def __init__(self) -> None:
        """Build the section nav and one detail panel per section."""
        self._nav = CursorList(fixtures_misc.settings_nav_items(), id="settings-nav-list")
        appearance = fixtures_misc.appearance_panel(_DARK)
        self._appearance_block = LineBlock(appearance.lines)
        panels = {
            "sec:appearance": Panel(
                self._appearance_block,
                title=appearance.title,
                hint=appearance.hint,
            ),
            "sec:keyboard": line_panel(fixtures_misc.keyboard_panel()),
            "sec:interaction": line_panel(fixtures_misc.interaction_panel()),
            "sec:plugins": line_panel(fixtures_misc.plugins_panel()),
            "sec:updates": line_panel(fixtures_misc.updates_panel()),
        }
        self._host = InspectorHost(panels, initial="sec:appearance")
        self._host.id = "settings-detail"
        super().__init__(
            Panel(self._nav, title="SETTINGS", id="settings-nav"),
            self._host,
            id="settings-workspace",
        )

    @property
    def hints(self) -> tuple[KeyHint, ...]:
        """Contextual footer hints."""
        return fixtures_misc.SETTINGS_HINTS

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """Switch the detail area to the highlighted section."""
        if event.source is self._nav:
            self._host.show(event.key)

    def action_toggle_theme(self) -> None:
        """Flip the whole app between the dark and light themes."""
        if self._host.current != "sec:appearance":
            return
        theme = _LIGHT if self.app.theme == _DARK else _DARK
        self.app.theme = theme
        self._appearance_block.update_lines(fixtures_misc.appearance_lines(theme))
