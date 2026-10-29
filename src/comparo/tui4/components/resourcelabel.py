"""One resource row grammar shared by Explorer, selection and execution views."""

from rich.text import Text

from comparo.application import ResourceCoordinate
from comparo.tui4.components.badge import BadgeEmphasis
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.badge import badge_text
from comparo.tui4.fragments import chip
from comparo.tui4.fragments import entity_label
from comparo.tui4.fragments import glyph
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.theme import kind_glyph

_ROLE_BADGES = {
    "baseline": ("base", BadgeTone.BASELINE),
    "candidate": ("cand", BadgeTone.CANDIDATE),
}
_LOCALITY_BADGES = {
    "local": ("local", BadgeTone.SUCCESS, BadgeEmphasis.QUIET),
    "live": ("live", BadgeTone.DANGER, BadgeEmphasis.SOLID),
}


def resource_label(
    entry: ResourceEntry, execution_environment: ResourceCoordinate | None = None
) -> Text:
    """Render the established entity glyph, method color and resource badges."""
    summary = entry.summary
    facts = entry.facts
    display_name = summary.name or f"Unnamed {summary.coordinate.kind}"
    character, token = kind_glyph(summary.coordinate.kind)
    prefix: list[Text] = [glyph(character, token)]
    if facts.method is not None:
        prefix.append(chip(facts.method, f"method.{facts.method}", width=7))
    badges: list[Text] = []
    context = execution_environment
    if context is not None and summary.coordinate == context:
        badges.append(badge_text("exec", tone=BadgeTone.ACTIVE, emphasis=BadgeEmphasis.SOLID))
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
