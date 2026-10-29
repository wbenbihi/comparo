"""Launch workspace fixtures — Scope, Plan, Run stage content and live builders."""

from __future__ import annotations

from dataclasses import dataclass

from comparo.tuibis.viewmodels import KeyHint
from comparo.tuibis.viewmodels import ListItemVM
from comparo.tuibis.viewmodels import PanelVM
from comparo.tuibis.viewmodels import StageViewVM
from comparo.tuibis.viewmodels import StyledCell
from comparo.tuibis.viewmodels import StyledLine


def _l(
    *cells: tuple[str, str] | tuple[str, str, int], cursor: bool = False, indent: int = 1
) -> StyledLine:
    """Build one StyledLine from (text, style[, width]) tuples."""
    packed = tuple(StyledCell(cell[0], cell[1], cell[2] if len(cell) > 2 else 0) for cell in cells)
    return StyledLine(packed, cursor=cursor, indent=indent)


_BLANK = _l(("", "fg"))


# ---------------------------------------------------------------- scope data


@dataclass(frozen=True)
class ScopeRequest:
    """One selectable request row in the scope list."""

    key: str
    name: str
    method: str
    cells: int
    pill: str
    meta: str


SCOPE_REQUESTS: tuple[ScopeRequest, ...] = (
    ScopeRequest("req:quote", "Price quote", "GET", 3, "⊞ 6", "plan x currency"),
    ScopeRequest("req:checkout", "Checkout", "POST", 3, "⊞ 6", "~w write-capable"),
    ScopeRequest("req:product", "Get product", "GET", 1, "1 cell", "id=123"),
    ScopeRequest("req:orders", "List orders", "GET", 3, "⊞ 3", "paged"),
    ScopeRequest("req:create", "Create order", "POST", 1, "1 cell", "~w write-capable"),
    ScopeRequest("req:fee", "Service fee", "GET", 1, "1 cell", "flat"),
)

INITIAL_SELECTED: frozenset[str] = frozenset(
    {"req:quote", "req:checkout", "req:product", "req:orders"}
)

PROFILES: dict[str, frozenset[str]] = {
    "profile:release-gate": frozenset({"req:quote", "req:checkout", "req:product", "req:orders"}),
    "profile:smoke-run": frozenset({"req:quote", "req:product"}),
}

SCOPE_CHIPS: tuple[str, ...] = ("all", "writes", "matrix")


def scope_items(selected: frozenset[str]) -> tuple[ListItemVM, ...]:
    """Build the interactive scope list: requests, scenarios, quick-start profiles."""
    rows: list[ListItemVM] = [
        ListItemVM(
            "hdr:requests",
            _l(
                ("REQUESTS", "label"),
                ("  6", "muted"),
                ("   space select · enter runs a profile", "dim"),
            ),
            header=True,
        )
    ]
    for req in SCOPE_REQUESTS:
        pill_style = "pill.matrix" if req.pill.startswith("⊞") else "muted"
        rows.append(
            ListItemVM(
                req.key,
                _l(
                    ("» ", "glyph"),
                    (req.name, "fg", 14),
                    (req.method, f"method.{req.method}", 5),
                    (f" {req.pill} ", pill_style),
                    (f"  {req.meta}", "dim"),
                ),
                selectable=True,
                selected=req.key in selected,
                search_text=f"{req.name} {req.method} {req.meta}",
            )
        )
    rows.extend(
        (
            ListItemVM("hdr:gap1", _BLANK, header=True),
            ListItemVM(
                "hdr:scenarios",
                _l(("SCENARIOS", "label"), ("  2", "muted"), ("   - not executable in v1", "dim")),
                header=True,
            ),
            ListItemVM(
                "scn:bulk",
                _l(("[-]", "dim"), (" ¶ ", "glyph"), ("Bulk quote", "dim"), ("    5 steps", "dim")),
                dimmed=True,
            ),
            ListItemVM(
                "scn:cancel",
                _l(("[-]", "dim"), (" ¶ ", "glyph"), ("Cancel flow", "dim"), ("   3 steps", "dim")),
                dimmed=True,
            ),
            ListItemVM("hdr:gap2", _BLANK, header=True),
            ListItemVM(
                "hdr:quickstart",
                _l(
                    ("QUICK-START", "label"), ("  enter prefills the scope — stays editable", "dim")
                ),
                header=True,
            ),
            ListItemVM(
                "profile:release-gate",
                _l(
                    ("▶ ", "glyph"),
                    ("release-gate", "fg", 14),
                    ("quote checkout product orders", "dim"),
                ),
                search_text="release-gate profile",
            ),
            ListItemVM(
                "profile:smoke-run",
                _l(("▶ ", "glyph"), ("smoke-run", "fg", 14), ("quote product", "dim")),
                search_text="smoke-run profile",
            ),
        )
    )
    return tuple(rows)


def topology_lines(candidate_on: bool) -> tuple[StyledLine, ...]:
    """Topology rows for the current candidate state — always three rows."""
    baseline = _l(
        ("baseline   ", "muted"),
        ("▌staging ▾", "baseline"),
        ("   https://staging.api.payments.local", "dim"),
    )
    if candidate_on:
        candidate = _l(
            ("candidate  ", "muted"),
            ("▌production ▾", "candidate"),
            ("   https://api.payments.local", "dim"),
            ("   c remove", "dim"),
        )
        note = _l(("directed diff — candidate judged against baseline", "dim"))
    else:
        candidate = _l(("candidate  ", "muted"), ("+ add candidate (c)", "dim"))
        note = _l(("no candidate → assertions-only · candidates → diff", "dim"))
    return (baseline, candidate, note)


def scope_math_lines(
    selected: frozenset[str],
    candidate_on: bool,
    prefilled_by: str = "",
) -> tuple[StyledLine, ...]:
    """Recompute the SCOPE MATH rows from the live selection — always six rows."""
    chosen = [req for req in SCOPE_REQUESTS if req.key in selected]
    count = len(chosen)
    cells = sum(req.cells for req in chosen)
    envs = 2 if candidate_on else 1
    candidates = 1 if candidate_on else 0
    calls = cells * envs
    comparisons = cells * candidates
    if candidate_on:
        run_line = _l(
            ("resulting run ", "muted"),
            (" ⇄ DIFF ", "chip.plain"),
            (" staging", "baseline"),
            ("→", "muted"),
            ("production", "candidate"),
            (" · no candidate = assertions-only", "dim"),
        )
    else:
        run_line = _l(
            ("resulting run ", "muted"),
            (" » RUN assertions-only ", "chip.warn"),
            (" staging", "baseline"),
            (" only", "muted"),
            (" · c adds production back", "dim"),
        )
    count_line = _l(
        (str(count), "bright"),
        (" requests", "muted"),
        (" x ", "dim"),
        (str(cells), "bright"),
        (" cells", "muted"),
        (" x ", "dim"),
        (str(envs), "bright"),
        (" envs", "muted"),
        (" → ", "dim"),
        (str(calls), "bright"),
        (" calls", "muted"),
        (f" · {comparisons} comparisons", "muted"),
    )
    checks_line = _l(
        ("checks ", "muted"),
        ("◈ 28 rules · Δ 12 rules", "fg"),
        ("   gate ", "muted"),
        ("release-gate", "fg"),
        ("   ad-hoc · 2 overrides", "muted"),
    )
    note_line = (
        _l(
            ("▶ prefilled by ", "muted"),
            (prefilled_by, "warn"),
            (" — quick-start · still editable", "dim"),
        )
        if prefilled_by
        else _BLANK
    )
    if count:
        ready_line = _l(
            ("+", "ok"),
            (" ready — ", "ok"),
            (" p ", "chip.active"),
            (" admit plan", "bright"),
            ("   nothing is sent until you run", "dim"),
        )
    else:
        ready_line = _l(("x nothing selected — space marks requests", "err"))
    cli = "$ comparo diff --baseline staging"
    if candidate_on:
        cli += " --candidate production"
    cli += f" -r {count} selected"
    return (run_line, count_line, checks_line, note_line, ready_line, _l((cli, "dim")))


def variants_panel() -> PanelVM:
    """The static VARIANTS panel of the scope stage."""
    return PanelVM(
        title="⊞ VARIANTS",
        hint="m edit axes",
        lines=(
            _l(
                ("⊞ ", "glyph"),
                ("Plans", "fg"),
                (
                    "   plan(2) × currency(3) → 6 cases · session keeps 4",  # noqa: RUF001
                    "muted",
                ),
            ),
            _l(("plan      ", "muted"), ("[x] basic  [x] pro", "fg")),
            _l(
                ("currency  ", "muted"),
                ("[x] USD  [x] CAD  [ ] EUR", "fg"),
                ("   ← narrowed this session", "warn"),
            ),
        ),
    )


def overrides_panel() -> PanelVM:
    """The static SESSION OVERRIDES panel of the scope stage."""
    return PanelVM(
        title="SESSION OVERRIDES",
        hint="o edit · u reset",
        lines=(
            _l(
                ("trials ", "muted"),
                ("1", "fg"),
                ("   attempts ", "muted"),
                ("2", "fg"),
                ("   fail-fast ", "muted"),
                ("off ▾", "fg"),
                ("   timeout ", "muted"),
                ("60s", "fg"),
            ),
            _l(
                ("rate ", "muted"),
                ("100/min", "fg"),
                (" ← session (profile 60)", "warn"),
                ("   values ", "muted"),
                ("params + staging-params", "fg"),
            ),
            _l(
                ("writes ", "muted"),
                ("[x] approve mutations", "mark.on"),
                ("  1 write-capable call in scope", "warn"),
            ),
        ),
    )


def scope_stage() -> StageViewVM:
    """S1 — hint shell only; the interactive scope view composes its own panels."""
    return StageViewVM(
        stage="scope",
        left=(),
        right=(),
        full=(),
        hints=(
            KeyHint("space", "select"),
            KeyHint("a/n", "all/none"),
            KeyHint("c", "candidate"),
            KeyHint("/", "filter"),
            KeyHint("p", "admit plan"),
        ),
    )


def plan_stage() -> StageViewVM:
    """P1 — the safety checkpoint as tables."""
    destinations = PanelVM(
        title="DESTINATIONS  ⇄  policy-admitted · redirects re-authorized",
        lines=(
            _l(
                (
                    "ROLE        ENVIRONMENT   HOST                                 "
                    "METHODS     CALLS   TLS   NOTES",
                    "label",
                )
            ),
            _l(
                ("▌", "baseline"),
                ("BASELINE   ", "baseline"),
                ("staging       ", "fg"),
                ("https://staging.api.payments.local   ", "fg"),
                ("GET POST    ", "muted"),
                ("12      ", "fg"),
                ("on    ", "ok"),
                ("reference — never judged", "muted"),
            ),
            _l(
                ("▌", "candidate"),
                ("CANDIDATE  ", "candidate"),
                ("production    ", "fg"),
                ("https://api.payments.local           ", "fg"),
                ("GET POST    ", "muted"),
                ("12      ", "fg"),
                ("on    ", "ok"),
                ("judged against baseline", "muted"),
            ),
        ),
    )
    scope = PanelVM(
        title="⊞ SCOPE & EXPANSION",
        hint="enter full cell list",
        lines=(
            _l(("REQUEST         CELLS  CALLS  WRITES", "label")),
            _l(
                (" » ", "glyph"),
                ("Price quote   ", "fg"),
                ("3      ", "muted"),
                ("6      ", "fg"),
                ("no", "muted"),
            ),
            _l(
                (" » ", "glyph"),
                ("Checkout      ", "fg"),
                ("3      ", "muted"),
                ("6      ", "fg"),
                ("~ yes", "warn"),
            ),
            _l(
                (" » ", "glyph"),
                ("Get product   ", "fg"),
                ("1      ", "muted"),
                ("2      ", "fg"),
                ("no", "muted"),
            ),
            _l(
                (" » ", "glyph"),
                ("List orders   ", "fg"),
                ("3      ", "muted"),
                ("6      ", "fg"),
                ("no", "muted"),
            ),
            _l(
                (
                    "matrix plan(2)×currency(2) → 4 cells · skipped 0",  # noqa: RUF001
                    "muted",
                )
            ),
        ),
    )
    validation = PanelVM(
        title="VALIDATION",
        lines=(
            _l(("CHECK        PROFILE         RULES                      SOURCE", "label")),
            _l(
                (" ◈ ", "glyph"),
                ("asserts   ", "fg"),
                ("release-gate    ", "fg"),
                ("28 · 3 blocking · 7 warn   ", "muted"),
                ("profile", "dim"),
            ),
            _l(
                (" Δ ", "glyph"),
                ("diffs     ", "fg"),
                ("release-gate    ", "fg"),
                ("12 · $status always on     ", "muted"),
                ("profile", "dim"),
            ),
            _l(
                (" ‡ ", "glyph"),
                ("gate      ", "fg"),
                ("complete run    ", "fg"),
                ("0 blocking violations      ", "muted"),
                ("project", "dim"),
            ),
            _l(("a call passes when baseline asserts AND candidate asserts AND diffs hold", "dim")),
        ),
    )
    limits = PanelVM(
        title="LIMITS",
        lines=(
            _l(("timeout     ", "muted"), ("60s/call", "fg")),
            _l(("concurrency ", "muted"), ("6", "fg"), ("    retries ", "muted"), ("2", "fg")),
            _l(("rate        ", "muted"), ("100 req/min/host", "fg"), (" ← session", "warn")),
            _l(("capture     ", "muted"), ("50 MB/call · full response", "fg")),
        ),
    )
    safety = PanelVM(
        title="SAFETY REVIEW",
        lines=(
            _l(
                ("~ ADVISORY", "status.advisory"),
                ("  1 write-capable call: ", "fg"),
                ("Checkout POST", "bright"),
                ("  ·  approved this session  ", "fg"),
                ("m", "fg"),
                (" revoke", "muted"),
            ),
            _l(
                ("secrets      ", "muted"),
                ("BEARER_TOKEN → api.payments.local:443 only", "fg"),
                ("  ·  displays masked", "dim"),
            ),
            _l(
                ("destinations ", "muted"),
                ("2 hosts permitted · DNS re-checked · same-origin redirects", "fg"),
            ),
            _l(
                ("artifacts    ", "muted"),
                ("persist on gate failure · sanitized · secrets never stored", "fg"),
            ),
        ),
    )
    overrides = PanelVM(
        title="OVERRIDES · PROVENANCE — project → profile → session",
        lines=(
            _l(
                ("SETTING     PROJECT     PROFILE       SESSION            KEPT IN REPORT", "label")
            ),
            _l(
                ("rate      ", "fg"),
                ("30/min      ", "dim"),
                ("60/min        ", "dim"),
                ("100/min", "warn"),
                ("            yes — provenance recorded", "muted"),
            ),
            _l(
                ("matrix    ", "fg"),
                ("all cases   ", "dim"),
                ("all cases     ", "dim"),
                ("currency -EUR", "warn"),
                ("      yes", "muted"),
            ),
        ),
    )
    return StageViewVM(
        stage="plan",
        left=(scope, limits),
        right=(validation, safety),
        full=(destinations, overrides),
        action_line=(
            StyledCell(" x ", "chip.active"),
            StyledCell(" run 12 calls", "bright"),
            StyledCell("    + plan is valid — execution allowed", "ok"),
            StyledCell("      y copy plan · esc edit scope", "muted"),
        ),
        hints=(
            KeyHint("x", "run"),
            KeyHint("m", "writes"),
            KeyHint("y", "copy plan"),
            KeyHint("esc", "edit scope"),
        ),
    )


# ------------------------------------------------------------------ run data

BOARD_TITLE = "CALLS — all · > active · x fails · SENT flags outbound drift (≠)"

_BOARD_HEADER = ListItemVM(
    "hdr:board",
    _l(
        ("   CALL           CELL       SENT  ", "label"),
        ("BASELINE       ", "baseline"),
        ("CANDIDATE        ", "candidate"),
        ("ASSERT     DIFF        TIME", "label"),
    ),
    header=True,
)

_CALL_ROWS: dict[str, ListItemVM] = {
    "call:1": ListItemVM(
        "call:1",
        _l(
            ("+", "ok"),
            (" Price quote    basic·USD  ", "fg"),
            ("=     ", "ok"),
            ("200 118ms      ", "fg"),
            ("200 121ms        ", "fg"),
            ("3/3 +      ", "ok"),
            ("same       ", "ok"),
            (" 0.9s", "muted"),
        ),
        search_text="Price quote basic USD passed",
    ),
    "call:2": ListItemVM(
        "call:2",
        _l(
            (">", "status.running"),
            (" Price quote    pro·USD    ", "bright"),
            ("=     ", "ok"),
            ("200 126ms      ", "fg"),
            ("streaming ·8ev   ", "status.running"),
            ("2/3 >      ", "status.running"),
            ("pending    ", "dim"),
            (" 7.3s", "muted"),
        ),
        search_text="Price quote pro USD streaming running",
    ),
    "call:3": ListItemVM(
        "call:3",
        _l(
            ("x", "err"),
            (" Checkout       basic·USD  ", "fg"),
            ("≠ !   ", "status.outbound"),
            ("200 118ms      ", "fg"),
            ("500 2.1s         ", "err"),
            ("1/3 x      ", "err"),
            ("x drift    ", "err"),
            (" 2.4s", "muted"),
        ),
        search_text="Checkout basic USD failed drift",
    ),
    "call:4": ListItemVM(
        "call:4",
        _l(
            (">", "status.running"),
            (" Checkout       pro·USD    ", "fg"),
            ("=     ", "ok"),
            ("200 122ms      ", "fg"),
            ("awaiting hdrs    ", "status.running"),
            ("0/3        ", "dim"),
            ("pending    ", "dim"),
            (" 1.1s", "muted"),
        ),
        search_text="Checkout pro USD running",
    ),
    "call:5": ListItemVM(
        "call:5",
        _l(
            ("+", "ok"),
            (" Get product    id=123     ", "fg"),
            ("=     ", "ok"),
            ("200 95ms       ", "fg"),
            ("200 97ms         ", "fg"),
            ("3/3 +      ", "ok"),
            ("same       ", "ok"),
            (" 0.8s", "muted"),
        ),
        search_text="Get product id=123 passed",
    ),
    "call:6": ListItemVM(
        "call:6",
        _l(
            ("+", "ok"),
            (" List orders    basic·USD  ", "fg"),
            ("=     ", "ok"),
            ("200 142ms      ", "fg"),
            ("200 149ms        ", "fg"),
            ("3/3 +      ", "ok"),
            ("~ tol      ", "warn"),
            (" 1.2s", "muted"),
        ),
        search_text="List orders basic USD passed tolerated",
    ),
    "call:7": ListItemVM(
        "call:7",
        _l(
            (".", "dim"),
            (" List orders    pro·USD    ", "dim"),
            ("      ", "dim"),
            ("—              ", "dim"),
            ("—                ", "dim"),
            ("0/3        ", "dim"),
            ("—          ", "dim"),
            ("    —", "dim"),
        ),
        search_text="List orders pro USD queued",
    ),
    "call:8": ListItemVM(
        "call:8",
        _l(
            (".", "dim"),
            (" Price quote    pro·CAD    ", "dim"),
            ("      ", "dim"),
            ("—              ", "dim"),
            ("—                ", "dim"),
            ("0/3        ", "dim"),
            ("—          ", "dim"),
            ("    —", "dim"),
        ),
        search_text="Price quote pro CAD queued",
    ),
}


def run_call_items() -> tuple[ListItemVM, ...]:
    """The full live board: header plus all eight calls."""
    return (_BOARD_HEADER, *_CALL_ROWS.values())


def run_fail_items() -> tuple[ListItemVM, ...]:
    """The fails-only board: header plus the failed calls."""
    return (_BOARD_HEADER, _CALL_ROWS["call:3"])


def run_cancelling_items() -> tuple[ListItemVM, ...]:
    """The cancelling board: finished calls kept, active drained, queued cancelled."""
    drained = {
        "call:2": ListItemVM(
            "call:2",
            _l(
                ("~", "warn"),
                (" Price quote    pro·USD    ", "fg"),
                ("=     ", "ok"),
                ("200 126ms      ", "fg"),
                ("draining ·9ev    ", "warn"),
                ("2/3 >      ", "warn"),
                ("cancelled  ", "dim"),
                (" 8.1s", "muted"),
            ),
            search_text="Price quote pro USD draining",
        ),
        "call:4": ListItemVM(
            "call:4",
            _l(
                ("~", "warn"),
                (" Checkout       pro·USD    ", "fg"),
                ("=     ", "ok"),
                ("200 122ms      ", "fg"),
                ("draining         ", "warn"),
                ("0/3        ", "dim"),
                ("cancelled  ", "dim"),
                (" 1.9s", "muted"),
            ),
            search_text="Checkout pro USD draining",
        ),
        "call:7": ListItemVM(
            "call:7",
            _l(
                ("·", "dim"),
                (" List orders    pro·USD    ", "dim"),
                ("      ", "dim"),
                ("cancelled before start", "dim"),
            ),
            search_text="List orders pro USD cancelled",
        ),
        "call:8": ListItemVM(
            "call:8",
            _l(
                ("·", "dim"),
                (" Price quote    pro·CAD    ", "dim"),
                ("      ", "dim"),
                ("cancelled before start", "dim"),
            ),
            search_text="Price quote pro CAD cancelled",
        ),
    }
    return (_BOARD_HEADER, *(drained.get(key, row) for key, row in _CALL_ROWS.items()))


def run_header_lines(cancelling: bool) -> tuple[StyledLine, ...]:
    """The run header strip — running or amber cancelling variant, two rows each."""
    if cancelling:
        return (
            _l(
                ("~ CANCELLING", "status.advisory"),
                ("  release-gate scope", "bright"),
                ("   ", "fg"),
                ("staging", "baseline"),
                (" ⇄ ", "muted"),
                ("production", "candidate"),
                ("   plan 9f4b2e7c", "muted"),
                ("   a resumes the run", "dim"),
            ),
            _l(
                ("▓" * 50 + "░" * 36, "warn"),
                ("  ", "fg"),
                ("7 / 12 calls", "bright"),
                (" · 2 draining · 3 cancelled", "warn"),
                (" · artifacts kept", "dim"),
            ),
        )
    return (
        _l(
            ("> RUNNING", "status.running"),
            ("  release-gate scope", "bright"),
            ("   ", "fg"),
            ("staging", "baseline"),
            (" ⇄ ", "muted"),
            ("production", "candidate"),
            ("   plan 9f4b2e7c", "muted"),
            ("   survives workspace switches", "dim"),
        ),
        _l(
            ("▓" * 50 + "░" * 36, "baseline"),
            ("  ", "fg"),
            ("7 / 12 calls", "bright"),
            (" · 2 active · 3 queued", "muted"),
            (" · elapsed 18s · est 14s left", "dim"),
        ),
    )


_CALL_DETAILS: dict[str, tuple[StyledLine, ...]] = {
    "call:1": (
        _l(
            ("» ", "glyph"),
            ("Price quote", "bright"),
            (" · basic·USD", "muted"),
            ("   attempt 1 of 2", "muted"),
        ),
        _l(
            ("▌baseline ", "baseline"),
            (" complete", "fg"),
            ("  200 in 118ms · 1.1 KB json", "muted"),
        ),
        _l(
            ("▌candidate", "candidate"),
            (" complete", "fg"),
            ("  200 in 121ms · 1.1 KB json", "muted"),
        ),
        _l(("sent", "muted"), ("       = identical on both sides", "ok")),
        _l(("assertions", "muted"), (" 3/3 held", "ok")),
        _l(("diff", "muted"), ("       same — 0 fields drifted", "ok")),
    ),
    "call:2": (
        _l(
            ("» ", "glyph"),
            ("Price quote", "bright"),
            (" · pro·USD", "muted"),
            ("   attempt 1 of 2", "muted"),
        ),
        _l(
            ("▌baseline ", "baseline"),
            (" complete", "fg"),
            ("  200 in 126ms · 1.2 KB json", "muted"),
        ),
        _l(
            ("▌candidate", "candidate"),
            (" streaming", "status.running"),
            ("  8 SSE events · 2.3s open · idle 0.2s", "muted"),
        ),
        _l(("sent", "muted"), ("       = identical on both sides", "ok")),
        _l(
            ("assertions", "muted"), (" 2/3 held", "fg"), (" · latency ≤150ms pending close", "dim")
        ),
        _l(("diff", "muted"), ("       starts when both sides close", "dim")),
    ),
    "call:3": (
        _l(
            ("» ", "glyph"),
            ("Checkout", "bright"),
            (" · basic·USD", "muted"),
            ("   attempt 2 of 2", "muted"),
        ),
        _l(
            ("▌baseline ", "baseline"),
            (" complete", "fg"),
            ("  200 in 118ms · 2.0 KB json", "muted"),
        ),
        _l(
            ("▌candidate", "candidate"),
            (" failed", "err"),
            ("  500 in 2.1s · text/html error page", "err"),
        ),
        _l(
            ("sent", "muted"),
            ("       ≠ outbound drift — retry-token header added", "status.outbound"),
        ),
        _l(("assertions", "muted"), (" 1/3 held", "err"), (" · status 2xx violated", "err")),
        _l(("diff", "muted"), ("       x drift — $status 200 vs 500", "err")),
    ),
    "call:4": (
        _l(
            ("» ", "glyph"),
            ("Checkout", "bright"),
            (" · pro·USD", "muted"),
            ("   attempt 1 of 2", "muted"),
        ),
        _l(
            ("▌baseline ", "baseline"),
            (" complete", "fg"),
            ("  200 in 122ms · 2.0 KB json", "muted"),
        ),
        _l(
            ("▌candidate", "candidate"),
            (" awaiting headers", "status.running"),
            ("  1.1s open", "muted"),
        ),
        _l(("sent", "muted"), ("       = identical on both sides", "ok")),
        _l(("assertions", "muted"), (" 0/3 evaluated", "dim")),
        _l(("diff", "muted"), ("       pending", "dim")),
    ),
    "call:5": (
        _l(
            ("» ", "glyph"),
            ("Get product", "bright"),
            (" · id=123", "muted"),
            ("   attempt 1 of 2", "muted"),
        ),
        _l(
            ("▌baseline ", "baseline"),
            (" complete", "fg"),
            ("  200 in 95ms · 0.8 KB json", "muted"),
        ),
        _l(
            ("▌candidate", "candidate"),
            (" complete", "fg"),
            ("  200 in 97ms · 0.8 KB json", "muted"),
        ),
        _l(("sent", "muted"), ("       = identical on both sides", "ok")),
        _l(("assertions", "muted"), (" 3/3 held", "ok")),
        _l(("diff", "muted"), ("       same — 0 fields drifted", "ok")),
    ),
    "call:6": (
        _l(
            ("» ", "glyph"),
            ("List orders", "bright"),
            (" · basic·USD", "muted"),
            ("   attempt 1 of 2", "muted"),
        ),
        _l(
            ("▌baseline ", "baseline"),
            (" complete", "fg"),
            ("  200 in 142ms · 3.4 KB json", "muted"),
        ),
        _l(
            ("▌candidate", "candidate"),
            (" complete", "fg"),
            ("  200 in 149ms · 3.4 KB json", "muted"),
        ),
        _l(("sent", "muted"), ("       = identical on both sides", "ok")),
        _l(("assertions", "muted"), (" 3/3 held", "ok")),
        _l(("diff", "muted"), ("       ~ tolerated — latency within 10%", "warn")),
    ),
    "call:7": (
        _l(
            ("» ", "glyph"),
            ("List orders", "bright"),
            (" · pro·USD", "muted"),
            ("   queued", "muted"),
        ),
        _l(("▌baseline ", "baseline"), (" queued", "dim")),
        _l(("▌candidate", "candidate"), (" queued", "dim")),
        _l(("sent", "muted"), ("       — nothing sent yet", "dim")),
        _l(("assertions", "muted"), (" —", "dim")),
        _l(("diff", "muted"), ("       —", "dim")),
    ),
    "call:8": (
        _l(
            ("» ", "glyph"),
            ("Price quote", "bright"),
            (" · pro·CAD", "muted"),
            ("   queued", "muted"),
        ),
        _l(("▌baseline ", "baseline"), (" queued", "dim")),
        _l(("▌candidate", "candidate"), (" queued", "dim")),
        _l(("sent", "muted"), ("       — nothing sent yet", "dim")),
        _l(("assertions", "muted"), (" —", "dim")),
        _l(("diff", "muted"), ("       —", "dim")),
    ),
}


def call_detail_lines(key: str) -> tuple[StyledLine, ...]:
    """SELECTED CALL rows for one board key — six rows, queued fallback."""
    return _CALL_DETAILS.get(key, _CALL_DETAILS["call:7"])


def events_panel() -> PanelVM:
    """The live EVENTS side panel of the run stage."""
    return PanelVM(
        title="EVENTS — live · value-free",
        lines=(
            _l(("18:12:04.1 ", "dim"), ("candidate ", "candidate"), ("headers 200", "muted")),
            _l(("18:12:04.3 ", "dim"), ("candidate ", "candidate"), ("sse event 8", "muted")),
            _l(("18:12:04.4 ", "dim"), ("baseline ", "baseline"), ("asserts held 2/3", "muted")),
            _l(("18:12:04.6 ", "dim"), ("checkout ", "fg"), ("x failed — 500 vs 2xx", "err")),
            _l(
                ("18:12:04.8 ", "dim"),
                ("scheduler ", "fg"),
                ("List orders pro·USD started", "muted"),
            ),
        ),
    )


def run_stage() -> StageViewVM:
    """R1 — hint shell only; the interactive run view composes its own panels."""
    return StageViewVM(
        stage="run",
        left=(),
        right=(),
        full=(),
        hints=(
            KeyHint("↑↓", "select call"),
            KeyHint("f", "fails only"),
            KeyHint("a", "cancel"),
            KeyHint("]", "analyse"),
        ),
    )


STAGES: dict[str, StageViewVM] = {
    "scope": scope_stage(),
    "plan": plan_stage(),
    "run": run_stage(),
}
