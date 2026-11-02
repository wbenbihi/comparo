"""User preferences composed from the shared terminal component library."""

import asyncio
from dataclasses import replace
from typing import Protocol
from typing import cast
from typing import override

from textual import on
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import VerticalScroll
from textual.content import Content

from comparo.build_info import read_build_info
from comparo.tui.components.actionbar import Action
from comparo.tui.components.actionbar import ActionBar
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.menu import Menu
from comparo.tui.components.menu import MenuItem
from comparo.tui.components.panel import Panel
from comparo.tui.components.selectfield import SelectField
from comparo.tui.components.typography import Caption
from comparo.tui.components.typography import SectionTitle
from comparo.tui.components.valueinput import ValueInput
from comparo.tui.preferences import SHORTCUTS
from comparo.tui.preferences import Preferences
from comparo.tui.types import ComparoScreen
from comparo.tui.workspaces.base import Workspace


class _PreferenceOwner(Protocol):
    preferences: Preferences
    preference_notice: str

    async def persist_preferences(self, preferences: Preferences) -> bool: ...


class SettingsWorkspace(Workspace):
    """Edit a local draft; save and apply it explicitly without touching execution controls."""

    SCREEN = ComparoScreen.SETTINGS

    def __init__(self) -> None:
        """Start a draft independent of any running execution."""
        super().__init__()
        self.draft = Preferences()
        self.command = "workspace.explore"

    @override
    def compose(self) -> ComposeResult:
        yield SectionTitle("Settings")
        yield Caption("About Comparo and your terminal preferences · shared across projects")
        with Horizontal(id="settings-layout"):
            yield Panel(
                Menu(
                    MenuItem("general", "General"),
                    MenuItem("appearance", "Appearance & layout"),
                    MenuItem("interaction", "Interaction"),
                    MenuItem("shortcuts", "Keyboard shortcuts"),
                    id="settings-sections",
                ),
                title="SETTINGS",
                id="settings-nav",
            )
            with VerticalScroll(id="settings-editor"):
                with Panel(title="GENERAL", id="settings-general"):
                    yield KeyValueList(id="settings-build-info")
                    yield Caption("Loading build information…", id="settings-build-status")
                with Panel(title="APPEARANCE & LAYOUT", id="settings-appearance"):
                    yield SelectField(
                        "Theme",
                        (
                            MenuItem("comparo-dark", "Comparo dark"),
                            MenuItem("comparo-light", "Comparo light"),
                        ),
                        id="setting-theme",
                    )
                    yield SelectField(
                        "Density",
                        (MenuItem("comfortable", "Comfortable"), MenuItem("compact", "Compact")),
                        id="setting-density",
                    )
                    yield SelectField(
                        "Open on startup",
                        tuple(MenuItem(s.value, s.value.title()) for s in ComparoScreen),
                        id="setting-workspace",
                    )
                    yield Caption(
                        "Spacing applies when saved. Theme and startup workspace apply next launch."
                    )
                with Panel(title="INTERACTION", id="settings-interaction"):
                    yield SelectField(
                        "Mouse",
                        (MenuItem("on", "Enabled"), MenuItem("off", "Disabled")),
                        id="setting-mouse",
                    )
                    yield Caption(
                        "Mouse reporting changes on the next launch. "
                        "Keyboard navigation always remains available."
                    )
                with Panel(title="KEYBOARD SHORTCUTS", id="settings-shortcuts"):
                    yield SelectField(
                        "Command",
                        tuple(MenuItem(key, label) for key, (label, _, _) in SHORTCUTS.items()),
                        value=self.command,
                        id="setting-command",
                    )
                    yield ValueInput("Key", maximum_length=32, id="setting-key")
                    yield Caption(
                        "Use the default key, F1-F24, or ctrl+alt+letter. "
                        "On Mac, function keys may need Fn. Conflicting shortcuts are rejected."
                    )
                    yield ActionBar(
                        (
                            Action("assign", "Assign key"),
                            Action("default-key", "Restore command default"),
                        ),
                        id="settings-key-actions",
                    )
                    yield Caption("", id="settings-key-summary", markup=False)
        yield Caption("", id="settings-notice", markup=False)
        yield ActionBar(
            (
                Action("save", "Save & apply", primary=True),
                Action("discard", "Discard edits"),
                Action("reset", "Reset draft to defaults"),
            ),
            id="settings-actions",
        )

    def on_mount(self) -> None:
        """Reflect the active preferences and any load diagnostic."""
        self._restore()
        self._load_build_info()

    @work(exclusive=True, group="settings-build-info")
    async def _load_build_info(self) -> None:
        info = await asyncio.to_thread(read_build_info)
        self.query_one("#settings-build-info", KeyValueList).entries = tuple(
            Field(key, Content(value))
            for key, value in (
                ("Version", info.version),
                ("Author", info.author),
                ("Repository", info.repository),
                ("Source", info.source),
                ("License", info.license),
                ("Build SHA", info.sha or "Not recorded"),
            )
        )
        self.query_one("#settings-build-status", Caption).update(
            "Modified source · this checkout/build differs from the recorded commit."
            if info.modified
            else "Read-only package and build information."
        )

    @override
    def focus_default(self) -> None:
        self.query_one("#settings-sections", Menu).focus()

    def _restore(self) -> None:
        owner = cast(_PreferenceOwner, self.app)
        self.draft = owner.preferences
        self._reflect()
        self._notice(owner.preference_notice)

    def _notice(self, text: str) -> None:
        self.query_one("#settings-notice", Caption).update(text)

    def _reflect(self) -> None:
        for name in ("theme", "density", "workspace"):
            self.query_one(f"#setting-{name}", SelectField).set_value(getattr(self.draft, name))
        self.query_one("#setting-mouse", SelectField).set_value("on" if self.draft.mouse else "off")
        self._key()

    def _key(self) -> None:
        self.query_one("#setting-key", ValueInput).value = self.draft.keymap.get(
            self.command, SHORTCUTS[self.command][1]
        )
        self.query_one("#settings-key-summary", Caption).update(
            "\n".join(
                f"{label}: {self.draft.keymap.get(name, default)}"
                for name, (label, default, _) in SHORTCUTS.items()
            )
        )

    @on(Menu.Selected, "#settings-sections")
    def _section(self, event: Menu.Selected) -> None:
        event.stop()
        panel = self.query_one(f"#settings-{event.item}")
        panel.scroll_visible(animate=False, top=True)
        if event.item != "general":
            panel.query_one(SelectField).focus()

    @on(SelectField.Changed)
    def _changed(self, event: SelectField.Changed) -> None:
        event.stop()
        name = (event.field.id or "").removeprefix("setting-")
        if name == "command":
            self.command = event.value
            self._key()
            return
        if name == "mouse":
            self.draft = replace(self.draft, mouse=event.value == "on")
        elif name == "theme":
            self.draft = replace(self.draft, theme=event.value)
        elif name == "density":
            self.draft = replace(self.draft, density=event.value)
        elif name == "workspace":
            self.draft = replace(self.draft, workspace=event.value)
        self._notice("Unsaved preferences. Save & apply to keep these choices.")

    @on(ActionBar.Requested)
    async def _action(self, event: ActionBar.Requested) -> None:
        event.stop()
        if event.action in {"assign", "default-key"}:
            keymap = dict(self.draft.keymap)
            if event.action == "assign":
                keymap[self.command] = (
                    self.query_one("#setting-key", ValueInput).value.strip().lower()
                )
            else:
                keymap.pop(self.command, None)
            try:
                self.draft = replace(self.draft, keymap=keymap)
            except ValueError as error:
                self._notice(str(error))
                return
            self._key()
            self._notice("Shortcut assigned to draft. Save & apply to activate it.")
        elif event.action == "reset":
            self.draft = Preferences()
            self._reflect()
            self._notice("Defaults loaded into draft. Save & apply to keep them.")
        elif event.action == "discard":
            self._restore()
        elif event.action == "save":
            saved = await cast(_PreferenceOwner, self.app).persist_preferences(self.draft)
            self._notice(
                "Preferences saved. Theme, mouse and startup changes apply next launch."
                if saved
                else "Could not save preferences. Your active settings are unchanged."
            )
