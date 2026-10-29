"""Reports and Settings fixtures — archive rows, run previews, sections."""

from __future__ import annotations

from comparo.tuibis.fixtures_launch import _l
from comparo.tuibis.viewmodels import KeyHint
from comparo.tuibis.viewmodels import ListItemVM
from comparo.tuibis.viewmodels import PanelVM
from comparo.tuibis.viewmodels import StyledLine

REPORTS_CHIPS: tuple[str, ...] = ("all", "failed", "passed", "advisory", "error")

REPORTS_BROWSE_HINTS: tuple[KeyHint, ...] = (
    KeyHint("↑↓", "select"),
    KeyHint("enter", "open"),
    KeyHint("/", "filter"),
    KeyHint("esc", "back"),
)

SETTINGS_HINTS: tuple[KeyHint, ...] = (
    KeyHint("↑↓", "section"),
    KeyHint("t", "toggle theme"),
)

_BLANK = _l(("", "fg"))


def _header(key: str, line: StyledLine) -> ListItemVM:
    """Build one non-navigable archive header row."""
    return ListItemVM(key=key, line=line, header=True)


def _run(key: str, line: StyledLine, search: str) -> ListItemVM:
    """Build one navigable archive run row."""
    return ListItemVM(key=key, line=line, search_text=search)


def archive_items() -> tuple[ListItemVM, ...]:
    """The archive rows: day headers plus six runs, cursor-navigable."""
    return (
        _header("hdr:today", _l(("TODAY", "label"), ("  27 jul", "dim"))),
        _run(
            "run:74ecaa",
            _l(
                ("x FAILED   ", "status.failed"),
                ("74ecaa ", "bright"),
                ("14:42  ", "muted"),
                ("▶ ", "glyph"),
                ("release-gate  ", "fg"),
                ("12 calls · ", "muted"),
                ("3 issues", "err"),
                (" · 31s · 9b7e3a1", "muted"),
            ),
            "74ecaa failed release-gate today 14:42",
        ),
        _run(
            "run:74d1b3",
            _l(
                ("+ PASSED   ", "status.passed"),
                ("74d1b3 ", "fg"),
                ("12:37  ", "muted"),
                ("▶ ", "glyph"),
                ("smoke-run     ", "fg"),
                ("8 calls · 0 issues · 18s · 9b7e3a1", "muted"),
            ),
            "74d1b3 passed smoke-run today 12:37",
        ),
        _run(
            "run:74c9e1",
            _l(
                ("~ ADVISORY ", "status.advisory"),
                ("74c9e1 ", "fg"),
                ("10:15  ", "muted"),
                ("▶ ", "glyph"),
                ("release-gate  ", "fg"),
                ("6 calls · 1 advisory · 22s · 9b7e3a1", "muted"),
            ),
            "74c9e1 advisory release-gate today 10:15",
        ),
        _run(
            "run:748f21",
            _l(
                ("! ERROR    ", "status.error"),
                ("748f21 ", "fg"),
                ("08:41  ", "muted"),
                ("» ", "glyph"),
                ("list-orders   ", "fg"),
                ("0 calls · transport error · 4s", "muted"),
            ),
            "748f21 error list-orders today 08:41 transport",
        ),
        _header("hdr:gap-1", _BLANK),
        _header("hdr:yesterday", _l(("YESTERDAY", "label"), ("  26 jul", "dim"))),
        _run(
            "run:742b9d",
            _l(
                ("+ PASSED   ", "status.passed"),
                ("742b9d ", "fg"),
                ("21:18  ", "muted"),
                ("▶ ", "glyph"),
                ("release-gate  ", "fg"),
                ("11 calls · 0 issues · 29s · 3d1c9f8", "muted"),
            ),
            "742b9d passed release-gate yesterday 21:18",
        ),
        _run(
            "run:73f2a0",
            _l(
                ("# CANCELLED", "status.cancelled"),
                (" 73f2a0 ", "muted"),
                ("11:27  ", "muted"),
                ("▶ ", "glyph"),
                ("plan-validate ", "muted"),
                ("6 calls · by user", "dim"),
            ),
            "73f2a0 cancelled plan-validate yesterday 11:27",
        ),
        _header("hdr:gap-2", _BLANK),
        _header(
            "hdr:7d",
            _l(("LAST 7 DAYS", "label"), ("  20-26 jul · 8 runs · 1 failed", "dim")),
        ),
    )


def _meta(label: str, *cells: tuple[str, str]) -> StyledLine:
    """Build one aligned preview metadata row."""
    return _l((f"{label:<10}", "muted"), *cells)


def _open_hint() -> StyledLine:
    """The standard `o open analysis` preview affordance row."""
    return _l((" o ", "chip.active"), (" open analysis", "bright"), ("  read-only", "muted"))


def _export_hint() -> StyledLine:
    """The standard export/copy preview affordance row."""
    return _l(
        ("e", "fg"),
        (" export json·junit·sarif·md", "muted"),
        ("      y", "fg"),
        (" copy run id", "muted"),
    )


def _pad(lines: tuple[StyledLine, ...], count: int) -> tuple[StyledLine, ...]:
    """Pad a preview to ``count`` rows so LineBlock updates stay in place."""
    return lines + tuple(StyledLine(()) for _ in range(count - len(lines)))


def report_previews() -> dict[str, PanelVM]:
    """One preview panel per archive run, all padded to equal row counts."""
    bodies: dict[str, tuple[StyledLine, ...]] = {
        "run:74ecaa": (
            _l(("x FAILED", "status.failed"), (" 3 issues · 7 of 12 calls", "bright")),
            _BLANK,
            _l(("1 ", "muted"), ("x ", "err"), ("Candidate 500 vs 2xx     3x", "fg")),
            _l(("2 ", "muted"), ("x ", "err"), ("Price format changed     3x", "fg")),
            _l(("3 ", "muted"), ("~ ", "warn"), ("Latency > 150ms          1x", "fg")),
            _BLANK,
            _meta("profile", ("▶ ", "glyph"), ("release-gate", "fg")),
            _meta(
                "topology",
                ("staging", "baseline"),
                (" ⇄ ", "muted"),
                ("production", "candidate"),
            ),
            _meta("commit", ("9b7e3a1 on main", "fg")),
            _meta("artifact", ("rep_01HX7Z6Y0P7D1K9S", "fg")),
            _meta("integrity", ("+ VERIFIED", "status.verified"), (" · sanitized", "dim")),
            _meta("coverage", ("12/12 · 0 omitted", "fg")),
            _BLANK,
            _open_hint(),
            _export_hint(),
        ),
        "run:74d1b3": (
            _l(("+ PASSED", "status.passed"), (" 0 issues · 8 of 8 calls", "bright")),
            _BLANK,
            _l(("no issues — every check green", "muted")),
            _BLANK,
            _meta("profile", ("▶ ", "glyph"), ("smoke-run", "fg")),
            _meta(
                "topology",
                ("staging", "baseline"),
                (" ⇄ ", "muted"),
                ("staging", "candidate"),
            ),
            _meta("commit", ("9b7e3a1 on main", "fg")),
            _meta("artifact", ("rep_01HX7Y2M4QK8D3VN", "fg")),
            _meta("integrity", ("+ VERIFIED", "status.verified"), (" · sanitized", "dim")),
            _meta("coverage", ("8/8 · 0 omitted", "fg")),
            _BLANK,
            _open_hint(),
            _export_hint(),
        ),
        "run:74c9e1": (
            _l(("~ ADVISORY", "status.advisory"), (" 1 advisory · 6 of 6 calls", "bright")),
            _BLANK,
            _l(("1 ", "muted"), ("~ ", "warn"), ("Latency > 150ms          1x", "fg")),
            _BLANK,
            _l(("advisories inform — they never gate", "dim")),
            _BLANK,
            _meta("profile", ("▶ ", "glyph"), ("release-gate", "fg")),
            _meta(
                "topology",
                ("staging", "baseline"),
                (" ⇄ ", "muted"),
                ("production", "candidate"),
            ),
            _meta("commit", ("9b7e3a1 on main", "fg")),
            _meta("artifact", ("rep_01HX7X9R2T5B7JQC", "fg")),
            _meta("integrity", ("+ VERIFIED", "status.verified"), (" · sanitized", "dim")),
            _meta("coverage", ("6/6 · 0 omitted", "fg")),
            _BLANK,
            _open_hint(),
            _export_hint(),
        ),
        "run:748f21": (
            _l(("! ERROR", "status.error"), (" transport error · 0 of 12 calls", "bright")),
            _BLANK,
            _l(("candidate refused the connection before any call was sent", "fg")),
            _l(("ECONNREFUSED api.production.internal:443 · 3 retries · 4s", "muted")),
            _l(("baseline was never contacted — nothing to compare", "muted")),
            _BLANK,
            _meta("profile", ("» ", "glyph"), ("list-orders", "fg")),
            _meta(
                "topology",
                ("staging", "baseline"),
                (" ⇄ ", "muted"),
                ("production", "candidate"),
            ),
            _meta("commit", ("9b7e3a1 on main", "fg")),
            _meta("artifact", ("rep_01HX7W5D8N2F4HTZ", "fg")),
            _meta("integrity", ("+ VERIFIED", "status.verified"), (" · sanitized", "dim")),
            _meta("coverage", ("0/12 · aborted at transport", "fg")),
            _BLANK,
            _l(("no analysis — this run produced no calls", "dim")),
            _l(
                ("y", "fg"),
                (" copy run id", "muted"),
                ("      e", "fg"),
                (" export json·md", "muted"),
            ),
        ),
        "run:742b9d": (
            _l(("+ PASSED", "status.passed"), (" 0 issues · 11 of 11 calls", "bright")),
            _BLANK,
            _l(("no issues — every check green", "muted")),
            _BLANK,
            _meta("profile", ("▶ ", "glyph"), ("release-gate", "fg")),
            _meta(
                "topology",
                ("staging", "baseline"),
                (" ⇄ ", "muted"),
                ("production", "candidate"),
            ),
            _meta("commit", ("3d1c9f8 on main", "fg")),
            _meta("artifact", ("rep_01HX5T8K1WQ9M2XB", "fg")),
            _meta("integrity", ("+ VERIFIED", "status.verified"), (" · sanitized", "dim")),
            _meta("coverage", ("11/11 · 0 omitted", "fg")),
            _BLANK,
            _open_hint(),
            _export_hint(),
        ),
        "run:73f2a0": (
            _l(("# CANCELLED", "status.cancelled"), (" by user · 6 of 12 calls", "bright")),
            _BLANK,
            _l(("stopped from the run screen at 11:27", "fg")),
            _l(("completed calls kept — partial evidence only", "muted")),
            _BLANK,
            _meta("profile", ("▶ ", "glyph"), ("plan-validate", "fg")),
            _meta(
                "topology",
                ("staging", "baseline"),
                (" ⇄ ", "muted"),
                ("production", "candidate"),
            ),
            _meta("commit", ("3d1c9f8 on main", "fg")),
            _meta("artifact", ("rep_01HX5J4C7RD2P8NM", "fg")),
            _meta("integrity", ("+ VERIFIED", "status.verified"), (" · sanitized", "dim")),
            _meta("coverage", ("6/12 · 6 never sent", "fg")),
            _BLANK,
            _l(
                (" o ", "chip.active"),
                (" open analysis", "bright"),
                ("  partial · read-only", "muted"),
            ),
            _export_hint(),
        ),
    }
    rows = max(len(lines) for lines in bodies.values())
    return {
        key: PanelVM(title=f"PREVIEW — {key.removeprefix('run:')}", lines=_pad(lines, rows))
        for key, lines in bodies.items()
    }


def settings_nav_items() -> tuple[ListItemVM, ...]:
    """The five settings sections plus the app-level footnote."""
    sections = (
        ("sec:appearance", "Appearance"),
        ("sec:keyboard", "Keyboard"),
        ("sec:interaction", "Interaction"),
        ("sec:plugins", "Plugins"),
        ("sec:updates", "Updates"),
    )
    items = tuple(
        ListItemVM(key=key, line=_l((label, "fg")), search_text=label.lower())
        for key, label in sections
    )
    return (
        *items,
        ListItemVM(key="nav:gap", line=_BLANK, header=True),
        ListItemVM(key="nav:note-1", line=_l(("app-level only —", "dim")), dimmed=True),
        ListItemVM(key="nav:note-2", line=_l(("never project config", "dim")), dimmed=True),
    )


def _mark(on: bool) -> tuple[str, str]:
    """One radio mark cell: filled when ``on``."""
    return ("(•)", "mark.on") if on else ("( )", "mark.off")


def appearance_lines(theme: str) -> tuple[StyledLine, ...]:
    """The APPEARANCE rows with the radio marks matching ``theme``."""
    dark = theme == "comparo-dark"
    return (
        _l(("THEME", "label"), ("   t toggles · applies live", "dim")),
        _l(
            _mark(dark),
            (" comparo dark", "bright" if dark else "fg"),
            ("    ", "fg"),
            _mark(not dark),
            (" comparo light", "bright" if not dark else "fg"),
            ("    ( ) terminal — inherit", "fg"),
        ),
        _BLANK,
        _l(("DENSITY", "label"), ("    (•) comfortable   ( ) compact", "fg")),
        _l(("GLYPHS", "label"), ("     (•) unicode ◆ » ⊞ ⌂   ( ) ascii * > # @", "fg")),
        _l(("MOTION", "label"), ("     (•) animated   ( ) reduced — plain counters", "fg")),
        _l(("HOME", "label"), ("       (•) explore   ( ) launch   ( ) reports", "fg")),
        _BLANK,
        _l(("PREVIEW", "label"), ("    changes apply live", "dim")),
        _l(
            ("+ PASSED", "status.passed"),
            ("  ", "fg"),
            ("x FAILED", "status.failed"),
            ("  ", "fg"),
            ("▌baseline", "baseline"),
            (" ⇄ ", "muted"),
            ("▌candidate", "candidate"),
            ("  ", "fg"),
            ("» ", "glyph"),
            ("Price quote ", "fg"),
            ("GET", "method.GET"),
        ),
        _BLANK,
        _l(("stored per user — never in the project", "dim")),
    )


def appearance_panel(theme: str) -> PanelVM:
    """The APPEARANCE section panel for the active ``theme``."""
    return PanelVM(
        title="APPEARANCE — stored per user, never in the project",
        hint="t toggle theme",
        lines=appearance_lines(theme),
    )


def _key_row(command: str, context: str, key: str, tone: str = "fg") -> StyledLine:
    """One keymap table row: command, context, key."""
    return _l((command, "fg", 22), (context, "muted", 18), (key, tone))


def keyboard_panel() -> PanelVM:
    """The KEYBOARD section panel: the command/context/key table."""
    return PanelVM(
        title="KEYBOARD — every command, its context, its key",
        lines=(
            _l(("COMMAND", "label", 22), ("CONTEXT", "label", 18), ("KEY", "label")),
            _key_row("quit", "everywhere", "q reserved — always quits", "bright"),
            _key_row("back / close", "everywhere", "esc"),
            _key_row("workspaces", "everywhere", "1 2 3 4"),
            _key_row("move cursor", "lists", "↑ ↓"),
            _key_row("filter", "lists", "/"),
            _key_row("select", "lists", "space"),
            _key_row("open / activate", "lists", "enter"),
            _key_row("admit plan", "launch · scope", "p"),
            _key_row("execute", "launch · plan", "x"),
            _key_row("stage forward / back", "launch", "] ["),
            _key_row("next index", "analyse", "i"),
            _key_row("deep dive response", "analyse", "z"),
            _key_row("sent diff", "analyse", "d"),
            _key_row("open analysis", "reports", "o"),
            _key_row("toggle theme", "settings", "t"),
            _BLANK,
            _l(("rebinding lands with the keymap editor", "dim")),
        ),
    )


def interaction_panel() -> PanelVM:
    """The INTERACTION section panel: mouse, confirmations, home."""
    return PanelVM(
        title="INTERACTION — input behavior",
        lines=(
            _l(("MOUSE", "label"), ("              (•) on   ( ) keyboard only", "fg")),
            _l(
                ("CONFIRM WRITES", "label"),
                ("     (•) always prompt   ( ) execution profile decides", "fg"),
            ),
            _l(
                ("DEFAULT WORKSPACE", "label"),
                ("  (•) explore   ( ) launch   ( ) reports", "fg"),
            ),
            _BLANK,
            _l(("write-capable requests always show the shield prompt", "dim")),
        ),
    )


def plugins_panel() -> PanelVM:
    """The PLUGINS section panel: empty state plus the roadmap note."""
    return PanelVM(
        title="PLUGINS",
        lines=(
            _l(("no plugins installed", "muted")),
            _BLANK,
            _l(("plugin API lands after 1.0 — planned surfaces:", "fg")),
            _l(("  diff renderers · exporters · auth helpers", "muted")),
            _BLANK,
            _l(("plugins never run during a diff — render and export only", "dim")),
        ),
    )


def updates_panel() -> PanelVM:
    """The UPDATES section panel: version, channel, status."""
    return PanelVM(
        title="UPDATES",
        lines=(
            _l(("VERSION", "label"), ("    comparo 1.0.0", "fg")),
            _l(("CHANNEL", "label"), ("    (•) stable   ( ) edge", "fg")),
            _l(
                ("STATUS", "label"),
                ("     ", "fg"),
                ("up to date", "ok"),
                (" · checked 27 jul 14:02", "muted"),
            ),
            _BLANK,
            _l(
                (" u ", "chip.active"),
                (" check now", "bright"),
                ("  notes open in browser", "muted"),
            ),
        ),
    )


def section_panels(theme: str) -> dict[str, PanelVM]:
    """All five settings section panels keyed by nav key."""
    return {
        "sec:appearance": appearance_panel(theme),
        "sec:keyboard": keyboard_panel(),
        "sec:interaction": interaction_panel(),
        "sec:plugins": plugins_panel(),
        "sec:updates": updates_panel(),
    }
