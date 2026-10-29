"""The project tree — kind groups, entity glyphs, method colors, pills."""

from __future__ import annotations

from rich.text import Text
from textual.message import Message
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from comparo.tuibis.theme import style
from comparo.tuibis.viewmodels import ProjectVM
from comparo.tuibis.viewmodels import TreeLeafVM
from comparo.tuibis.widgets import fragments


class ProjectTree(Tree[str]):
    """Native Tree with comparo row rendering; highlight previews instantly."""

    class LeafHighlighted(Message):
        """LeafHighlighted — see the module docstring."""

        def __init__(self, key: str) -> None:
            """Initialize the component from its view-model inputs."""
            super().__init__()
            self.key = key

    def __init__(self, project: ProjectVM) -> None:
        """Initialize the component from its view-model inputs."""
        super().__init__("project", id="project-tree")
        self.show_root = False
        self.guide_depth = 2
        self._project = project
        self._query = ""
        self._populate(project)

    def _leaf_label(self, leaf: TreeLeafVM) -> Text:
        text = Text()
        text.append_text(fragments.entity(leaf.kind, f"{leaf.name:<13}"))
        if leaf.method:
            text.append_text(fragments.method(leaf.method, pad=5))
        if leaf.pills:
            text.append(" ")
            text.append_text(fragments.pills(leaf.pills))
        if leaf.detail:
            text.append(f"  {leaf.detail}", style=style("muted"))
        if leaf.dimmed:
            text.stylize(style("dim"))
        return text

    def _group_label(self, label: str, count: str) -> Text:
        text = Text(label, style=style("label"))
        text.append(f"  {count}", style=style("muted"))
        return text

    def _populate(self, project: ProjectVM) -> None:
        for group in project.groups:
            node: TreeNode[str] = self.root.add(
                self._group_label(group.label, group.count),
                expand=not group.collapsed,
            )
            for leaf in group.leaves:
                node.add_leaf(self._leaf_label(leaf), data=leaf.key)
            if group.omitted:
                node.add_leaf(Text(group.omitted, style=style("dim")))

    def set_filter(self, query: str) -> None:
        """Rebuild the tree keeping only leaves whose name matches ``query``.

        Groups stay visible with updated match counts; groups with matches
        expand. An empty query restores the full tree.
        """
        self._query = query
        self.root.remove_children()
        if not query:
            self._populate(self._project)
            return
        needle = query.lower()
        for group in self._project.groups:
            matches = tuple(leaf for leaf in group.leaves if needle in leaf.name.lower())
            node: TreeNode[str] = self.root.add(
                self._group_label(group.label, f"{len(matches)} match"),
                expand=bool(matches),
            )
            for leaf in matches:
                node.add_leaf(self._leaf_label(leaf), data=leaf.key)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        """Re-emit highlights as typed leaf messages."""
        if event.node.data is not None:
            self.post_message(self.LeafHighlighted(event.node.data))
