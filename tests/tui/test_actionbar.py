"""Action availability updates preserve controls, focus and command authority."""

import asyncio
from typing import override

from textual.app import App
from textual.app import ComposeResult
from textual.widgets import Button

from comparo.tui.components.actionbar import Action
from comparo.tui.components.actionbar import ActionBar
from comparo.tui.theme import COMPARO_DARK


class _ActionApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.register_theme(COMPARO_DARK)
        self.theme = "comparo-dark"
        self.requested: list[str] = []

    @override
    def compose(self) -> ComposeResult:
        yield ActionBar((Action("preview", "Preview"), Action("save", "Save", enabled=False)))

    def on_action_bar_requested(self, event: ActionBar.Requested) -> None:
        assert event.control is self.query_one(ActionBar)
        self.requested.append(event.action)


def test_availability_updates_preserve_controls_and_focus() -> None:
    async def scenario() -> None:
        app = _ActionApp()
        async with app.run_test() as pilot:
            bar = app.query_one(ActionBar)
            preview, save = bar.query(Button)
            preview.focus()
            for enabled in (True, False, True):
                bar.actions = (
                    Action("preview", "Review YAML", key="p"),
                    Action("save", "Save profile", enabled=enabled, primary=True),
                )
                # Availability is synchronous: callers cannot see a temporarily empty bar.
                assert tuple(bar.query(Button)) == (preview, save)
                assert save.disabled is not enabled
                assert save.has_class("primary")
                assert str(preview.label) == "[p] Review YAML"
                await pilot.pause()
                assert app.focused is preview
                assert tuple(bar.query(Button)) == (preview, save)
            assert bar.move_focus(1)
            await pilot.pause()
            assert app.focused is save
            assert not bar.move_focus(1)
            assert bar.move_focus(-1)
            bar.focus_action(last=True)
            await pilot.pause()
            save.press()
            await pilot.pause()
            assert app.requested == ["save"]
            bar.actions = (Action("preview", "Preview"), Action("save", "Save", enabled=False))
            bar.on_button_pressed(Button.Pressed(save))
            bar.on_button_pressed(Button.Pressed(Button("Stale", name="removed")))
            await pilot.pause()
            assert app.requested == ["save"]
            assert not save.has_class("primary")
            bar.actions = (Action("cancel", "Cancel"),)
            await pilot.pause()
            assert [button.name for button in bar.query(Button)] == ["cancel"]
            bar.actions = ()
            await pilot.pause()
            bar.focus_action()
            assert not bar.move_focus(1)
            assert not list(bar.query(Button))

    asyncio.run(scenario())
