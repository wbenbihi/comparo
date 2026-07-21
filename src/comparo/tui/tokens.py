"""Palette, lookup-map, and footer-hint constants for the comparo TUI.

Pure data: colours for comparo-ink, method/mode/kind lookup tables, the
per-screen footer key-hint tuples, and the help-overlay text. Nothing here
references a view/modal class or ComparoApp — this module sits below
comparo.tui.render in the dependency order (constants <- functions <- views).
"""

from typing import Literal

from comparo.core.health import Health
from comparo.core.models import AssertionProfile
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.models import Instance
from comparo.core.models import Matrix
from comparo.core.models import Request
from comparo.core.models import Schema

__all__ = [
    "_ACCENT",
    "_ADD_BG",
    "_ASSERT_GLYPH",
    "_AXIS",
    "_DANGER",
    "_DEL_BG",
    "_DIFF_BG",
    "_DIFF_FIELDS_KEYS",
    "_DIFF_PREPARE_KEYS",
    "_DIFF_REQ_KEYS",
    "_DIFF_RULES_KEYS",
    "_DIFF_RUNNING_KEYS",
    "_DIM",
    "_DOCS_URL",
    "_DRIFT",
    "_ENV_KEYS",
    "_ERROR_KEYS",
    "_EXEC_CELL_KEYS",
    "_EXEC_DIFF_KEYS",
    "_EXEC_KEYS",
    "_EXEC_LAUNCH_KEYS",
    "_EXEC_RESULTS_KEYS",
    "_EXEC_RUNNING_KEYS",
    "_EXPLORER_KEYS",
    "_GATE_COLOR",
    "_HEALTH_COLOR",
    "_HEALTH_LABEL",
    "_HEALTH_SEVERITY",
    "_HELP_ERROR_GLOBAL",
    "_HELP_GLOBAL",
    "_HELP_MODAL_GLOBAL",
    "_HELP_RUNNING_GLOBAL",
    "_HELP_SCREEN",
    "_HELP_TITLE",
    "_HUNK_BG",
    "_INK",
    "_INSTANCE_KEYS",
    "_ISSUES_URL",
    "_KINDS",
    "_KIND_COLOR",
    "_KIND_GLYPH",
    "_LABEL",
    "_METHOD",
    "_MODAL_HELP_SCREENS",
    "_MODE",
    "_PREPARE_KEYS",
    "_REPORT_DIFF_KEYS",
    "_REPORT_LIST_KEYS",
    "_REPORT_RUN_KEYS",
    "_REPO_URL",
    "_RESOLVE_KEYS",
    "_RUNNING_DONE_KEYS",
    "_RUNNING_KEYS",
    "_RUN_GLYPH",
    "_SAME",
    "_SETTINGS_KEYS",
    "_SETTINGS_SUBTITLE",
    "_SKIP",
    "_STATUS",
    "_SYNTAX_BG",
    "_TAB_NAMES",
    "_TEXT",
    "_TEXT_HI",
    "_WARN",
    "_WELL_BORDER",
]

_INK = "#0d1017"

_TEXT = "#c5d0de"

_TEXT_HI = "#eaf0f8"

_DIM = "#5c6878"

_ACCENT = "#6d9eff"

_AXIS = "#a98bf0"

_SAME = "#48a97f"

_DRIFT = "#e0566b"

_SKIP = "#5c6878"

_WARN = "#d99b3f"

_DANGER = "#ff5f52"

_LABEL = "#7f8ba0"

_SYNTAX_BG = "#182030"

#: The git-diff well and its muted line-background bands (mockup .diff / .dl.*).
_DIFF_BG = "#090c11"  # a recessed well, darker than the panels

_DEL_BG = "#2b161c"  # muted red band behind deleted (baseline) lines

_ADD_BG = "#122a20"  # muted green band behind added (candidate) lines

_HUNK_BG = "#1d1836"  # muted purple band behind the hunk header

_WELL_BORDER = "#3f3960"  # the diff well's rounded outline (a muted axis-purple)

_METHOD: dict[str, str] = {
    "GET": _SAME,
    "POST": _ACCENT,
    "PUT": _WARN,
    "PATCH": _AXIS,
    "DELETE": _DRIFT,
    "HEAD": _DIM,
    "OPTIONS": _DIM,
}

_MODE: dict[str, str] = {
    "ignore": _SKIP,
    "exact": _SAME,
    "shape": _ACCENT,
    "type": _AXIS,
    "tolerance": _WARN,
}

_KINDS: tuple[tuple[str, type], ...] = (
    ("Environments", Environment),
    ("Requests", Request),
    ("Matrices", Matrix),
    ("Schemas", Schema),
    ("Instances", Instance),
    ("Diff Profiles", DiffProfile),
    ("Assertion Profiles", AssertionProfile),
    ("Execution Profiles", ExecutionProfile),
)

# Footer key hints, ordered nav → context actions → meta, ending in help + quit.
_EXPLORER_KEYS = (
    ("↑↓", "move"),
    ("space", "fold"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)

_ENV_KEYS = (
    ("↑↓", "move"),
    ("enter", "default"),
    ("h", "health"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)

_EXEC_KEYS = (
    ("↑↓", "move"),
    ("enter", "launch"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)

_RESOLVE_KEYS = (
    ("↑↓", "move"),
    ("r", "raw/resolved"),
    ("p", "curl"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)

#: Instances resolve like requests but have no HTTP call, so no 'p curl'.
_INSTANCE_KEYS = (
    ("↑↓", "move"),
    ("r", "raw/resolved"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)

_PREPARE_KEYS = (
    ("↑↓", "move"),
    ("space", "fold"),
    ("enter", "select"),
    ("/", "filter"),
    ("m", "matrix"),
    ("e", "env"),
    ("x", "run"),
    ("?", "help"),
    ("q", "quit"),
)

_RUNNING_KEYS = (
    ("↑↓", "move"),
    ("enter", "open"),
    ("/", "filter"),
    ("f", "failures"),
    ("r", "rules"),
    ("t", "views"),
    ("bksp", "back"),
    ("a", "abort"),
    ("?", "help"),
    ("q", "quit"),
)

_RUNNING_DONE_KEYS = (
    ("↑↓", "move"),
    ("enter", "open"),
    ("/", "filter"),
    ("f", "failures"),
    ("r", "rules"),
    ("o", "order"),
    ("t", "views"),
    ("x", "re-run"),
    ("bksp", "back"),
    ("s", "save"),
    ("?", "help"),
    ("q", "quit"),
)

_DIFF_PREPARE_KEYS = (
    ("↑↓", "move"),
    ("space", "fold"),
    ("enter", "select"),
    ("b/c", "baseline / candidate"),
    ("m", "matrix"),
    ("x", "run diff"),
    ("?", "help"),
    ("q", "quit"),
)

_DIFF_RUNNING_KEYS = (
    ("?", "help"),
    ("esc/⌫", "cancel diff"),
    ("q", "quit"),
)

_DIFF_REQ_KEYS = (
    ("↑↓", "cell"),
    ("enter", "rules ↗"),
    ("r", "view"),
    ("v", "layout"),
    ("n/p", "next ✗"),
    ("f", "fails"),
    ("i", "ignore"),
    ("s", "save"),
    ("?", "help"),
    ("q", "quit"),
)

_DIFF_RULES_KEYS = (
    ("↑↓", "rule"),
    ("enter", "cell ↗"),
    ("r", "view"),
    ("/", "filter"),
    ("f", "broken"),
    ("s", "save"),
    ("?", "help"),
    ("q", "quit"),
)

_DIFF_FIELDS_KEYS = (
    ("↑↓", "field"),
    ("enter", "cell ↗"),
    ("r", "view"),
    ("i", "ignore"),
    ("s", "save"),
    ("?", "help"),
    ("q", "quit"),
)

#: The nav tabs, in order — used to validate the saved default-tab preference.
_TAB_NAMES = ("explorer", "run", "diff", "execution", "report", "settings")

_SETTINGS_KEYS = (
    ("↑↓", "sections"),
    ("enter/t", "activate"),
    ("?", "help"),
    ("q", "quit"),
)

_ERROR_KEYS = (
    ("r", "re-check"),
    ("?", "help"),
    ("q", "quit"),
)

# Execution tab — each sub-view advertises its own footer keys (mockups A, steps 1-5).
_EXEC_LAUNCH_KEYS = (
    ("⏎", "launch"),
    ("space", "toggle"),
    ("t", "tags"),
    ("m", "mode"),
    ("?", "help"),
    ("esc/⌫", "close"),
    ("q", "quit"),
)

_EXEC_RUNNING_KEYS = (
    ("?", "help"),
    ("esc/⌫", "cancel run"),
    ("q", "quit"),
)

_EXEC_RESULTS_KEYS = (
    ("↑↓", "section"),
    ("⏎", "cell"),
    ("d", "diff"),
    ("e", "report"),
    ("s", "save"),
    ("r", "re-run"),
    ("?", "help"),
    ("esc/⌫", "close"),
    ("q", "quit"),
)

_EXEC_CELL_KEYS = (
    ("v", "unified/side"),
    ("i", "ignore field"),
    ("⏎", "open diff"),
    ("?", "help"),
    ("esc/⌫", "back"),
    ("q", "quit"),
)

_EXEC_DIFF_KEYS = (
    ("v", "unified/side-by-side"),
    ("↑↓", "scroll"),
    ("?", "help"),
    ("esc/⌫", "back"),
    ("q", "quit"),
)

# Report tab — the saved-report list, and the read-only in-tab replays.
_REPORT_LIST_KEYS = (
    ("⏎", "analyze"),
    ("/", "filter"),
    ("o", "export md"),
    ("r", "reload"),
    ("d", "delete"),
    ("?", "help"),
    ("esc/⌫", "close"),
    ("q", "quit"),
)

_REPORT_DIFF_KEYS = (
    ("v", "unified/side"),
    ("↑↓", "field"),
    ("o", "export"),
    ("?", "help"),
    ("esc/⌫", "back"),
    ("q", "quit"),
)

_REPORT_RUN_KEYS = (
    ("↑↓", "requests"),
    ("⏎", "drill"),
    ("z", "maximize"),
    ("o", "export"),
    ("?", "help"),
    ("esc/⌫", "back"),
    ("q", "quit"),
)

_RUN_GLYPH: dict[str, tuple[str, str]] = {
    "pending": ("○", _DIM),
    "running": ("◐", _WARN),
    "ok": ("✓", _SAME),
    "failed": ("✗", _DRIFT),
}

_STATUS: dict[str, tuple[str, str]] = {
    "pending": ("○", _DIM),
    "running": ("◐", _WARN),
    "success": ("✓", _SAME),
    "partial": ("◑", _WARN),
    "failed": ("✗", _DRIFT),
    "skipped": ("–", _DIM),
}

_KIND_COLOR: dict[type, str] = {
    Environment: _SAME,
    Request: _TEXT_HI,
    Matrix: _AXIS,
    Schema: _ACCENT,
    Instance: _SAME,
    DiffProfile: _WARN,
    AssertionProfile: _SAME,
    ExecutionProfile: _ACCENT,
}

_HEALTH_COLOR: dict[Health, str] = {
    Health.UNKNOWN: _DIM,
    Health.PASS: _SAME,
    Health.PARTIAL: _WARN,
    Health.FAIL: _DRIFT,
}

_HEALTH_LABEL: dict[Health, str] = {
    Health.UNKNOWN: "health unknown · press h",
    Health.PASS: "healthy",
    Health.PARTIAL: "partially healthy",
    Health.FAIL: "unreachable",
}

_HEALTH_SEVERITY: dict[Health, Literal["information", "warning", "error"]] = {
    Health.UNKNOWN: "information",
    Health.PASS: "information",
    Health.PARTIAL: "warning",
    Health.FAIL: "error",
}

_HELP_TITLE: dict[str, str] = {
    "explorer": "EXPLORER — understand how the project is configured",
    "diff": "DIFF — compare responses across environments",
    "error": "PROJECT WILL NOT LOAD",
    "run": "RUN",
    "report": "REPORT — browse saved runs, replayed with the live panels",
    "report-detail": "REPORT — deep dive into a saved run",
    "execution": "EXECUTION — launch, run, gate, drill, diff — all in one tab",
    "execution-diff": "EXECUTION — the run's scoped diff",
    "execution-running": "EXECUTION — running the plan",
    "settings": "SETTINGS",
}

_HELP_SCREEN: dict[str, tuple[tuple[str, str], ...]] = {
    "explorer": (
        ("↑ ↓", "move through the project tree"),
        ("space", "fold / unfold a section"),
        ("tab", "switch the active panel — tree, detail, provenance"),
        ("enter", "on an environment: set it default · on an execution profile: launch it"),
        ("h", "on an environment: run its health checks live"),
        ("r", "on a request or instance: toggle raw ⇄ resolved"),
        ("p", "on a request: show its curl (c inside copies real secrets)"),
        ("/", "filter the tree by name, kind, or tag"),
        ("g", "open the reference graph — what links to what"),
    ),
    "run": (
        ("↑ ↓", "move through rows / the detail tree"),
        ("space", "PREPARE — fold a request to show its cases"),
        ("enter", "PREPARE select · RUNNING drill into the next split"),
        ("e", "PREPARE — choose the environment this run executes against"),
        ("m", "PREPARE — choose matrix values (applies to every request)"),
        ("x", "run the selected cells against the current environment"),
        ("/", "filter by request or case name (shown on the panel)"),
        ("f", "RUNNING — filter the tables to failures only"),
        ("r", "RUNNING — pivot the left column: requests ⇄ the assertion-rules index"),
        ("o", "RUNNING — flip the requests table worst-first ⇄ plan order"),
        ("t", "RUNNING — cycle the detail facet: all → request → response → headers → raw"),
        ("n / p", "DETAIL — hop to the next / previous broken check pinned in the body"),
        ("y", "DETAIL — copy the raw exchange to the clipboard (secrets masked)"),
        ("esc / bksp", "RUNNING — collapse a split (or return to PREPARE)"),
        ("z", "RUNNING — maximize the detail panel"),
        ("a", "RUNNING — abort the run and return to PREPARE"),
        ("s", "RUNNING — save the finished run (masked) + archive it as a report"),
    ),
    "diff": (
        ("b / c", "pick the baseline / candidate environment in place (PREPARE and RESULTS)"),
        ("space", "PREPARE — fold a request to show its matrix cases"),
        ("enter", "PREPARE — toggle a request in / out · RESULTS — jump across pivots"),
        ("m", "PREPARE — choose matrix values (applies to every request)"),
        ("x", "diff the selected requests against the baseline ⇄ candidate pair"),
        ("↑ ↓", "move the index — the right panel inspects the selected row"),
        ("r", "RESULTS — cycle the index: requests → rules → fields"),
        ("/", "RESULTS — filter the active index"),
        ("f", "RESULTS — show only broken / failing rows"),
        ("n / p", "RESULTS — hop to the next / previous red row"),
        ("v", "RESULTS — flip the body diff unified ⇄ side-by-side"),
        ("o", "RESULTS — expand / collapse the outbound-request layer"),
        ("i", "RESULTS — silence the selected drift (writes the shown ignore rule)"),
        ("s", "RESULTS — save the diff report to the archive"),
        ("esc", "return from a cross-pivot jump · back to PREPARE"),
    ),
    "report": (
        ("↑ ↓", "move through the saved runs"),
        (
            "enter",
            "analyze the run in place — a diff replays the Diff panels, a run the Run panels",
        ),
        ("v", "in a saved diff: toggle the body well unified ⇄ side-by-side"),
        ("o", "export a Markdown summary of the run"),
        ("d", "delete the saved run (after confirmation)"),
        ("r", "reload the archive directory from disk"),
        ("/", "find by id, envs, or gate"),
        ("esc / bksp", "return from a replay to the saved-report list"),
    ),
    "report-detail": (
        ("↑ ↓", "scroll the run's full detail"),
        ("o", "export this run as a Markdown summary"),
        ("esc / bksp", "return to the saved-run list"),
        ("q", "quit comparo"),
    ),
    "execution": (
        ("enter", "on a profile: launch it in place · on a drifted cell: drill in"),
        ("space / t / m", "the plan's selection, tags, and mode (set by the profile's YAML)"),
        ("↑ ↓", "move through the profiles / the drifted cells"),
        ("d", "open the run's scoped body diff in place"),
        ("s", "archive this run's results as a saved report"),
        ("e", "name this run's archived report (browse it in the Report tab)"),
        ("v", "in a cell / diff: toggle the body diff unified ⇄ side-by-side"),
        ("i", "in a cell: silence the drifted field (after confirmation)"),
        ("r", "re-run this execution profile"),
        ("esc / bksp", "step back one sub-view (launch ← results ← cell ← diff)"),
    ),
    "execution-cell": (
        ("↑ ↓", "scroll the assertions / body diff"),
        ("v", "toggle the body diff unified ⇄ side-by-side"),
        ("i", "silence this drifted field (after confirmation)"),
        ("esc / bksp / q", "return to the execution"),
    ),
    "execution-diff": (
        ("↑ ↓", "scroll through the run's drifted cells"),
        ("v", "toggle every body diff unified ⇄ side-by-side"),
        ("esc / bksp / q", "return to the execution"),
    ),
    "execution-running": (
        ("(running)", "comparo is executing the plan; this view closes when the run finishes"),
    ),
    "settings": (
        ("↑ ↓", "move between settings sections"),
        ("enter", "toggle the section's control (Updates, Appearance, Behavior)"),
        ("t", "run the never-leak self-check (Security section)"),
    ),
    "error": (("r", "re-check the project after editing the files"),),
    "filter": (
        ("type", "filter the list as you type — it stays live behind the overlay"),
        ("enter", "keep this filter and close"),
        ("esc", "clear the filter and close"),
    ),
    "confirm": (
        ("y / enter", "confirm the action (comparo names the exact file first)"),
        ("n / esc", "cancel — nothing is written"),
    ),
    "graph": (
        ("↑ ↓", "scroll the reference graph"),
        ("g / esc", "close the overlay"),
        ("q", "quit comparo"),
    ),
    "picker": (
        ("↑ ↓", "move through the choices"),
        ("enter", "choose the highlighted item"),
    ),
    "curl": (
        ("↑ ↓", "scroll the curl"),
        ("c", "copy the real (secret-bearing) curl to the clipboard"),
        ("esc", "close — the shown curl is masked"),
        ("q", "quit comparo"),
    ),
    "matrix": (
        ("↑ ↓", "move through the matrix values"),
        ("space", "toggle a matrix value in / out of the run"),
        ("a / n", "select all / none"),
        ("esc", "apply the selection"),
    ),
}

_HELP_GLOBAL = (
    ("1 … 6", "switch screens — Explorer, Run, Diff, Execution, Report, Settings"),
    ("&é\"'(-", "same tabs on an AZERTY top row (no Shift needed)"),
    ("?", "show this help"),
    ("q", "quit comparo"),
)

_GATE_COLOR = {"PASS": _SAME, "FAIL": _DRIFT, "ERROR": _WARN}

#: Per-kind glyph + colour for the saved-report list (also used in the kind legend),
#: so each row shows its own kind (execution / diff / run) at a glance.
_KIND_GLYPH: dict[str, tuple[str, str]] = {
    "execution": ("◆", _AXIS),
    "diff": ("◇", _ACCENT),
    "run": ("◇", _SAME),
}

_ISSUES_URL = "https://github.com/wbenbihi/comparo/issues/new"

#: Overlays (pushed modals) shadow the app tab keys, so their help lists only
#: the modal-scoped global keys, never the tab-switch / quit block.
_MODAL_HELP_SCREENS = frozenset(
    {
        "execution",
        "execution-cell",
        "execution-diff",
        "report-detail",
        "filter",
        "confirm",
        "graph",
        "picker",
        "curl",
        "matrix",
    }
)

_HELP_MODAL_GLOBAL = (("?", "show / close this help"), ("esc", "close this overlay"))

#: The fatal-error screen has no tab bar and no ContentSwitcher, so the 1-5 /
#: AZERTY tab keys are inert there; its help lists only the keys that work.
_HELP_ERROR_GLOBAL = (("?", "show / close this help"), ("q", "quit comparo"))

#: The launch transition binds only '?'; it closes itself when the run finishes.
_HELP_RUNNING_GLOBAL = (("?", "show / close this help"),)

_ASSERT_GLYPH = {"pass": ("✓", _SAME), "warn": ("!", _WARN), "fail": ("✗", _DRIFT)}

_SETTINGS_SUBTITLE: dict[str, str] = {
    "about": "MIT · alpha",
    "project": "read-only",
    "security": "t · self-check",
    "appearance": "1 theme",
    "keybindings": "read-only",
    "updates": "opt-in",
    "plugins": "none",
    "engine": "comparo/v1",
    "behavior": "startup",
}

_REPO_URL = "github.com/wbenbihi/comparo"

_DOCS_URL = "github.com/wbenbihi/comparo/tree/main/docs"
