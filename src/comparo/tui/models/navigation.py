"""Immutable semantic nodes for tree-based navigation components."""

from dataclasses import dataclass

from rich.text import Text
from rich.text import TextType


@dataclass(frozen=True)
class MenuNode[T: str]:
    """One tree entry; a node with no children is a selectable leaf."""

    id: T
    label: TextType
    children: tuple["MenuNode[T]", ...] = ()
    glyph: str | None = None

    @property
    def is_leaf(self) -> bool:
        """Return whether the node can be selected instead of expanded."""
        return not self.children

    @property
    def prompt(self) -> Text:
        """Build the Rich label consumed by Textual's tree widget."""
        if self.glyph is None:
            return (
                Text.from_markup(self.label) if isinstance(self.label, str) else self.label.copy()
            )
        return Text.assemble(f"{self.glyph} ", self.label)
