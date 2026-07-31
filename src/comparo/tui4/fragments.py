"""Reusable Rich text grammar — pills, glyphs, entity labels.

Every fragment resolves its colors through ``theme.style`` tokens.
"""

from rich.text import Text

from comparo.tui4.theme import PILL_LEFT
from comparo.tui4.theme import PILL_RIGHT
from comparo.tui4.theme import style


def glyph(character: str, token: str) -> Text:
    """One colored glyph with its trailing space."""
    return Text(f"{character} ", style(token))


def pill(text: str, token: str) -> Text:
    """One rounded pill: half-circle caps drawn in the pill background color."""
    resolved = style(token)
    if " on " in resolved:
        _, background = resolved.rsplit(" on ", 1)
        fragment = Text()
        fragment.append(PILL_LEFT, background)
        fragment.append(text, resolved)
        fragment.append(PILL_RIGHT, background)
        return fragment
    return Text(f" {text} ", resolved)


def chip(text: str, token: str, width: int = 0) -> Text:
    """One inline chip, optionally left-aligned to a column width."""
    rendered = f"{text:<{width}}" if width else text
    return Text(rendered, style(token))


def entity_label(
    name: str | None,
    entity_id: str,
    *,
    prefix: tuple[Text, ...] = (),
    pills: tuple[Text, ...] = (),
) -> Text:
    """`<prefix…> name id <pills…>` — the standard list/tree row grammar."""
    label = Text()
    for fragment in prefix:
        label.append_text(fragment)
    if name is not None:
        label.append(name)
        label.append(f" {entity_id}", style("dim"))
    else:
        label.append(entity_id)
    for fragment in pills:
        label.append(" ")
        label.append_text(fragment)
    return label
