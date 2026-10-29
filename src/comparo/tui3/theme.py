"""The v0 palette — single color source for tui3.

Tokens mirror docs/design/comparo-*-mockup.html exactly: panel greys, the
same/drift/skip triad, accent blue, axis violet, and one color per entity
kind. ``style()`` resolves semantic names for Rich fragments; the tcss reads
the same values through the registered themes.
"""

from __future__ import annotations

from textual.theme import Theme

BG = "#0d1017"
PANEL = "#131822"
PANEL_HI = "#182030"
LINE = "#28313f"
FG = "#c5d0de"
FG_HI = "#eaf0f8"
DIM = "#5c6878"
SAME = "#48a97f"
DRIFT = "#e0566b"
SKIP = "#5c6878"
ACCENT = "#6d9eff"
AXIS = "#a98bf0"
WARN = "#d99b3f"
DANGER = "#ff5f52"

L_BG = "#f3f5f8"
L_LINE = "#d3dae3"
L_FG = "#2a3441"
L_FG_HI = "#101823"
L_DIM = "#7c8794"
L_SAME = "#1f7a55"
L_DRIFT = "#c23a52"
L_ACCENT = "#3b6fe0"
L_AXIS = "#7a56d6"
L_WARN = "#9a6700"
L_DANGER = "#b3261e"

KIND = {
    "env": SAME,
    "req": FG_HI,
    "val": SAME,
    "mat": AXIS,
    "sch": ACCENT,
    "diff": WARN,
    "assert": SAME,
    "exec": ACCENT,
}

_KIND_LIGHT = {
    "env": L_SAME,
    "req": L_FG_HI,
    "val": L_SAME,
    "mat": L_AXIS,
    "sch": L_ACCENT,
    "diff": L_WARN,
    "assert": L_SAME,
    "exec": L_ACCENT,
}

_STYLES: dict[str, str] = {
    "fg": FG,
    "hi": FG_HI,
    "b": f"bold {FG_HI}",
    "dim": DIM,
    "ph": f"bold {DIM}",
    "same": SAME,
    "same.b": f"bold {SAME}",
    "drift": DRIFT,
    "drift.b": f"bold {DRIFT}",
    "skip": SKIP,
    "acc": ACCENT,
    "acc.b": f"bold {ACCENT}",
    "axis": AXIS,
    "axis.b": f"bold {AXIS}",
    "warn": WARN,
    "warn.b": f"bold {WARN}",
    "danger": DANGER,
    "sel": f"{FG_HI} on #24354f",
    "seg.on": f"bold {ACCENT}",
    "pill.default": SAME,
    "pill.live": f"bold {DANGER}",
    "pill.replay": f"bold {WARN}",
    "badge.w": WARN,
    "badge.s": SKIP,
    "method.get": f"bold {ACCENT}",
    "method.post": f"bold {WARN}",
    "diff.add": f"{SAME} on #14251e",
    "diff.del": f"{DRIFT} on #261119",
    "diff.hunk": f"{ACCENT} on #131c2e",
    "diff.ctx": DIM,
    "diff.em": f"bold #0d1017 on {DRIFT}",
    "diff.ema": f"bold #0d1017 on {SAME}",
    "cmd": DIM,
    "cmd.b": f"bold {FG_HI}",
    "btn": f"bold {ACCENT}",
    "btn.primary": f"bold #0d1017 on {ACCENT}",
    "btn.blocked": f"bold {DRIFT}",
    "prog.on": f"{ACCENT} on {ACCENT}",
    "prog.off": f"{LINE} on #0b0e14",
}

_STYLES_LIGHT: dict[str, str] = {
    **_STYLES,
    "fg": L_FG,
    "hi": L_FG_HI,
    "b": f"bold {L_FG_HI}",
    "dim": L_DIM,
    "ph": f"bold {L_DIM}",
    "same": L_SAME,
    "same.b": f"bold {L_SAME}",
    "drift": L_DRIFT,
    "drift.b": f"bold {L_DRIFT}",
    "skip": L_DIM,
    "acc": L_ACCENT,
    "acc.b": f"bold {L_ACCENT}",
    "axis": L_AXIS,
    "axis.b": f"bold {L_AXIS}",
    "warn": L_WARN,
    "warn.b": f"bold {L_WARN}",
    "danger": L_DANGER,
    "sel": f"{L_FG_HI} on #cfe0ff",
    "seg.on": f"bold {L_ACCENT}",
    "pill.default": L_SAME,
    "pill.live": f"bold {L_DANGER}",
    "pill.replay": f"bold {L_WARN}",
    "badge.w": L_WARN,
    "badge.s": L_DIM,
    "method.get": f"bold {L_ACCENT}",
    "method.post": f"bold {L_WARN}",
    "diff.add": f"{L_SAME} on #e0f2e8",
    "diff.del": f"{L_DRIFT} on #fbe3e8",
    "diff.hunk": f"{L_ACCENT} on #e3ebfb",
    "diff.ctx": L_DIM,
    "diff.em": f"bold #ffffff on {L_DRIFT}",
    "diff.ema": f"bold #ffffff on {L_SAME}",
    "cmd": L_DIM,
    "cmd.b": f"bold {L_FG_HI}",
    "btn": f"bold {L_ACCENT}",
    "btn.primary": f"bold #ffffff on {L_ACCENT}",
    "btn.blocked": f"bold {L_DRIFT}",
    "prog.on": f"{L_ACCENT} on {L_ACCENT}",
    "prog.off": f"{L_LINE} on #e6ebf2",
}

_light_active = False


def set_palette(light: bool) -> None:
    """Point ``style()`` at the palette matching the active theme."""
    global _light_active
    _light_active = light


def style(name: str) -> str:
    """Resolve a semantic style token to a Rich style string."""
    styles = _STYLES_LIGHT if _light_active else _STYLES
    kinds = _KIND_LIGHT if _light_active else KIND
    if name in styles:
        return styles[name]
    if name.startswith("k."):
        return kinds[name.removeprefix("k.")]
    if name.startswith("k+"):
        return f"bold {kinds[name.removeprefix('k+')]}"
    raise KeyError(f"unknown style token: {name}")


TUI3_DARK = Theme(
    name="tui3-dark",
    primary=ACCENT,
    secondary=AXIS,
    background=BG,
    surface=PANEL,
    panel=PANEL_HI,
    foreground=FG,
    success=SAME,
    warning=WARN,
    error=DRIFT,
    accent=ACCENT,
    dark=True,
    variables={
        "t3-line": LINE,
        "t3-dim": DIM,
        "t3-panel-hi": PANEL_HI,
        "t3-cursor": "#24354f",
        "t3-hover": "#1a2334",
        "t3-input-focus": "#10161f",
    },
)

TUI3_LIGHT = Theme(
    name="tui3-light",
    primary="#3b6fe0",
    secondary="#7a56d6",
    background="#f3f5f8",
    surface="#ffffff",
    panel="#eef2f7",
    foreground="#2a3441",
    success="#1f7a55",
    warning="#9a6700",
    error="#c23a52",
    accent="#3b6fe0",
    dark=False,
    variables={
        "t3-line": L_LINE,
        "t3-dim": L_DIM,
        "t3-panel-hi": "#eef2f7",
        "t3-cursor": "#cfe0ff",
        "t3-hover": "#e3ebf6",
        "t3-input-focus": "#ffffff",
    },
)
