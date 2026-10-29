"""Rich-text fragment builders — the one place row content gets styled.

Trees, tables, and text rows assemble their content exclusively from these
helpers so pills, methods, glyphs, and provenance render identically on
every screen.
"""

from __future__ import annotations

from rich.text import Text

from comparo.tuibis.theme import style
from comparo.tuibis.viewmodels import EntityKind
from comparo.tuibis.viewmodels import Pill
from comparo.tuibis.viewmodels import ProvenancedValue

GLYPHS: dict[EntityKind, str] = {
    EntityKind.PROJECT: "◆",
    EntityKind.REQUEST: "»",
    EntityKind.SCENARIO: "¶",
    EntityKind.ENVIRONMENT: "⌂",
    EntityKind.MATRIX: "⊞",
    EntityKind.SCHEMA: "§",
    EntityKind.ASSERTION_PROFILE: "◈",
    EntityKind.DIFF_PROFILE: "Δ",
    EntityKind.EXECUTION_PROFILE: "▶",
    EntityKind.VALUE: "$",
    EntityKind.REPORT: "▤",
}


def glyph(kind: EntityKind) -> Text:
    """Entity glyph fragment."""
    return Text(GLYPHS[kind], style=style("glyph"))


def method(name: str, pad: int = 0) -> Text:
    """Method-colored fragment, optionally padded."""
    text = f"{name:<{pad}}" if pad else name
    return Text(text, style=style(f"method.{name}"))


def pill(spec: Pill) -> Text:
    """Single pill fragment."""
    return Text(f" {spec.text} ", style=style(f"pill.{spec.kind.value}"))


def pills(specs: tuple[Pill, ...]) -> Text:
    """Space-separated run of pills."""
    out = Text()
    for index, spec in enumerate(specs):
        if index:
            out.append(" ")
        out.append_text(pill(spec))
    return out


def entity(kind: EntityKind, name: str, *, bold: bool = False) -> Text:
    """Glyph + name fragment for a resource."""
    out = glyph(kind)
    out.append(" ")
    out.append(name, style=style("bright") if bold else style("fg"))
    return out


def provenance(note: str) -> Text:
    """Dimmed ``← source`` annotation."""
    return Text(f"← {note}", style=style("dim")) if note else Text()


def kv_row(row: ProvenancedValue, key_width: int, value_width: int) -> Text:
    """Aligned key/value row with provenance."""
    out = Text()
    out.append(f"{row.key:<{key_width}}", style=style("muted"))
    value_style = style("warn") if row.redacted else style("fg")
    out.append(f"{row.value:<{value_width}}", style=value_style)
    out.append_text(provenance(row.provenance))
    return out


def label(text: str, note: str = "") -> Text:
    """Section label fragment with optional note."""
    out = Text(text, style=style("label"))
    if note:
        out.append(f"  {note}", style=style("muted"))
    return out


def muted(text: str) -> Text:
    """Muted text fragment."""
    return Text(text, style=style("muted"))


def dim(text: str) -> Text:
    """Dim text fragment."""
    return Text(text, style=style("dim"))


def plain(text: str) -> Text:
    """Foreground text fragment."""
    return Text(text, style=style("fg"))


STATUS_GLYPHS: dict[str, tuple[str, str]] = {
    "passed": ("+", "PASSED"),
    "advisory": ("~", "ADVISORY"),
    "failed": ("x", "FAILED"),
    "error": ("!", "ERROR"),
    "running": (">", "RUNNING"),
    "queued": (".", "QUEUED"),
    "skipped": ("-", "SKIPPED"),
    "cancelled": ("#", "CANCELLED"),
    "redacted": ("*", "REDACTED"),
    "drift": ("x", "DRIFT"),
    "same": ("=", "SAME"),
    "outbound": ("≠", "OUTBOUND"),
    "verified": ("+", "VERIFIED"),
}


def status(name: str, *, word: bool = True, pad: int = 0) -> Text:
    """Status pill fragment — glyph + word, never color alone."""
    glyph_char, label_text = STATUS_GLYPHS[name]
    text = f"{glyph_char} {label_text}" if word else glyph_char
    if pad:
        text = f"{text:<{pad}}"
    return Text(text, style=style(f"status.{name}"))


def mark(selected: bool) -> Text:
    """Selection mark fragment: [x] cyan when selected, [ ] muted."""
    if selected:
        return Text("[x]", style=style("mark.on"))
    return Text("[ ]", style=style("mark.off"))


def cursor(active: bool) -> Text:
    """Cursor bar fragment: ▌ on the highlighted row."""
    return Text("▌ " if active else "  ", style=style("cursor"))


def chip(text: str, kind: str = "active") -> Text:
    """Reverse-video chip fragment (active tab, warnings, plain badges)."""
    return Text(f" {text} ", style=style(f"chip.{kind}"))


def lane(name: str, side: str) -> Text:
    """Lane-colored fragment: side is 'baseline' or 'candidate'."""
    return Text(name, style=style(side))
