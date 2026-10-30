"""Expandable sections with a library-owned header and semantic navigation."""

from typing import ClassVar
from typing import override

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.containers import Vertical
from textual.events import Click
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from comparo.tui.theme import style


class DisclosureHeader(Widget, can_focus=True):
    """One focus stop for title, summary and right-aligned edit affordance."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,space,right", "activate", "Edit section"),
        Binding("up", "move(-1)", "Previous section", show=False),
        Binding("down", "move(1)", "Next section", show=False),
        Binding("left", "collapse", "Close section", show=False),
    ]
    DEFAULT_CSS = """
    DisclosureHeader { height: 1; width: 1fr; padding: 0 1; }
    DisclosureHeader:hover { background: $accent 8%; }
    DisclosureHeader:focus { background: $accent 16%; }
    """
    title: reactive[str] = reactive("")
    expanded: reactive[bool] = reactive(False)
    action_label: reactive[str] = reactive("Open")

    class Collapsed(Message):
        """Close the section without losing its header focus."""

    class Activated(Message):
        """Edit/toggle this section."""

    class Moved(Message):
        """Move to another section without entering its controls."""

        def __init__(self, direction: int) -> None:
            """Retain the requested sibling direction."""
            super().__init__()
            self.direction = direction

    @override
    def render(self) -> Text:
        title, _, summary = self.title.partition(" · ")
        action = Text(f"[ {self.action_label} ]", style("value.reference"))
        text = Text("▾ " if self.expanded else "▸ ", style("dim"))
        text.append(title, style("bright"))
        text.truncate(max(0, self.content_size.width - action.cell_len - 2), overflow="ellipsis")
        available = max(0, self.content_size.width - text.cell_len - action.cell_len - 4)
        context = Text(summary, style("muted"))
        context.truncate(available, overflow="ellipsis")
        text.append(
            " "
            * max(
                2, self.content_size.width - text.cell_len - context.cell_len - action.cell_len - 2
            )
        )
        text.append_text(context)
        text.append("  ")
        text.append_text(action)
        return text

    def action_activate(self) -> None:
        """Keep activation independent from cursor movement."""
        self.post_message(self.Activated())

    def action_collapse(self) -> None:
        """Close this section through its semantic owner."""
        self.post_message(self.Collapsed())

    def action_move(self, direction: int) -> None:
        """Delegate sibling navigation to the owning form."""
        self.post_message(self.Moved(direction))

    def on_click(self, event: Click) -> None:
        """Make the whole header a pointer target."""
        event.stop()
        self.action_activate()


class Disclosure(Vertical):
    """Stable section body behind a custom full-width summary header."""

    expanded: reactive[bool] = reactive(False)
    title: reactive[str] = reactive("")
    DEFAULT_CSS = """
    Disclosure { height: auto; border: round $comparo-border; margin-bottom: 1; }
    Disclosure > Vertical { height: auto; padding: 0 1; }
    """

    class Opened(Message):
        """A user requested editing this section, not merely a state refresh."""

        def __init__(self, section: "Disclosure") -> None:
            """Identify the section entering edit mode."""
            super().__init__()
            self.section = section

    def __init__(
        self, title: str, *children: Widget, expanded: bool = False, id: str | None = None
    ) -> None:
        """Retain components while their section is closed."""
        super().__init__(id=id)
        self._contents = children
        self.edit_mode = False
        self.set_reactive(Disclosure.title, title)
        self.set_reactive(Disclosure.expanded, expanded)

    @override
    def compose(self) -> ComposeResult:
        yield DisclosureHeader()
        yield Vertical(*self._contents)

    def on_mount(self) -> None:
        """Apply the initial section state."""
        self._reflect()

    def watch_expanded(self) -> None:
        """Update visibility without destroying editor state."""
        if self.is_mounted:
            self._reflect()

    def watch_title(self) -> None:
        """Refresh the header summary without replacing its focus owner."""
        if self.is_mounted:
            self._reflect()

    def _reflect(self) -> None:
        header = self.query_one(DisclosureHeader)
        header.title = self.title
        header.expanded = self.expanded
        header.action_label = "Edit" if self.edit_mode else "Close" if self.expanded else "Open"
        self.query_one(Vertical).display = self.expanded

    @on(DisclosureHeader.Activated)
    def _toggle(self, event: DisclosureHeader.Activated) -> None:
        event.stop()
        # In a form, activating an open header enters its editor; Left/Escape returns.
        self.expanded = True if self.edit_mode else not self.expanded
        if self.expanded:
            self.post_message(self.Opened(self))

    @on(DisclosureHeader.Collapsed)
    def _collapse(self, event: DisclosureHeader.Collapsed) -> None:
        event.stop()
        self.expanded = False

    def focus_header(self) -> None:
        """Return focus to this section's navigation surface."""
        self.query_one(DisclosureHeader).focus()
