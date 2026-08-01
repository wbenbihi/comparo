"""Explorer-specific navigation built from reusable TUI components."""

from __future__ import annotations

from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive

from comparo.tui4.bindings import FILTER_KEY
from comparo.tui4.components.badge import BadgeEmphasis
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.badge import badge_text
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.legend import Legend
from comparo.tui4.components.treemenu import TreeMenu
from comparo.tui4.fragments import chip
from comparo.tui4.fragments import entity_label
from comparo.tui4.fragments import glyph
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.explorer import catalog_nodes
from comparo.tui4.models.explorer import legend_items
from comparo.tui4.models.navigation import MenuNode
from comparo.tui4.theme import kind_glyph
from comparo.tui4.theme import style

_ROLE_BADGES = {
    "baseline": ("base", BadgeTone.BASELINE),
    "candidate": ("cand", BadgeTone.CANDIDATE),
}
_LOCALITY_BADGES = {
    "local": ("local", BadgeTone.SUCCESS, BadgeEmphasis.QUIET),
    "live": ("live", BadgeTone.DANGER, BadgeEmphasis.SOLID),
}


class ExplorerResourceTree(TreeMenu[str]):
    """Resource-aware tree that owns the Explorer's node projection."""

    catalog: reactive[ExplorerCatalog | None] = reactive(None, init=False)
    filter_text: reactive[str] = reactive("", init=False)

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        """Initialize an empty resource tree."""
        super().__init__(id=id, classes=classes)

    def watch_catalog(self) -> None:
        """Rebuild semantic nodes when a workspace snapshot changes."""
        self._refresh_nodes()

    def watch_filter_text(self) -> None:
        """Rebuild semantic nodes when the local filter changes."""
        self._refresh_nodes()

    def _refresh_nodes(self) -> None:
        """Project catalog state into the generic menu's immutable nodes."""
        catalog = self.catalog
        self.nodes = catalog_nodes(catalog, self.filter_text) if catalog is not None else ()

    @override
    def format_node(self, node: MenuNode[str]) -> Text:
        """Apply the Explorer row grammar to every resource and section node."""
        catalog = self.catalog
        entry = catalog.entries.get(node.id) if catalog is not None else None
        if entry is not None:
            return self._format_resource(entry)
        if node.id.startswith("section:"):
            return Text.assemble(
                (node.prompt.plain, style("label")),
                (f" {len(node.children)}", style("dim")),
            )
        return super().format_node(node)

    @staticmethod
    def _format_resource(entry: ResourceEntry) -> Text:
        """Format one resource identity and its safe catalog facts."""
        summary = entry.summary
        facts = entry.facts
        display_name = summary.name or f"Unnamed {summary.coordinate.kind}"
        character, token = kind_glyph(summary.coordinate.kind)
        prefix: list[Text] = [glyph(character, token)]
        if facts.method is not None:
            prefix.append(chip(facts.method, f"method.{facts.method}", width=7))
        badges: list[Text] = []
        if facts.role is not None:
            label, tone = _ROLE_BADGES[facts.role]
            badges.append(badge_text(label, tone=tone, emphasis=BadgeEmphasis.SOLID))
        if facts.locality is not None:
            label, tone, emphasis = _LOCALITY_BADGES[facts.locality]
            badges.append(badge_text(label, tone=tone, emphasis=emphasis))
        if facts.uses_matrix:
            badges.append(badge_text("matrix", tone=BadgeTone.WARNING))
        if facts.streaming:
            badges.append(badge_text("sse", tone=BadgeTone.INFO))
        return entity_label(
            display_name,
            summary.coordinate.resource_id,
            prefix=tuple(prefix),
            badges=tuple(badges),
            include_id=False,
        )


class ExplorerNavigator(Vertical):
    """Self-contained Explorer filter, resource tree, and kind legend."""

    BINDINGS: ClassVar[list[BindingType]] = []

    catalog: reactive[ExplorerCatalog | None] = reactive(None, init=False)
    legend_visible: reactive[bool] = reactive(True, init=False)

    class ResourceHighlighted(Message):
        """The navigator cursor moved onto a concrete resource."""

        def __init__(self, navigator: ExplorerNavigator, entry: ResourceEntry) -> None:
            """Initialize the semantic resource event."""
            self.navigator = navigator
            self.entry = entry
            super().__init__()

        @property
        @override
        def control(self) -> ExplorerNavigator:
            """Return the navigator that emitted this event."""
            return self.navigator

    @override
    def compose(self) -> ComposeResult:
        """Compose reusable controls behind an Explorer-specific interface."""
        yield FilterInput(title="Filter", key_hint=FILTER_KEY)
        yield ExplorerResourceTree(id="explorer-tree")
        yield Legend(id="explorer-legend")

    def watch_catalog(self) -> None:
        """Feed snapshot data into the tree and legend components."""
        tree = self.query_one(ExplorerResourceTree)
        tree.catalog = self.catalog
        self.query_one(Legend).items = legend_items(self.catalog) if self.catalog else ()

    def watch_legend_visible(self) -> None:
        """Hide the legend completely so the tree reclaims its rows."""
        self.query_one(Legend).display = self.legend_visible

    def toggle_legend(self) -> None:
        """Toggle the optional kind legend."""
        self.legend_visible = not self.legend_visible

    def focus_tree(self) -> None:
        """Move focus to resource navigation."""
        self.query_one(ExplorerResourceTree).focus()

    def focus_filter(self) -> None:
        """Move focus to the navigator's filter."""
        self.query_one(FilterInput).focus()

    def on_filter_input_changed(self, event: FilterInput.Changed) -> None:
        """Apply the live local filter without exposing tree mechanics."""
        event.stop()
        self.query_one(ExplorerResourceTree).filter_text = event.value

    def on_filter_input_submitted(self, event: FilterInput.Submitted) -> None:
        """Return keyboard focus to filtered results."""
        event.stop()
        self.focus_tree()

    def on_filter_input_dismissed(self, event: FilterInput.Dismissed) -> None:
        """Return keyboard focus to resource navigation."""
        event.stop()
        self.focus_tree()

    def on_tree_menu_highlighted(self, event: TreeMenu.Highlighted[str]) -> None:
        """Translate the generic menu event into a typed resource event."""
        tree = self.query_one(ExplorerResourceTree)
        if event.menu is not tree:
            return
        event.stop()
        catalog = self.catalog
        entry = catalog.entries.get(event.item) if catalog is not None else None
        if entry is not None:
            self.post_message(self.ResourceHighlighted(self, entry))
