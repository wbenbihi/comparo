"""TreeMenu — collapsible semantic menu over Tree."""

from dataclasses import dataclass
from typing import override

from rich.text import Text
from rich.text import TextType
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Tree
from textual.widgets.tree import TreeNode


@dataclass(frozen=True)
class MenuNode[T: str]:
    """One tree entry; a node with no children is a selectable leaf."""

    id: T
    label: TextType
    children: tuple["MenuNode[T]", ...] = ()
    glyph: str | None = None

    @property
    def is_leaf(self) -> bool:
        """Whether this node can be selected instead of expanded."""
        return not self.children

    @property
    def prompt(self) -> Text:
        """Build the Rich label consumed by Textual's tree widget."""
        if self.glyph is None:
            return (
                Text.from_markup(self.label) if isinstance(self.label, str) else self.label.copy()
            )
        return Text.assemble(f"{self.glyph} ", self.label)


class TreeMenu[T: str](Tree[T]):
    """Collapsible menu: branches toggle, leaves post Highlighted/Selected.

    ``nodes`` is the only write path; node ids must be unique tree-wide.
    """

    nodes: reactive[tuple[MenuNode[T], ...]] = reactive(())

    class Highlighted[R: str](Message):
        """Cursor moved onto a leaf."""

        def __init__(self, menu: "TreeMenu[R]", item: R) -> None:
            """Initialize the message with its tree and item identity."""
            self.menu = menu
            self.item = item
            super().__init__()

        @property
        @override
        def control(self) -> "TreeMenu[R]":
            """Return the tree that emitted this message."""
            return self.menu

    class Selected[R: str](Highlighted[R]):
        """Leaf committed (enter/click)."""

    def __init__(
        self,
        *nodes: MenuNode[T],
        initial: T | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a tree from immutable semantic nodes."""
        super().__init__("root", id=id, classes=classes)
        self.show_root = False
        self.nodes = tuple(nodes)
        self._initial = initial
        self._built = False

    @override
    def on_mount(self) -> None:
        """Build nodes and apply the initial cursor position."""
        self._rebuild(prefer=self._initial)

    def watch_nodes(self) -> None:
        """Rebuild the mounted tree when its node projection changes."""
        if self.is_mounted:
            self._rebuild()

    @property
    def cursor_id(self) -> T | None:
        """Return the stable identity under the cursor, when available."""
        return self.cursor_node.data if self.cursor_node is not None else None

    def _expanded_ids(self) -> set[T]:
        expanded: set[T] = set()

        def walk(tree_node: TreeNode[T]) -> None:
            for child in tree_node.children:
                if child.is_expanded and child.data is not None:
                    expanded.add(child.data)
                walk(child)

        walk(self.root)
        return expanded

    def _rebuild(self, prefer: T | None = None) -> None:
        """Recreate nodes; first populated build expands everything."""
        expanded = self._expanded_ids() if self._built else None
        current = prefer if prefer is not None else self.cursor_id
        by_id: dict[T, TreeNode[T]] = {}
        self.clear()
        self.root.expand()

        def build(parent: TreeNode[T], node: MenuNode[T]) -> None:
            if node.is_leaf:
                tree_node = parent.add_leaf(node.prompt, data=node.id)
            else:
                tree_node = parent.add(node.prompt, data=node.id)
                for child in node.children:
                    build(tree_node, child)
                if expanded is None or node.id in expanded:
                    tree_node.expand()
            by_id[node.id] = tree_node

        for node in self.nodes:
            build(self.root, node)
        self._by_id = by_id
        self._built = bool(self.nodes)
        if current is not None and current in by_id:
            self.move_cursor(by_id[current])

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[T]) -> None:
        """Emit a semantic highlight only for selectable leaves."""
        event.stop()
        node = event.node
        if node.data is not None and not node.allow_expand:
            self.post_message(self.Highlighted(self, node.data))

    def on_tree_node_selected(self, event: Tree.NodeSelected[T]) -> None:
        """Emit a semantic selection only for selectable leaves."""
        event.stop()
        node = event.node
        if node.data is not None and not node.allow_expand:
            self.post_message(self.Selected(self, node.data))

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[T]) -> None:
        """Keep Textual's structural event inside this component."""
        event.stop()

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed[T]) -> None:
        """Keep Textual's structural event inside this component."""
        event.stop()
