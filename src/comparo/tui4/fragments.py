"""Reusable Rich text grammar for glyphs, chips, and entity labels.

Every fragment resolves its colors through ``theme.style`` tokens.
"""

from rich.text import Text

from comparo.tui4.theme import style


def glyph(character: str, token: str) -> Text:
    """One colored glyph with its trailing space."""
    return Text(f"{character} ", style(token))


def chip(text: str, token: str, width: int = 0) -> Text:
    """One inline chip, optionally left-aligned to a column width."""
    rendered = f"{text:<{width}}" if width else text
    return Text(rendered, style(token))


def entity_label(
    name: str | None,
    entity_id: str,
    *,
    prefix: tuple[Text, ...] = (),
    badges: tuple[Text, ...] = (),
    include_id: bool = True,
) -> Text:
    """Build the standard entity row, optionally omitting its internal ID."""
    label = Text()
    for fragment in prefix:
        label.append_text(fragment)
    if name is not None:
        label.append(name)
        if include_id:
            label.append(f" {entity_id}", style("dim"))
    elif include_id:
        label.append(entity_id)
    for fragment in badges:
        label.append(" ")
        label.append_text(fragment)
    return label
