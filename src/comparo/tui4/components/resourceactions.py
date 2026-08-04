"""Semantic action bar for one selected resource."""

from __future__ import annotations

from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button

from comparo.tui4.bindings import RESOURCE_ACTION_KEYS
from comparo.tui4.models.resource import ResourceAction
from comparo.tui4.models.resource import ResourceActionId

_BUTTON_PREFIX = "resource-action-"


class ResourceActionBar(Horizontal):
    """Render capability-aware actions with visible keyboard equivalents."""

    actions: reactive[tuple[ResourceAction, ...]] = reactive((), recompose=True)

    class ActionRequested(Message):
        """A user requested one enabled semantic resource action."""

        def __init__(self, bar: ResourceActionBar, action: ResourceActionId) -> None:
            """Initialize an action request from its stable identifier."""
            self.bar = bar
            self.action = action
            super().__init__()

        @property
        @override
        def control(self) -> ResourceActionBar:
            """Return the action bar that emitted the request."""
            return self.bar

    def __init__(
        self,
        actions: tuple[ResourceAction, ...] = (),
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the bar from immutable capability decisions."""
        super().__init__(id=id, classes=classes)
        self.set_reactive(ResourceActionBar.actions, actions)

    @override
    def compose(self) -> ComposeResult:
        """Compose one keyed Textual button per semantic action."""
        for action in self.actions:
            key = RESOURCE_ACTION_KEYS[action.id.value]
            yield Button(
                Content(f"[{key}] {action.label}"),
                id=f"{_BUTTON_PREFIX}{action.id.value}",
                disabled=not action.enabled,
                tooltip=action.disabled_reason,
                compact=True,
                flat=True,
            )

    def request(self, action_id: ResourceActionId) -> None:
        """Emit an action only when the current capability decision allows it."""
        action = next((item for item in self.actions if item.id is action_id), None)
        if action is not None and action.enabled:
            self.post_message(self.ActionRequested(self, action_id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Translate Textual button identity into a semantic request."""
        event.stop()
        button_id = event.button.id
        if button_id is None or not button_id.startswith(_BUTTON_PREFIX):
            return
        try:
            action = ResourceActionId(button_id.removeprefix(_BUTTON_PREFIX))
        except ValueError:
            return
        self.request(action)
