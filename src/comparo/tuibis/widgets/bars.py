"""Shell chrome components: top bar, workspace tabs, footer."""

from __future__ import annotations

from typing import override

from rich.text import Text
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static

from comparo.tuibis.theme import style
from comparo.tuibis.viewmodels import EntityKind
from comparo.tuibis.viewmodels import KeyHint
from comparo.tuibis.widgets.fragments import glyph

WORKSPACES: tuple[str, ...] = ("EXPLORE", "LAUNCH", "REPORTS", "SETTINGS")


class TopBar(Horizontal):
    """`comparo ⇄ · ◆ project … env lanes · ctrl+p commands`."""

    def __init__(self, project: str, baseline: str, candidate: str) -> None:
        """Initialize the component from its view-model inputs."""
        left = Text()
        left.append(" comparo ", style=style("bright"))
        left.append("⇄", style=style("baseline"))
        left.append("  ")
        left.append_text(glyph(EntityKind.PROJECT))
        left.append(" ")
        left.append(project, style=style("bright"))
        right = Text()
        right.append("env ", style=style("muted"))
        right.append(baseline, style=style("baseline"))
        right.append(" ⇄ ", style=style("muted"))
        right.append(candidate, style=style("candidate"))
        right.append("   ")
        right.append("ctrl+p", style=style("fg"))
        right.append(" commands ", style=style("muted"))
        super().__init__(
            Static(left, classes="bar-left"),
            Static(right, classes="bar-right"),
            classes="top-bar",
        )


class WorkspaceTabs(Horizontal):
    """The single top-level menu: `1 EXPLORE  2 LAUNCH  3 REPORTS  4 SETTINGS`."""

    class Selected(Message):
        """Selected — see the module docstring."""

        def __init__(self, workspace: str) -> None:
            """Initialize the component from its view-model inputs."""
            super().__init__()
            self.workspace = workspace

    def __init__(self, active: str) -> None:
        """Initialize the component from its view-model inputs."""
        self._active = active
        super().__init__(classes="workspace-tabs")

    def compose_tabs(self) -> Text:
        """Render the tab strip line."""
        text = Text(" ")
        for index, name in enumerate(WORKSPACES, 1):
            if name == self._active:
                text.append(f" {index} {name} ", style=style("chip.active"))
            else:
                text.append(f" {index} {name} ", style=style("muted"))
            text.append("  ")
        return text

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield the child widgets."""
        yield Static(self.compose_tabs(), classes="tabs-line")

    def set_active(self, name: str) -> None:
        """Switch the highlighted workspace tab."""
        self._active = name
        self.query_one(".tabs-line", Static).update(self.compose_tabs())


class FooterBar(Horizontal):
    """Contextual key hints; `? help · q quit` is always right-most."""

    def __init__(self, hints: tuple[KeyHint, ...]) -> None:
        """Initialize the component from its view-model inputs."""
        super().__init__(classes="footer-bar")
        self._hints = hints

    def _render_hints(self) -> tuple[Text, Text]:
        left = Text(" ")
        for index, hint in enumerate(self._hints):
            if index:
                left.append("  ·  ", style=style("dim"))
            left.append(hint.key, style=style("fg"))
            left.append(f" {hint.label}", style=style("muted"))
        right = Text()
        right.append("?", style=style("fg"))
        right.append(" help", style=style("muted"))
        right.append("  ·  ", style=style("dim"))
        right.append("q", style=style("fg"))
        right.append(" quit ", style=style("muted"))
        return left, right

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield the child widgets."""
        left, right = self._render_hints()
        yield Static(left, classes="bar-left")
        yield Static(right, classes="bar-right")

    def set_hints(self, hints: tuple[KeyHint, ...]) -> None:
        """Replace the contextual key hints."""
        self._hints = hints
        left, right = self._render_hints()
        statics = list(self.query(Static))
        statics[0].update(left)
        statics[1].update(right)
