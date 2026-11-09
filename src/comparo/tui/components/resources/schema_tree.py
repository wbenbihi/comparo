"""Expandable JSON Schema property inspection from safe structural projections."""

import json
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.widgets import Tree

from comparo.tui.components.inspector import InspectorColumns
from comparo.tui.components.inspector import InspectorSection
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.table import Table
from comparo.tui.components.treemenu import TreeMenu
from comparo.tui.components.typography import Caption
from comparo.tui.models.navigation import MenuNode
from comparo.tui.models.navigation import TreePosition
from comparo.tui.models.resource_presenters import SchemaProperty
from comparo.tui.models.schema_document import SchemaNode
from comparo.tui.theme import style


def property_key(prop: SchemaProperty | SchemaNode) -> str:
    """Distinguish dotted names and properties nested under array items."""
    return json.dumps(prop.schema_path or prop.path)


class SchemaPropertyTree(TreeMenu[str]):
    """Native expandable structure whose branch nodes can also be inspected."""

    DEFAULT_CSS = "SchemaPropertyTree { height: 22; width: 100%; }"

    def __init__(self, properties: tuple[SchemaProperty | SchemaNode, ...]) -> None:
        """Find parents by exact schema coordinate, never by dotted display text."""
        self._properties = {property_key(prop): prop for prop in properties}
        children: dict[str | None, list[str]] = {None: []}
        for key, prop in self._properties.items():
            parents = [
                candidate
                for candidate, other in self._properties.items()
                if len(other.schema_path) < len(prop.schema_path)
                and prop.schema_path[: len(other.schema_path)] == other.schema_path
            ]
            parent = (
                max(parents, key=lambda item: len(self._properties[item].schema_path))
                if parents
                else None
            )
            children.setdefault(parent, []).append(key)

        def node(key: str) -> MenuNode[str]:
            prop = self._properties[key]
            label = Text(prop.path[-1] if prop.path else "root", style("fg"))
            label.append(f" · {prop.type}", style("kind.schema"))
            if not isinstance(prop, SchemaNode) or prop.role == "property":
                label.append(" · required" if prop.required else " · optional", style("muted"))
            if isinstance(prop, SchemaNode) and prop.reference_state:
                label.append(
                    f" · {prop.reference_state}",
                    style("warn") if prop.reference_state != "resolved locally" else style("muted"),
                )
            return MenuNode(key, label, tuple(node(child) for child in children.get(key, ())))

        super().__init__(
            *(node(key) for key in children[None]), initial=next(iter(self._properties), None)
        )

    @override
    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        """Inspect both object branches and scalar leaves."""
        event.stop()
        if event.node.data is not None:
            self.post_message(self.Highlighted(self, event.node.data))

    @override
    def restore_position(self, position: TreePosition[str], *, cursor: str | None = None) -> None:
        """Synchronize the definition when history restores a tree cursor."""
        super().restore_position(position, cursor=cursor)
        if self.cursor_id is not None:
            self.post_message(self.Highlighted(self, self.cursor_id))


class SchemaPropertyBrowser(Vertical):
    """A property tree paired with the selected property's validation contract."""

    DEFAULT_CSS = """
    SchemaPropertyBrowser { height: auto; width: 100%; }
    SchemaPropertyBrowser Table { height: auto; max-height: 6; }
    """

    def __init__(self, properties: tuple[SchemaProperty | SchemaNode, ...]) -> None:
        """Retain only structural facts admitted by the application outline."""
        super().__init__()
        self._properties = {property_key(prop): prop for prop in properties}

    @override
    def compose(self) -> ComposeResult:
        """Mount the native tree and stable definition pane."""
        with InspectorColumns():
            yield SchemaPropertyTree(tuple(self._properties.values()))
            yield InspectorSection("Definition", KeyValueList())
        references = tuple(
            (key, prop)
            for key, prop in self._properties.items()
            if isinstance(prop, SchemaNode) and prop.reference
        )
        if references:
            yield InspectorSection(
                "References · Enter opens the declaration",
                Table(
                    columns=(
                        Column("path", "Path"),
                        Column("reference", "$ref"),
                        Column("state", "State"),
                    ),
                    records=tuple(
                        Record(
                            key,
                            (
                                "/".join(prop.source_path) or "#",
                                prop.reference,
                                prop.reference_state,
                            ),
                        )
                        for key, prop in references
                    ),
                    id="schema-references",
                ),
            )
        cycles = sum(
            isinstance(prop, SchemaNode) and prop.reference_state == "cycle"
            for prop in self._properties.values()
        )
        yield Caption(
            f"{len(references)} reference locations · {cycles} recursive branches stopped"
            " · No validation sample provided"
        )

    def on_table_row_selected(self, event: Table.RowSelected[str]) -> None:
        """Open a reference at its exact location in the same schema tree."""
        event.stop()
        tree = self.query_one(SchemaPropertyTree)
        tree.reveal(event.row)
        tree.focus()
        self.on_tree_menu_highlighted(TreeMenu.Highlighted(tree, event.row))

    def on_tree_menu_highlighted(self, event: TreeMenu.Highlighted[str]) -> None:
        """Render exact constraints and a JSON Pointer into the authored schema."""
        event.stop()
        prop = self._properties.get(event.item)
        if prop is None:
            return
        pointer = "#/" + "/".join(
            part.replace("~", "~0").replace("/", "~1")
            for part in (prop.source_path if isinstance(prop, SchemaNode) else prop.schema_path)
        )
        self.query_one(KeyValueList).entries = (
            Field("Property", Content(" / ".join(prop.path))),
            Field("Type", prop.type),
            Field(
                "Presence",
                "—"
                if isinstance(prop, SchemaNode) and prop.role != "property"
                else "required"
                if prop.required
                else "optional",
            ),
            Field("Structure", prop.shape),
            Field(
                "Constraints",
                Content("\n".join(prop.constraints) or "No exact constraints declared."),
            ),
            Field("Definition", Content(pointer)),
            *((Field("Source", prop.source),) if isinstance(prop, SchemaNode) else ()),
            *(
                (Field("$ref", Content(prop.reference)), Field("Reference", prop.reference_state))
                if isinstance(prop, SchemaNode) and prop.reference
                else ()
            ),
        )
