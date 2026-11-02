"""Immutable field rows shared by TUI presentation models and widgets."""

from dataclasses import dataclass

from textual.content import Content


@dataclass(frozen=True)
class Field:
    """One key/value row with an optional semantic tone."""

    key: str
    value: str | Content
    tone: str | None = None
