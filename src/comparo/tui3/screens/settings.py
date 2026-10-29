"""Settings — app-level categories: appearance, keyboard, interaction, plugins, updates."""

from __future__ import annotations

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widget import Widget

from comparo.tui3.screens.base import WorkspaceScreen
from comparo.tui3.widgets import CursorList
from comparo.tui3.widgets import Panel
from comparo.tui3.widgets import PanelHeader
from comparo.tui3.widgets import Row
from comparo.tui3.widgets import Seg
from comparo.tui3.widgets import TextRow
from comparo.tui3.widgets import fragments as fr

CATS: tuple[tuple[str, str], ...] = (
    ("appearance", "Appearance"),
    ("keyboard", "Keyboard"),
    ("interaction", "Interaction"),
    ("plugins", "Plugins"),
    ("updates", "Updates"),
)
CAT_LABEL = dict(CATS)

KEY_TABLE: tuple[tuple[str, str, str, str], ...] = (
    ("q", "quit — always, everywhere", "reserved", "b"),
    ("esc", "back / cancel / close", "reserved", "b"),
    ("1-4", "workspaces", "global", "hi"),
    ("space", "select", "lists", "hi"),
    ("x / p", "run / plan", "launch", "hi"),
    ("[ ]", "previous / next facet", "detail views", "hi"),
)


class SettingsNav(Panel):
    """The fixed-width category panel."""

    DEFAULT_CSS: ClassVar[str] = """
    SettingsNav { width: 26; }
    """


class SettingsScreen(WorkspaceScreen):
    """The Settings workspace: five categories, app-only scope."""

    def __init__(self) -> None:
        """Start on appearance."""
        super().__init__(id="ws-settings")
        self._cat = CATS[0][0]
        self._list = CursorList(
            tuple(Row(key, fr.t(label, "hi"), search=label) for key, label in CATS),
            id="settings-list",
        )
        self._head = PanelHeader(CAT_LABEL[self._cat])
        self._content = Vertical(id="settings-content")

    # ----------------------------------------------------------- composition

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Category list beside the category content panel."""
        yield Horizontal(
            SettingsNav(
                PanelHeader("Settings"),
                self._list,
                TextRow(fr.t("the app, never the project", "dim")),
                id="settings-nav",
            ),
            Panel(self._head, self._content, hi=True, id="settings-detail", classes="col-right"),
            classes="cols",
        )

    def on_mount(self) -> None:
        """Render the initial category."""
        self._paint_category()

    # -------------------------------------------------------------- content

    def _kv(self, label: str, *value: Text) -> TextRow:
        """One dim-label key/value line."""
        return TextRow(fr.join(fr.t(f"  {label:<10}", "dim"), *value))

    def _is_light(self) -> bool:
        """Whether the light theme is active."""
        return self.app.theme == "tui3-light"

    def _appearance(self) -> list[Widget]:
        """Theme, density, glyphs, and the live preview row."""
        return [
            TextRow(fr.t("THEME", "ph")),
            Horizontal(
                Seg(("dark", "light"), "light" if self._is_light() else "dark"),
                TextRow(fr.t(" — switches the app live", "dim")),
                classes="stack-auto",
            ),
            TextRow(fr.t("DENSITY", "ph")),
            TextRow(fr.join(fr.t("comfortable", "hi"), fr.sep(), fr.t("compact", "dim"))),
            TextRow(fr.t("GLYPHS", "ph")),
            TextRow(
                fr.join(
                    fr.t("unicode ● ◆ ▦ ⬡", "hi"), fr.sep(), fr.t("ascii fallback * > # @", "dim")
                )
            ),
            TextRow(fr.t("PREVIEW", "ph")),
            TextRow(
                fr.join(
                    fr.t("✓ same", "same"),
                    fr.sep(),
                    fr.t("✗ drift", "drift"),
                    fr.sep(),
                    fr.t("● env", "k.env"),
                    fr.sep(),
                    fr.t("◆ Price quote", "k.req"),
                    fr.t("  "),
                    fr.method("GET"),
                    fr.t("  x plans", "k.mat"),
                )
            ),
        ]

    def _keyboard(self) -> list[Widget]:
        """The reserved/global key table."""
        out: list[Widget] = [TextRow(fr.t(f"{'KEY':<8}{'ACTION':<28}SCOPE", "ph"))]
        out.extend(
            TextRow(
                fr.join(fr.t(f"{key:<8}", tok), fr.t(f"{action:<28}", "hi"), fr.t(scope, "dim"))
            )
            for key, action, scope, tok in KEY_TABLE
        )
        out.append(
            TextRow(
                fr.t(
                    "remapping lands with the keymap editor — conflicts fail closed, q stays q.",
                    "dim",
                )
            )
        )
        return out

    def _interaction(self) -> list[Widget]:
        """Mouse, write confirmation, and home workspace."""
        return [
            TextRow(fr.t("MOUSE", "ph")),
            TextRow(fr.join(fr.t("on", "hi"), fr.sep(), fr.t("keyboard-only", "dim"))),
            TextRow(fr.t("CONFIRM WRITES", "ph")),
            TextRow(
                fr.join(fr.t("always ask", "hi"), fr.sep(), fr.t("per-session approve", "dim"))
            ),
            TextRow(fr.t("HOME WORKSPACE", "ph")),
            TextRow(fr.join(fr.t("explorer", "hi"), fr.sep(), fr.t("launch · reports", "dim"))),
        ]

    def _plugins(self) -> list[Widget]:
        """The (empty) plugin registry."""
        return [
            TextRow(fr.t("none installed.", "dim")),
            TextRow(
                fr.t("The plugin API lands after 1.0 — reporters, auth helpers, importers.", "dim")
            ),
        ]

    def _updates(self) -> list[Widget]:
        """Version, channel, and update status."""
        return [
            self._kv("version", fr.t("comparo 1.0.0", "hi")),
            self._kv("channel", fr.t("stable", "hi")),
            self._kv(
                "status",
                fr.t("✓ up to date", "same"),
                fr.t(" · checked today 09:00 · u re-checks", "dim"),
            ),
        ]

    def _cat_widgets(self) -> list[Widget]:
        """The body for the active category."""
        if self._cat == "appearance":
            return self._appearance()
        if self._cat == "keyboard":
            return self._keyboard()
        if self._cat == "interaction":
            return self._interaction()
        if self._cat == "plugins":
            return self._plugins()
        return self._updates()

    def _paint_category(self) -> None:
        """Repaint the right panel for the active category."""
        self._head.set_left(fr.t(CAT_LABEL[self._cat].upper(), "ph"))
        self._content.remove_children()
        self._content.mount_all(self._cat_widgets())

    def _refresh_chrome(self) -> None:
        """Ask the app to repaint crumb and footer (guarded, no circular import)."""
        refresh = getattr(self.app, "refresh_chrome", None)
        if callable(refresh):
            refresh()

    def _set_theme(self, light: bool) -> None:
        """Really switch the app theme and re-highlight the seg."""
        self.app.theme = "tui3-light" if light else "tui3-dark"
        for seg in self._content.query(Seg):
            seg.set_active("light" if light else "dark")

    # --------------------------------------------------------------- events

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """The content follows the category cursor."""
        if event.source is not self._list:
            return
        self._cat = event.key
        self._paint_category()
        self._refresh_chrome()

    def on_seg_changed(self, event: Seg.Changed) -> None:
        """The theme seg switches the app theme live."""
        self._set_theme(event.value == "light")

    # ----------------------------------------------------------- app wiring

    @override
    def crumb(self) -> str:
        """Navrow crumb."""
        return f"settings · {self._cat}"

    @override
    def tier(self) -> str:
        """Navrow tier badge."""
        return "SETTINGS"

    @override
    def hints(self) -> list[tuple[str, str]]:
        """Footer keys."""
        return [("↑↓", "category"), ("enter", "change"), ("q", "quit")]

    @override
    def focus_default(self) -> None:
        """Focus the category list."""
        self._list.focus()

    @override
    def on_ws_key(self, key: str) -> bool:
        """Workspace keys: t toggles the theme on appearance."""
        if key == "t" and self._cat == "appearance":
            self._set_theme(not self._is_light())
            return True
        return False
