"""Reusable capability-aware actions following the resource action-bar grammar."""

from dataclasses import dataclass
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button


@dataclass(frozen=True, slots=True)
class Action:
    """One visible command and its current availability."""

    id: str
    label: str
    key: str = ""
    enabled: bool = True
    primary: bool = False


class ActionBar(Horizontal):
    """Render compact semantic actions; widgets outside the bar never handle Buttons."""

    DEFAULT_CSS = """
    ActionBar { height: auto; min-height: 1; overflow-x: auto; scrollbar-size-horizontal: 1; }
    ActionBar Button {
        width: auto; min-width: 0; height: 1; min-height: 1;
        padding: 0 1; margin: 0 1 0 0; border: none;
        background: transparent; color: $comparo-muted;
    }
    ActionBar Button.primary { color: $accent; text-style: bold; }
    ActionBar Button.-style-flat { border: none !important; }
    ActionBar Button:hover, ActionBar Button:focus { background: $accent 20%; color: $foreground; }
    ActionBar Button:disabled { color: $text-disabled; }
    """
    actions: reactive[tuple[Action, ...]] = reactive(())

    class Requested(Message):
        """An enabled semantic command was activated."""

        def __init__(self, bar: "ActionBar", action: str) -> None:
            """Retain the originating bar and command identity."""
            super().__init__()
            self.bar = bar
            self.action = action

        @property
        @override
        def control(self) -> "ActionBar":
            """Return the semantic event owner."""
            return self.bar

    def __init__(self, actions: tuple[Action, ...] = (), *, id: str | None = None) -> None:
        """Initialize from immutable command availability."""
        super().__init__(id=id)
        self.set_reactive(ActionBar.actions, actions)

    @override
    def compose(self) -> ComposeResult:
        """Use the same compact keyed actions as resource inspectors."""
        for action in self.actions:
            label = f"[{action.key}] {action.label}" if action.key else action.label
            yield Button(
                Content(label),
                name=action.id,
                disabled=not action.enabled,
                classes="primary" if action.primary else "",
                compact=True,
                flat=True,
            )

    def watch_actions(self, actions: tuple[Action, ...]) -> None:
        """Keep controls and keyboard focus stable when only availability changes."""
        if not self.is_mounted:
            return
        buttons = list(self.query(Button))
        if tuple(button.name for button in buttons) != tuple(action.id for action in actions):
            self.refresh(recompose=True)
            return
        for button, action in zip(buttons, actions, strict=True):
            button.label = Content(f"[{action.key}] {action.label}" if action.key else action.label)
            button.disabled = not action.enabled
            button.set_class(action.primary, "primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Emit only a currently enabled command."""
        event.stop()
        action = next((item for item in self.actions if item.id == event.button.name), None)
        if action is not None and action.enabled:
            self.post_message(self.Requested(self, action.id))

    def focus_action(self, *, last: bool = False) -> None:
        """Enter the semantic action group at its first or last enabled command."""
        buttons = [button for button in self.query(Button) if not button.disabled]
        if buttons:
            buttons[-1 if last else 0].focus()

    def move_focus(self, direction: int) -> bool:
        """Move within this group, returning false at its outer boundary."""
        buttons = [button for button in self.query(Button) if not button.disabled]
        focused = self.app.focused
        if focused in buttons:
            index = buttons.index(focused) + direction
            if 0 <= index < len(buttons):
                buttons[index].focus()
                return True
        return False
