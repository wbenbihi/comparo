"""The comparo palette — single source of truth for every color in tuibis.

Widgets style themselves through tcss classes defined in ``styles.tcss``;
Rich text fragments (tree labels, table cells) use the ``style()`` helper.
Both read from the tokens below, so the palette changes in exactly one place.
"""

from __future__ import annotations

from textual.theme import Theme

INK = "#0B0F14"
SURFACE = "#121820"
PANEL = "#18202C"
BORDER = "#243044"
FG = "#D6E1EC"
BRIGHT = "#EAF2FA"
MUTED = "#68809C"
DIM = "#3D4F66"
LABEL = "#8FA6C0"
GLYPH = "#7E96B5"
BLUE = "#6CA9F0"
TEAL = "#4DD0C9"
AMBER = "#E8B44C"
SLATE = "#8299B3"
STEEL = "#9FB6D4"
PILL_GREEN_BG = "#11291B"
PILL_RED_BG = "#2B0F16"
PILL_AMBER_BG = "#2B2109"
PILL_TEAL_BG = "#0D2B29"
PILL_SLATE_BG = "#1C2434"

PILL_LEFT = "◖"
PILL_RIGHT = "◗"

BASELINE = "#4FD6E3"
CANDIDATE = "#C89BF5"

OK = "#7BD88F"
WARN = "#F0C060"
ERR = "#F2637E"
ERROR_OP = "#FF9E64"

GLYPH_PROJECT = "⌂"
GLYPH_ENVIRONMENT = "◉"
GLYPH_REQUEST = "⇄"
GLYPH_MATRIX = "▦"
GLYPH_SCHEMA = "§"
GLYPH_VALUE = "$"
GLYPH_SCENARIO = "⇉"
GLYPH_ASSERTION = "✓"
GLYPH_DIFF = "Δ"
GLYPH_GATE = "‖"
GLYPH_SAFETY = "⊘"
GLYPH_REDACTION = "▒"
GLYPH_ARTIFACT = "▤"
GLYPH_EXECUTION = "▶"
GLYPH_UNKNOWN = "•"

METHOD_COLORS: dict[str, str] = {
    "GET": "#6BD48C",
    "POST": "#E8B44C",
    "PUT": "#6CA9F0",
    "PATCH": "#4DD0C9",
    "DELETE": "#F2637E",
    "HEAD": "#8299B3",
    "OPTIONS": "#8299B3",
}

_STYLES: dict[str, str] = {
    "fg": FG,
    "bright": f"bold {BRIGHT}",
    "muted": MUTED,
    "dim": DIM,
    "label": f"bold {LABEL}",
    "glyph": GLYPH,
    "baseline": BASELINE,
    "candidate": CANDIDATE,
    "ok": OK,
    "warn": WARN,
    "err": ERR,
    "error-op": ERROR_OP,
    "pill.matrix": f"{'#9FB6D4'} on {'#1E2A3C'}",
    "pill.live": f"{OK} on {'#11291B'}",
    "pill.default": f"{LABEL} on {'#1C2434'}",
    "pill.baseline": f"{BASELINE} on {'#0E2A31'}",
    "pill.candidate": f"{CANDIDATE} on {'#241B33'}",
    "chip.active": f"bold {'#08222B'} on {BASELINE}",
}

_STYLES.update(
    {
        "pill.matrix": f"{WARN} on {PILL_AMBER_BG}",
        "pill.local": f"{OK} on {PILL_GREEN_BG}",
        "pill.live": f"{ERR} on {PILL_RED_BG}",
        "pill.stream": f"{TEAL} on {PILL_TEAL_BG}",
        "pill.default": f"{LABEL} on {PILL_SLATE_BG}",
    }
)

_STYLES.update(
    {
        "kind.project": CANDIDATE,
        "kind.environment": OK,
        "kind.request": BASELINE,
        "kind.matrix": WARN,
        "kind.schema": BLUE,
        "kind.value": TEAL,
        "kind.scenario": LABEL,
        "kind.assertion": OK,
        "kind.diff": AMBER,
        "kind.gate": ERROR_OP,
        "kind.safety": ERR,
        "kind.redaction": SLATE,
        "kind.artifact": STEEL,
        "kind.execution": BRIGHT,
    }
)

KIND_GLYPHS: dict[str, tuple[str, str]] = {
    "Project": (GLYPH_PROJECT, "kind.project"),
    "Environment": (GLYPH_ENVIRONMENT, "kind.environment"),
    "Request": (GLYPH_REQUEST, "kind.request"),
    "Matrix": (GLYPH_MATRIX, "kind.matrix"),
    "Schema": (GLYPH_SCHEMA, "kind.schema"),
    "Value": (GLYPH_VALUE, "kind.value"),
    "Scenario": (GLYPH_SCENARIO, "kind.scenario"),
    "AssertionProfile": (GLYPH_ASSERTION, "kind.assertion"),
    "DiffProfile": (GLYPH_DIFF, "kind.diff"),
    "GateProfile": (GLYPH_GATE, "kind.gate"),
    "SafetyProfile": (GLYPH_SAFETY, "kind.safety"),
    "RedactionProfile": (GLYPH_REDACTION, "kind.redaction"),
    "ArtifactProfile": (GLYPH_ARTIFACT, "kind.artifact"),
    "ExecutionProfile": (GLYPH_EXECUTION, "kind.execution"),
}
_DEFAULT_GLYPH = (GLYPH_UNKNOWN, "muted")


def kind_glyph(kind: str) -> tuple[str, str]:
    """Glyph character and style token for one resource kind."""
    return KIND_GLYPHS.get(kind, _DEFAULT_GLYPH)


def style(name: str) -> str:
    """Resolve a semantic style name to a Rich style string."""
    if name in _STYLES:
        return _STYLES[name]
    if name.startswith("method."):
        return f"bold {METHOD_COLORS.get(name.removeprefix('method.'), MUTED)}"
    raise KeyError(f"unknown style token: {name}")


COMPARO_DARK = Theme(
    name="comparo-dark",
    primary=BASELINE,
    secondary=CANDIDATE,
    background=INK,
    surface=SURFACE,
    panel=PANEL,
    foreground=FG,
    success=OK,
    warning=WARN,
    error=ERR,
    accent=BASELINE,
    dark=True,
    variables={
        "comparo-border": BORDER,
        "comparo-muted": MUTED,
        "comparo-dim": DIM,
        "comparo-label": LABEL,
    },
)

_STYLES.update(
    {
        "status.passed": OK,
        "status.advisory": WARN,
        "status.failed": ERR,
        "status.error": ERROR_OP,
        "status.running": BASELINE,
        "status.queued": DIM,
        "status.skipped": DIM,
        "status.cancelled": DIM,
        "status.redacted": WARN,
        "status.drift": ERR,
        "status.same": OK,
        "status.outbound": WARN,
        "status.verified": OK,
        "diff.add": f"{OK} on #0D2117",
        "diff.del": f"{ERR} on #250E15",
        "diff.ctx": MUTED,
        "diff.hunk": f"{BASELINE} on #0E1D28",
        "diff.eadd": "bold #0D2117 on #7BD88F",
        "diff.edel": "bold #250E15 on #F2637E",
        "chip.warn": f"bold {'#2B1F04'} on {WARN}",
        "chip.plain": f"{FG} on {'#1C2A3A'}",
        "mark.on": BASELINE,
        "mark.off": MUTED,
        "cursor": BASELINE,
    }
)


COMPARO_LIGHT = Theme(
    name="comparo-light",
    primary="#0B7E93",
    secondary="#7C3AED",
    background="#F7F9FC",
    surface="#EDF1F6",
    panel="#E4EAF1",
    foreground="#22303F",
    success="#177A3D",
    warning="#9A6700",
    error="#C7365A",
    accent="#0B7E93",
    dark=False,
    variables={
        "comparo-border": "#C7D3E0",
        "comparo-muted": "#5C7189",
        "comparo-dim": "#97A6B8",
        "comparo-label": "#46608A",
    },
)
