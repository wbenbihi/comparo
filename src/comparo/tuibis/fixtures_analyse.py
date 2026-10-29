"""Analyse fixtures — run strip, five interactive indexes, deep dives.

Every index carries real cursor rows plus per-selection detail and inspect
variants so the visual prototype behaves like the live app: moving the list
cursor swaps the right-hand panels, and facet chips swap the inspect body.
"""

from __future__ import annotations

from comparo.tuibis.fixtures_launch import _l
from comparo.tuibis.viewmodels import AnalyseVM
from comparo.tuibis.viewmodels import IndexItemVM
from comparo.tuibis.viewmodels import ListItemVM
from comparo.tuibis.viewmodels import PanelVM
from comparo.tuibis.viewmodels import RunStripVM
from comparo.tuibis.viewmodels import StyledLine
from comparo.tuibis.widgets.analyse_view import AnalyseIndexData

STRIP = RunStripVM(
    run_id="74ecaa",
    profile="release-gate",
    baseline="staging",
    candidate="production",
    verdict="failed",
    tallies="7+ 1~ 3x 1! of 12 calls",
    right="31s · saved + verified",
)

INDEX_ITEMS = (
    IndexItemVM("requests", "1 REQUESTS 12"),
    IndexItemVM("issues", "2 ISSUES 3"),
    IndexItemVM("rules", "3 RULES 17"),
    IndexItemVM("fields", "4 FIELDS 38"),
    IndexItemVM("timeline", "5 TIMELINE 21"),
)

_GAP = StyledLine(())

# ── Inspect bodies — RESPONSE evidence trees ────────────────────────────────

_EVIDENCE_TREE: tuple[StyledLine, ...] = (
    _l(
        ("x ", "status.failed"),
        ("│ ", "dim"),
        ("HTTP/1.1 ", "fg"),
        ("500 Internal Server Error", "err"),
        ("   118ms · 412 B", "muted"),
        ("      x status-2xx expected 200-299", "err"),
    ),
    _l(
        ("  ", "fg"),
        ("│ ", "dim"),
        ("content-type: application/json", "muted"),
        ("   retry-after: 30 ", "muted"),
        ("+ added", "err"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("{", "fg")),
    _l(
        ("x ", "status.failed"),
        ("│ ", "dim"),
        ('  "success": ', "muted"),
        ("false", "err"),
        (",", "muted"),
        ("       ← baseline: true", "err"),
    ),
    _l(
        ("+ ", "err"),
        ("│ ", "dim"),
        ('  "error": ', "muted"),
        ('"Internal Server Error"', "fg"),
        (",", "muted"),
        ("   + added vs baseline", "err"),
    ),
    _l(
        ("+ ", "err"),
        ("│ ", "dim"),
        ('  "code": ', "muted"),
        ('"INTERNAL_ERROR"', "fg"),
        (",", "muted"),
        ("          + added", "err"),
    ),
    _l(
        ("- ", "err"),
        ("│ ", "dim"),
        ('  "order_id"', "dim"),
        ("            - removed · present in baseline only", "err"),
    ),
    _l(
        ("  ", "fg"),
        ("│ ", "dim"),
        (" ▸", "muted"),
        (' "metadata": {…} 3 fields', "muted"),
        ("  folded — space expands", "dim"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("}", "fg")),
)

_EVIDENCE_ORDERS: tuple[StyledLine, ...] = (
    _l(
        ("x ", "status.failed"),
        ("│ ", "dim"),
        ("HTTP/1.1 ", "fg"),
        ("500 Internal Server Error", "err"),
        ("   142ms · 233 B", "muted"),
        ("      x status-2xx expected 200-299", "err"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("content-type: application/json", "muted")),
    _l(("  ", "fg"), ("│ ", "dim"), ("{", "fg")),
    _l(
        ("x ", "status.failed"),
        ("│ ", "dim"),
        ('  "success": ', "muted"),
        ("false", "err"),
        (",", "muted"),
        ("       ← baseline: true", "err"),
    ),
    _l(
        ("+ ", "err"),
        ("│ ", "dim"),
        ('  "error": ', "muted"),
        ('"Upstream timeout"', "fg"),
        (",", "muted"),
        ("      + added vs baseline", "err"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("}", "fg")),
    _l(("= SENT 1:1 — both cells identical outbound · fair comparison", "dim")),
)

_EVIDENCE_QUOTE: tuple[StyledLine, ...] = (
    _l(
        ("~ ", "status.advisory"),
        ("│ ", "dim"),
        ("HTTP/1.1 ", "fg"),
        ("200 OK", "ok"),
        ("   91ms · 1.8 KB", "muted"),
        ("      + status-2xx held", "ok"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("{", "fg")),
    _l(
        ("  ", "fg"),
        ("│ ", "dim"),
        ('  "quote": { "price": "1,234.56" },', "muted"),
        ("   = same", "ok"),
    ),
    _l(
        ("~ ", "warn"),
        ("│ ", "dim"),
        ('  "usage": { "tokens": ', "muted"),
        ("251", "warn"),
        (" },", "muted"),
        ("   ~ baseline 258 · drift 2.7% inside ±5%", "warn"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("}", "fg")),
    _l(("~ advisory only — never gates the run", "dim")),
)

_EVIDENCE_PRODUCT: tuple[StyledLine, ...] = (
    _l(
        ("+ ", "ok"),
        ("│ ", "dim"),
        ("HTTP/1.1 ", "fg"),
        ("200 OK", "ok"),
        ("   44ms · 2.1 KB", "muted"),
        ("      + all assertions held", "ok"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("{", "fg")),
    _l(("  ", "fg"), ("│ ", "dim"), ('  "id": "prod_49aa",', "muted"), ("   = same", "ok")),
    _l(("  ", "fg"), ("│ ", "dim"), ('  "name": "Pro plan",', "muted"), ("   = same", "ok")),
    _l(
        ("  ", "fg"),
        ("│ ", "dim"),
        (" ▸", "muted"),
        (' "metadata": {…} 5 fields', "muted"),
        ("  = same · folded", "dim"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("}", "fg")),
)

_EVIDENCE_PRICE: tuple[StyledLine, ...] = (
    _l(
        ("x ", "status.failed"),
        ("│ ", "dim"),
        ("HTTP/1.1 ", "fg"),
        ("200 OK", "ok"),
        ("   91ms · 1.8 KB", "muted"),
        ("      x price-decimal #,##0.00", "err"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("{", "fg")),
    _l(("  ", "fg"), ("│ ", "dim"), ('  "quote": {', "muted")),
    _l(
        ("x ", "status.failed"),
        ("│ ", "dim"),
        ('    "price": ', "muted"),
        ('"1234.56"', "err"),
        (",", "muted"),
        ('   ← baseline: "1,234.56" · separator dropped', "err"),
    ),
    _l(
        ("  ", "fg"),
        ("│ ", "dim"),
        ('    "currency": "USD",', "muted"),
        ("   = same", "ok"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("  }", "fg")),
    _l(("  ", "fg"), ("│ ", "dim"), ("}", "fg")),
)

_EVIDENCE_LATENCY: tuple[StyledLine, ...] = (
    _l(
        ("~ ", "status.advisory"),
        ("│ ", "dim"),
        ("HTTP/1.1 ", "fg"),
        ("200 OK", "ok"),
        ("   212ms · 1.2 KB", "muted"),
        ("      ~ latency-slo budget 150ms", "warn"),
    ),
    _l(
        ("  ", "fg"),
        ("│ ", "dim"),
        ("timing  dns 2ms · connect 11ms · ttfb 198ms · total 212ms", "muted"),
    ),
    _l(
        ("~ ", "warn"),
        ("│ ", "dim"),
        ("ttfb ", "muted"),
        ("198ms", "warn"),
        ("   ← baseline 104ms · +90% slower", "warn"),
    ),
    _l(("  ", "fg"), ("│ ", "dim"), ("body identical — timing is the only drift", "dim")),
)

# ── Inspect bodies — EVENTS tables ──────────────────────────────────────────

_SSE_TABLE: tuple[StyledLine, ...] = (
    _l(
        (" #   OFFSET    EVENT         ", "label"),
        ("BASELINE                  ", "baseline"),
        ("CANDIDATE                 ", "candidate"),
        ("PAIRING", "label"),
    ),
    _l(
        (" 1   +0.102s   ", "muted"),
        ("message.start ", "fg"),
        ("role assistant            ", "fg"),
        ("role assistant            ", "fg"),
        ("= paired", "ok"),
    ),
    _l(
        (" 2   +0.184s   ", "muted"),
        ("content.delta ", "fg"),
        ('"Thanks for your…"        ', "fg"),
        ('"Thanks for your…"        ', "fg"),
        ("= paired", "ok"),
    ),
    _l(
        (" 4   +1.874s   ", "muted"),
        ("usage         ", "fg"),
        ("412 prompt · 256 out      ", "fg"),
        ("401 prompt · 251 out      ", "fg"),
        ("~ tolerance ±5%", "warn"),
    ),
    _l(
        (" 5   +2.081s   ", "muted"),
        ("message.stop  ", "fg"),
        ("finish stop               ", "fg"),
        ("finish stop               ", "fg"),
        ("= paired", "ok"),
    ),
)

_SSE_CONNECT: tuple[StyledLine, ...] = (
    _l(
        (" #   OFFSET    EVENT         ", "label"),
        ("BASELINE                  ", "baseline"),
        ("CANDIDATE                 ", "candidate"),
        ("PAIRING", "label"),
    ),
    _l(
        (" 1   +0.000s   ", "muted"),
        ("connect       ", "fg"),
        ("tcp 443 · 12ms            ", "fg"),
        ("econnrefused              ", "err"),
        ("! unpaired", "error-op"),
    ),
    _l(
        (" 2   +1.012s   ", "muted"),
        ("retry         ", "fg"),
        ("—                         ", "dim"),
        ("attempt 2 scheduled       ", "fg"),
        ("! candidate only", "error-op"),
    ),
    _l(
        (" 3   +1.108s   ", "muted"),
        ("connect       ", "fg"),
        ("—                         ", "dim"),
        ("tcp 443 · 9ms             ", "fg"),
        ("= recovered", "ok"),
    ),
    _l(
        (" 4   +1.204s   ", "muted"),
        ("message.stop  ", "fg"),
        ("finish stop               ", "fg"),
        ("finish stop               ", "fg"),
        ("= paired", "ok"),
    ),
)

# ── Inspect bodies — shared facet variants ──────────────────────────────────

_FACET_REQUEST: tuple[StyledLine, ...] = (
    _l(("POST /v1/checkout HTTP/1.1", "bright"), ("      ← what was actually sent", "dim")),
    _l(("host: api.acme.dev", "muted")),
    _l(
        ("authorization: Bearer ", "muted"),
        ("********", "status.redacted"),
        ("   ← redacted", "dim"),
    ),
    _l(("content-type: application/json", "muted")),
    _l(
        ("x-api-version: ", "muted"),
        ("2024-05-01", "baseline"),
        (" · ", "dim"),
        ("2024-06-01", "candidate"),
        ("   ≠ differs per lane", "warn"),
    ),
    _l(("{", "fg")),
    _l(('  "plan": "pro", "currency": "USD"', "fg"), ("   ← cell values injected", "dim")),
    _l(("}", "fg")),
)

_FACET_REQUEST_QUOTE: tuple[StyledLine, ...] = (
    _l(("GET /v1/quote?plan=pro&currency=USD HTTP/1.1", "bright")),
    _l(("host: api.acme.dev", "muted")),
    _l(("accept: application/json", "muted")),
    _l(("x-api-version: ", "muted"), ("same on both lanes", "fg"), ("   = 1:1", "ok")),
    _l(("no body — GET", "dim")),
)

_FACET_HEADERS: tuple[StyledLine, ...] = (
    _l(
        ("HEADER           ", "label"),
        ("BASELINE               ", "baseline"),
        ("CANDIDATE              ", "candidate"),
        ("VERDICT", "label"),
    ),
    _l(
        (" content-type    ", "muted"),
        ("application/json       ", "fg"),
        ("application/json       ", "fg"),
        ("= same", "ok"),
    ),
    _l(
        (" content-length  ", "muted"),
        ("1204                   ", "fg"),
        ("412                    ", "fg"),
        ("Δ changed", "err"),
    ),
    _l(
        (" retry-after     ", "muted"),
        ("—                      ", "dim"),
        ("30                     ", "fg"),
        ("+ added", "err"),
    ),
    _l(
        (" x-request-id    ", "muted"),
        ("(ignored by rule)      ", "dim"),
        ("(ignored by rule)      ", "dim"),
        ("· ignored", "dim"),
    ),
    _l(
        (" date            ", "muted"),
        ("(ignored by rule)      ", "dim"),
        ("(ignored by rule)      ", "dim"),
        ("· ignored", "dim"),
    ),
)

_FACET_RAW: tuple[StyledLine, ...] = (
    _l(("{", "dim"), ("                                 raw candidate body · 412 B", "dim")),
    _l(('  "success": false,', "dim")),
    _l(('  "error": "Internal Server Error",', "dim")),
    _l(('  "code": "INTERNAL_ERROR",', "dim")),
    _l(('  "metadata": {', "dim")),
    _l(('    "trace_id": "tr_8c11f2",', "dim")),
    _l(('    "region": "eu-west-1",', "dim")),
    _l(('    "ts": "2026-07-18T14:02:11Z"', "dim")),
    _l(("  }", "dim")),
    _l(("}", "dim")),
)

_SHARED_FACETS: dict[str, dict[str, tuple[StyledLine, ...]]] = {
    "REQUEST": {"": _FACET_REQUEST},
    "HEADERS": {"": _FACET_HEADERS},
    "EVENTS": {"": _SSE_TABLE},
    "RAW": {"": _FACET_RAW},
}

# ── Index 1 — REQUESTS ──────────────────────────────────────────────────────


def _requests_index() -> AnalyseIndexData:
    """Build the requests index with per-request detail and evidence."""
    items = (
        ListItemVM("hdr:req-failed", _l(("FAILED", "label"), ("  2", "muted")), header=True),
        ListItemVM(
            "req:checkout",
            _l(
                ("x ", "err"),
                ("» ", "glyph"),
                ("Checkout   ", "bright"),
                ("POST ", "method.POST"),
                (" 2x 1+", "muted"),
            ),
            search_text="checkout post failed",
        ),
        ListItemVM(
            "req:orders",
            _l(
                ("x ", "err"),
                ("» ", "glyph"),
                ("List orders ", "fg"),
                ("GET ", "method.GET"),
                ("  1x 2+", "muted"),
            ),
            search_text="list orders get failed",
        ),
        ListItemVM("gap:req-1", _GAP, header=True),
        ListItemVM("hdr:req-advisory", _l(("ADVISORY", "label"), ("  1", "muted")), header=True),
        ListItemVM(
            "req:quote",
            _l(
                ("~ ", "warn"),
                ("» ", "glyph"),
                ("Price quote ", "fg"),
                ("GET ", "method.GET"),
                ("  3+ 1~", "muted"),
            ),
            search_text="price quote get advisory",
        ),
        ListItemVM("gap:req-2", _GAP, header=True),
        ListItemVM("hdr:req-passed", _l(("PASSED", "label"), ("  9 — folded", "dim")), header=True),
        ListItemVM(
            "req:product",
            _l(
                ("+ ", "ok"),
                ("» ", "glyph"),
                ("Get product ", "fg"),
                ("GET ", "method.GET"),
                ("  1+", "muted"),
            ),
            search_text="get product passed",
        ),
        ListItemVM("req:more", _l(("+ ", "ok"), ("… 8 more", "dim")), dimmed=True),
    )
    matrix_header = _l(
        ("CELL         SENT  ", "label"),
        ("BASELINE      ", "baseline"),
        ("CANDIDATE     ", "candidate"),
        ("ASSERTS        DIFF          TIME", "label"),
    )
    detail_for: dict[str, tuple[StyledLine, ...]] = {
        "req:checkout": (
            matrix_header,
            _l(
                (" pro·USD    ", "bright"),
                ("=     ", "ok"),
                ("+ 200 118ms   ", "ok"),
                ("x 500 2.1s    ", "err"),
                ("1+ · 1x        ", "err"),
                ("x 4 broken    ", "err"),
                ("2.4s", "muted"),
                cursor=True,
            ),
            _l(
                (" basic·USD  ", "fg"),
                ("≠ !   ", "status.outbound"),
                ("+ 200 118ms   ", "ok"),
                ("x 500 121ms   ", "err"),
                ("1+ · 1x        ", "err"),
                ("x 4 broken    ", "err"),
                ("121ms", "muted"),
            ),
            _l(
                (" pro·CAD    ", "fg"),
                ("=     ", "ok"),
                ("+ 200 124ms   ", "ok"),
                ("+ 200 130ms   ", "ok"),
                ("all held       ", "ok"),
                ("= same        ", "ok"),
                ("130ms", "muted"),
            ),
            _l(
                ("≠ SENT NOT 1:1", "status.outbound"),
                ("  ·  basic·USD only  ·  ", "muted"),
                ("x-api-version: 2024-05-01 → 2024-06-01", "fg"),
                ("  ← ⌂ env differs", "warn"),
            ),
            _l(
                ("  unfair for that cell — ", "dim"),
                ("d", "fg"),
                (" sent diff", "muted"),
                ("  ·  worst: ", "muted"),
                ("Candidate returned 500 (status-2xx)", "err"),
            ),
        ),
        "req:orders": (
            matrix_header,
            _l(
                (" pro·USD    ", "bright"),
                ("=     ", "ok"),
                ("+ 200 96ms    ", "ok"),
                ("x 500 142ms   ", "err"),
                ("1x             ", "err"),
                ("x 2 broken    ", "err"),
                ("142ms", "muted"),
                cursor=True,
            ),
            _l(
                (" basic·USD  ", "fg"),
                ("=     ", "ok"),
                ("+ 200 101ms   ", "ok"),
                ("+ 200 99ms    ", "ok"),
                ("all held       ", "ok"),
                ("= same        ", "ok"),
                ("99ms", "muted"),
            ),
            _l(
                ("= SENT 1:1", "ok"),
                ("  ·  both cells identical outbound — fair comparison", "muted"),
            ),
            _l(
                ("  worst: ", "muted"),
                ("Candidate returned 500 (status-2xx)", "err"),
                ("  ·  no outbound drift", "dim"),
            ),
        ),
        "req:quote": (
            matrix_header,
            _l(
                (" pro·USD    ", "bright"),
                ("=     ", "ok"),
                ("+ 200 88ms    ", "ok"),
                ("+ 200 91ms    ", "ok"),
                ("~ 1 advisory   ", "warn"),
                ("~ 1 drift     ", "warn"),
                ("91ms", "muted"),
                cursor=True,
            ),
            _l(
                (" pro·CAD    ", "fg"),
                ("=     ", "ok"),
                ("+ 200 90ms    ", "ok"),
                ("+ 200 89ms    ", "ok"),
                ("all held       ", "ok"),
                ("= same        ", "ok"),
                ("89ms", "muted"),
            ),
            _l(
                (" basic·USD  ", "fg"),
                ("=     ", "ok"),
                ("+ 200 93ms    ", "ok"),
                ("+ 200 95ms    ", "ok"),
                ("all held       ", "ok"),
                ("= same        ", "ok"),
                ("95ms", "muted"),
            ),
            _l(
                ("~ $.usage.tokens drifted 2.7% — inside ±5% tolerance · never gates", "warn"),
            ),
        ),
        "req:product": (
            matrix_header,
            _l(
                (" pro·USD    ", "bright"),
                ("=     ", "ok"),
                ("+ 200 45ms    ", "ok"),
                ("+ 200 44ms    ", "ok"),
                ("all held       ", "ok"),
                ("= same        ", "ok"),
                ("44ms", "muted"),
                cursor=True,
            ),
            _l(
                (" basic·USD  ", "fg"),
                ("=     ", "ok"),
                ("+ 200 48ms    ", "ok"),
                ("+ 200 46ms    ", "ok"),
                ("all held       ", "ok"),
                ("= same        ", "ok"),
                ("46ms", "muted"),
            ),
            _l(("+ all cells held — no differences beyond ignore rules", "ok")),
        ),
    }
    facet_for = dict(_SHARED_FACETS)
    facet_for["REQUEST"] = {"": _FACET_REQUEST, "req:quote": _FACET_REQUEST_QUOTE}
    return AnalyseIndexData(
        key="requests",
        list_panel=PanelVM(
            title="REQUESTS — fails first",
            hint="↑↓ request · space unfold",
            lines=tuple(item.line for item in items),
        ),
        detail_panel=PanelVM(
            title="» Checkout · POST /v1/checkout · ⊞ Plans",
            lines=detail_for["req:checkout"],
        ),
        inspect_facet="RESPONSE",
        inspect_lines=_EVIDENCE_TREE,
        items=items,
        detail_titles={
            "req:checkout": "» Checkout · POST /v1/checkout · ⊞ Plans",
            "req:orders": "» List orders · GET /v1/orders · ⊞ Plans",
            "req:quote": "» Price quote · GET /v1/quote · ⊞ Plans",
            "req:product": "» Get product · GET /v1/products/{id}",
        },
        detail_for=detail_for,
        inspect_for={
            "req:checkout": _EVIDENCE_TREE,
            "req:orders": _EVIDENCE_ORDERS,
            "req:quote": _EVIDENCE_QUOTE,
            "req:product": _EVIDENCE_PRODUCT,
        },
        facet_for=facet_for,
    )


# ── Index 2 — ISSUES ────────────────────────────────────────────────────────


def _issues_index() -> AnalyseIndexData:
    """Build the issues index with per-issue occurrences and evidence."""
    items = (
        ListItemVM("hdr:iss-fail", _l(("FAILURES", "label"), ("  2", "muted")), header=True),
        ListItemVM(
            "issue:1",
            _l(("x ", "err"), ("Candidate 500 vs 2xx", "bright")),
            search_text="candidate 500 status-2xx",
        ),
        ListItemVM(
            "hdr:iss-1-sub",
            _l(("   status-2xx · 3× · 2 req", "muted")),  # noqa: RUF001
            header=True,
        ),
        ListItemVM(
            "issue:2",
            _l(("x ", "err"), ("Price format changed", "fg")),
            search_text="price format price-decimal",
        ),
        ListItemVM(
            "hdr:iss-2-sub",
            _l(("   price-decimal · 3× · 1 req", "muted")),  # noqa: RUF001
            header=True,
        ),
        ListItemVM("gap:iss-1", _GAP, header=True),
        ListItemVM(
            "hdr:iss-adv",
            _l(("ADVISORIES", "label"), ("  1 · never gate", "muted")),
            header=True,
        ),
        ListItemVM(
            "issue:3",
            _l(("~ ", "warn"), ("Latency > 150ms", "fg")),
            search_text="latency slo advisory",
        ),
        ListItemVM("gap:iss-2", _GAP, header=True),
        ListItemVM(
            "hdr:iss-ignored",
            _l(("IGNORED", "label"), ("  87 silenced by rules", "dim")),
            header=True,
        ),
    )
    detail_for: dict[str, tuple[StyledLine, ...]] = {
        "issue:1": (
            _l(
                ("Candidate returned 500, expected 2xx", "bright"),
                ("   ·   ", "dim"),
                ("blocking", "err"),
                ("   ·   gate ", "muted"),
                ("x", "err"),
            ),
            _l(
                ("rule ", "muted"),
                ("status-2xx", "fg"),
                ("  ·  release-gate.yaml:18  ·  expected ", "muted"),
                ("200-299", "fg"),
                ("  observed ", "muted"),
                ("500", "err"),
                ("   ·   o", "fg"),
                (" open rule", "muted"),
            ),
            _l(("OCCURRENCES  3 — all candidate side", "label")),
            _l(
                (" » Checkout     pro·USD    ", "bright"),
                ("500 ", "err"),
                ("118ms · attempt 1/1", "muted"),
                cursor=True,
            ),
            _l(
                (" » Checkout     basic·USD  ", "fg"),
                ("500 ", "err"),
                ("121ms · attempt 1/1", "muted"),
            ),
            _l(
                (" » List orders  pro·USD    ", "fg"),
                ("500 ", "err"),
                ("142ms · attempt 1/1", "muted"),
            ),
        ),
        "issue:2": (
            _l(
                ("Price format changed — separator dropped", "bright"),
                ("   ·   ", "dim"),
                ("warn→gate", "err"),
                ("   ·   gate ", "muted"),
                ("x", "err"),
            ),
            _l(
                ("rule ", "muted"),
                ("price-decimal", "fg"),
                ("  ·  release-gate.yaml:24  ·  expected ", "muted"),
                ("#,##0.00", "fg"),
                ("  observed ", "muted"),
                ("1234.56", "err"),
                ("   ·   o", "fg"),
                (" open rule", "muted"),
            ),
            _l(("OCCURRENCES  3 — one request", "label")),
            _l(
                (" » Price quote  pro·USD    ", "bright"),
                ('"1,234.56" → "1234.56"', "err"),
                cursor=True,
            ),
            _l((" » Price quote  pro·CAD    ", "fg"), ('"1,234.56" → "1234.56"', "err")),
            _l((" » Price quote  basic·EUR  ", "fg"), ('"1.234,56" → "1234.56"', "err")),
        ),
        "issue:3": (
            _l(
                ("Latency above 150ms on candidate", "bright"),
                ("   ·   ", "dim"),
                ("advisory", "warn"),
                ("   ·   never gates", "muted"),
            ),
            _l(
                ("rule ", "muted"),
                ("latency-slo", "fg"),
                ("  ·  profiles/observe.yaml:7  ·  budget ", "muted"),
                ("150ms", "fg"),
                ("  worst ", "muted"),
                ("212ms", "warn"),
            ),
            _l(("OCCURRENCES  2 — candidate side", "label")),
            _l(
                (" » Checkout     pro·USD    ", "bright"),
                ("212ms ", "warn"),
                ("vs 118ms baseline", "muted"),
                cursor=True,
            ),
            _l(
                (" » List orders  pro·USD    ", "fg"),
                ("164ms ", "warn"),
                ("vs 96ms baseline", "muted"),
            ),
        ),
    }
    return AnalyseIndexData(
        key="issues",
        list_panel=PanelVM(
            title="ISSUES — grouped by gate effect",
            hint="↑↓ issue · enter occurrence",
            lines=tuple(item.line for item in items),
        ),
        detail_panel=PanelVM(title="ISSUE — 1 of 3", lines=detail_for["issue:1"]),
        inspect_facet="RESPONSE",
        inspect_lines=_EVIDENCE_TREE,
        items=items,
        detail_titles={
            "issue:1": "ISSUE — 1 of 3",
            "issue:2": "ISSUE — 2 of 3",
            "issue:3": "ISSUE — 3 of 3",
        },
        detail_for=detail_for,
        inspect_for={
            "issue:1": _EVIDENCE_TREE,
            "issue:2": _EVIDENCE_PRICE,
            "issue:3": _EVIDENCE_LATENCY,
        },
        facet_for=dict(_SHARED_FACETS),
    )


# ── Index 3 — RULES ─────────────────────────────────────────────────────────


def _rules_index() -> AnalyseIndexData:
    """Build the rules index with per-rule scorecards and evidence."""
    items = (
        ListItemVM("hdr:rule-broken", _l(("BROKEN", "label"), ("  2", "muted")), header=True),
        ListItemVM(
            "rule:1",
            _l(("x ", "err"), ("◈ ", "glyph"), ("status-2xx", "bright")),
            search_text="status-2xx blocking",
        ),
        ListItemVM("hdr:rule-1-sub", _l(("   blocking · broke 3/12", "muted")), header=True),
        ListItemVM(
            "rule:2",
            _l(("x ", "err"), ("Δ ", "glyph"), ("price-decimal", "fg")),
            search_text="price-decimal warn gate",
        ),
        ListItemVM("hdr:rule-2-sub", _l(("   warn→gate · broke 3/9", "muted")), header=True),
        ListItemVM("gap:rule-1", _GAP, header=True),
        ListItemVM("hdr:rule-adv", _l(("ADVISORY", "label"), ("  2", "muted")), header=True),
        ListItemVM(
            "rule:3",
            _l(("~ ", "warn"), ("◈ ", "glyph"), ("latency-slo", "fg")),
            search_text="latency-slo advisory",
        ),
        ListItemVM(
            "rule:4",
            _l(("~ ", "warn"), ("Δ ", "glyph"), ("token-usage", "fg")),
            search_text="token-usage advisory",
        ),
        ListItemVM("gap:rule-2", _GAP, header=True),
        ListItemVM("hdr:rule-held", _l(("HELD", "label"), ("  10 · folded", "dim")), header=True),
        ListItemVM(
            "hdr:rule-ne",
            _l(("NOT EVALUATED", "label"), ("  3 — nothing to judge", "dim")),
            header=True,
        ),
    )
    detail_for: dict[str, tuple[StyledLine, ...]] = {
        "rule:1": (
            _l(
                ("◈ ", "glyph"),
                ("status-2xx", "bright"),
                ("   response status must be 200-299   ·   ", "muted"),
                ("blocking", "err"),
                ("   ·   applies to ", "muted"),
                ("Checkout · List orders", "fg"),
            ),
            _l(
                ("source ", "muted"),
                ("profiles/release-gate.yaml:18", "fg"),
                ("   evaluated 12 · broke ", "muted"),
                ("3", "err"),
                (" · held ", "muted"),
                ("9", "ok"),
                (" · n/e ", "muted"),
                ("0", "fg"),
            ),
            _l(
                ("▌baseline ", "baseline"),
                ("+ held 6/6 · 100%", "ok"),
                ("        ▌candidate ", "candidate"),
                ("x broke 3/6 · 50%", "err"),
                ("     same three 500s", "muted"),
            ),
            _l(
                ("OCCURRENCES", "label"),
                ("  Checkout pro·USD · Checkout basic·USD · List orders pro·USD", "fg"),
            ),
        ),
        "rule:2": (
            _l(
                ("Δ ", "glyph"),
                ("price-decimal", "bright"),
                ("   price fields keep #,##0.00 format   ·   ", "muted"),
                ("warn→gate", "err"),
                ("   ·   applies to ", "muted"),
                ("Price quote", "fg"),
            ),
            _l(
                ("source ", "muted"),
                ("profiles/release-gate.yaml:24", "fg"),
                ("   evaluated 9 · broke ", "muted"),
                ("3", "err"),
                (" · held ", "muted"),
                ("6", "ok"),
            ),
            _l(
                ("▌baseline ", "baseline"),
                ("+ held 9/9 · 100%", "ok"),
                ("        ▌candidate ", "candidate"),
                ("x broke 3/9 · 33%", "err"),
                ("     separator dropped on USD/CAD/EUR", "muted"),
            ),
            _l(
                ("OCCURRENCES", "label"),
                ("  Price quote pro·USD · pro·CAD · basic·EUR", "fg"),
            ),
        ),
        "rule:3": (
            _l(
                ("◈ ", "glyph"),
                ("latency-slo", "bright"),
                ("   p95 under 150ms   ·   ", "muted"),
                ("advisory — never gates", "warn"),
                ("   ·   applies to ", "muted"),
                ("all requests", "fg"),
            ),
            _l(
                ("source ", "muted"),
                ("profiles/observe.yaml:7", "fg"),
                ("   evaluated 12 · flagged ", "muted"),
                ("2", "warn"),
                (" · within ", "muted"),
                ("10", "ok"),
            ),
            _l(
                ("▌baseline ", "baseline"),
                ("+ within 12/12", "ok"),
                ("        ▌candidate ", "candidate"),
                ("~ flagged 2/12", "warn"),
                ("     worst 212ms · Checkout pro·USD", "muted"),
            ),
            _l(
                ("OCCURRENCES", "label"),
                ("  Checkout pro·USD · List orders pro·USD", "fg"),
            ),
        ),
        "rule:4": (
            _l(
                ("Δ ", "glyph"),
                ("token-usage", "bright"),
                ("   usage.tokens within ±5%   ·   ", "muted"),
                ("advisory", "warn"),
                ("   ·   applies to ", "muted"),
                ("Price quote", "fg"),
            ),
            _l(
                ("source ", "muted"),
                ("profiles/observe.yaml:12", "fg"),
                ("   evaluated 6 · flagged ", "muted"),
                ("1", "warn"),
                (" · within ", "muted"),
                ("5", "ok"),
            ),
            _l(
                ("▌baseline ", "baseline"),
                ("258 avg tokens", "fg"),
                ("        ▌candidate ", "candidate"),
                ("251 avg · drift 2.7%", "warn"),
                ("     inside tolerance", "muted"),
            ),
            _l(("OCCURRENCES", "label"), ("  Price quote pro·USD", "fg")),
        ),
    }
    return AnalyseIndexData(
        key="rules",
        list_panel=PanelVM(
            title="RULES — every rule that could judge",
            hint="↑↓ rule · o source",
            lines=tuple(item.line for item in items),
        ),
        detail_panel=PanelVM(title="RULE — status-2xx", lines=detail_for["rule:1"]),
        inspect_facet="RESPONSE",
        inspect_lines=_EVIDENCE_TREE,
        items=items,
        detail_titles={
            "rule:1": "RULE — status-2xx",
            "rule:2": "RULE — price-decimal",
            "rule:3": "RULE — latency-slo",
            "rule:4": "RULE — token-usage",
        },
        detail_for=detail_for,
        inspect_for={
            "rule:1": _EVIDENCE_TREE,
            "rule:2": _EVIDENCE_PRICE,
            "rule:3": _EVIDENCE_LATENCY,
            "rule:4": _EVIDENCE_QUOTE,
        },
        facet_for=dict(_SHARED_FACETS),
    )


# ── Index 4 — FIELDS ────────────────────────────────────────────────────────


def _fields_index() -> AnalyseIndexData:
    """Build the fields index with per-path value tables and evidence."""
    items = (
        ListItemVM("hdr:fld-broken", _l(("BROKEN", "label"), ("  4", "muted")), header=True),
        ListItemVM(
            "field:1",
            _l(("Δ ", "err"), ("$.quote.price", "bright")),
            search_text="quote price changed",
        ),
        ListItemVM("hdr:fld-1-sub", _l(("   changed · 3/6 cells", "muted")), header=True),
        ListItemVM(
            "field:2",
            _l(("Δ ", "err"), ("$.quote.taxRate", "fg")),
            search_text="quote taxrate changed",
        ),
        ListItemVM(
            "field:3",
            _l(("+ ", "err"), ("$.error", "fg"), ("  added", "muted")),
            search_text="error added",
        ),
        ListItemVM(
            "field:4",
            _l(("- ", "err"), ("$.order_id", "fg"), ("  removed", "muted")),
            search_text="order_id removed",
        ),
        ListItemVM("gap:fld-1", _GAP, header=True),
        ListItemVM("hdr:fld-adv", _l(("ADVISORY", "label"), ("  1", "muted")), header=True),
        ListItemVM(
            "field:5",
            _l(("~ ", "warn"), ("$.usage.tokens", "fg")),
            search_text="usage tokens advisory",
        ),
        ListItemVM("gap:fld-2", _GAP, header=True),
        ListItemVM(
            "hdr:fld-ignored",
            _l(("IGNORED", "label"), ("  2 paths · 18×", "dim")),  # noqa: RUF001
            header=True,
        ),
        ListItemVM("hdr:fld-same", _l(("SAME", "label"), ("  31 · folded", "dim")), header=True),
    )
    value_header = _l(
        ("CELL        ", "label"),
        ("BASELINE        ", "baseline"),
        ("CANDIDATE       ", "candidate"),
        ("VERDICT", "label"),
    )
    detail_for: dict[str, tuple[StyledLine, ...]] = {
        "field:1": (
            _l(
                ("$.quote.price", "bright"),
                ("  string · Δ 3/6 cells · 1 request", "muted"),
                ("  ·  rule ", "muted"),
                ("price-decimal", "fg"),
                (" · #,##0.00 · ", "muted"),
                ("failure", "err"),
            ),
            value_header,
            _l(
                (" pro·USD   ", "bright"),
                ('"1,234.56"      ', "fg"),
                ('"1234.56"       ', "fg"),
                ("x broke — separator dropped", "err"),
                cursor=True,
            ),
            _l(
                (" pro·CAD   ", "fg"),
                ('"1,234.56"      ', "fg"),
                ('"1234.56"       ', "fg"),
                ("x broke", "err"),
            ),
            _l(
                (" basic·USD ", "fg"),
                ('"1,234.56"      ', "fg"),
                ('"1,234.56"      ', "fg"),
                ("+ same", "ok"),
            ),
            _l(
                ("why it gates: ", "muted"),
                ("consistent formatting required for downstream parsing", "fg"),
            ),
        ),
        "field:2": (
            _l(
                ("$.quote.taxRate", "bright"),
                ("  number · Δ 2/6 cells · 1 request", "muted"),
                ("  ·  rule ", "muted"),
                ("tax-precision", "fg"),
                (" · ", "muted"),
                ("failure", "err"),
            ),
            value_header,
            _l(
                (" pro·USD   ", "bright"),
                ("0.0825          ", "fg"),
                ("0.08            ", "fg"),
                ("x broke — precision lost", "err"),
                cursor=True,
            ),
            _l(
                (" pro·CAD   ", "fg"),
                ("0.13            ", "fg"),
                ("0.13            ", "fg"),
                ("+ same", "ok"),
            ),
            _l(
                ("why it gates: ", "muted"),
                ("tax amounts must round-trip exactly", "fg"),
            ),
        ),
        "field:3": (
            _l(
                ("$.error", "bright"),
                ("  string · + added in 3/6 cells · 2 requests", "muted"),
                ("  ·  ", "muted"),
                ("no rule matches added error bodies", "dim"),
                (" · ", "muted"),
                ("failure", "err"),
            ),
            value_header,
            _l(
                (" pro·USD   ", "bright"),
                ("— absent        ", "dim"),
                ('"Internal S…"   ', "fg"),
                ("+ added with the 500", "err"),
                cursor=True,
            ),
            _l(
                (" basic·USD ", "fg"),
                ("— absent        ", "dim"),
                ('"Internal S…"   ', "fg"),
                ("+ added", "err"),
            ),
            _l(
                (" pro·CAD   ", "fg"),
                ("— absent        ", "dim"),
                ("— absent        ", "dim"),
                ("= same", "ok"),
            ),
            _l(
                ("appears only where candidate returned 500 — ", "muted"),
                ("same root cause as status-2xx", "fg"),
            ),
        ),
        "field:4": (
            _l(
                ("$.order_id", "bright"),
                ("  string · - removed in 2/6 cells · Checkout only", "muted"),
                ("  ·  ", "muted"),
                ("failure", "err"),
            ),
            value_header,
            _l(
                (" pro·USD   ", "bright"),
                ('"ord_7f3a9b2c"  ', "fg"),
                ("— absent        ", "dim"),
                ("- removed", "err"),
                cursor=True,
            ),
            _l(
                (" basic·USD ", "fg"),
                ('"ord_1c22d0aa"  ', "fg"),
                ("— absent        ", "dim"),
                ("- removed", "err"),
            ),
            _l(
                ("500 bodies drop the order id — ", "muted"),
                ("downstream consumers would break", "fg"),
            ),
        ),
        "field:5": (
            _l(
                ("$.usage.tokens", "bright"),
                ("  number · ~ drift 1/6 cells", "muted"),
                ("  ·  rule ", "muted"),
                ("token-usage", "fg"),
                (" · ±5% · ", "muted"),
                ("advisory", "warn"),
            ),
            value_header,
            _l(
                (" pro·USD   ", "bright"),
                ("258             ", "fg"),
                ("251             ", "fg"),
                ("~ drift 2.7%", "warn"),
                cursor=True,
            ),
            _l(
                (" pro·CAD   ", "fg"),
                ("244             ", "fg"),
                ("244             ", "fg"),
                ("+ same", "ok"),
            ),
            _l(("inside tolerance — recorded, never gates", "dim")),
        ),
    }
    return AnalyseIndexData(
        key="fields",
        list_panel=PanelVM(
            title="FIELDS — normalized JSON paths",
            hint="↑↓ path · g group by rule",
            lines=tuple(item.line for item in items),
        ),
        detail_panel=PanelVM(title="FIELD — $.quote.price", lines=detail_for["field:1"]),
        inspect_facet="RESPONSE",
        inspect_lines=_EVIDENCE_TREE,
        items=items,
        detail_titles={
            "field:1": "FIELD — $.quote.price",
            "field:2": "FIELD — $.quote.taxRate",
            "field:3": "FIELD — $.error",
            "field:4": "FIELD — $.order_id",
            "field:5": "FIELD — $.usage.tokens",
        },
        detail_for=detail_for,
        inspect_for={
            "field:1": _EVIDENCE_PRICE,
            "field:3": _EVIDENCE_TREE,
            "field:4": _EVIDENCE_TREE,
            "field:5": _EVIDENCE_QUOTE,
        },
        facet_for=dict(_SHARED_FACETS),
    )


# ── Index 5 — TIMELINE ──────────────────────────────────────────────────────


def _timeline_index() -> AnalyseIndexData:
    """Build the timeline index with per-event causal chains."""
    items = (
        ListItemVM("hdr:ev-first", _l(("7 failed events first", "dim")), header=True),
        ListItemVM(
            "ev:1",
            _l(("x ", "err"), ("02.254 cand assert", "bright")),
            search_text="candidate assert status",
        ),
        ListItemVM("hdr:ev-1-sub", _l(("   Checkout pro·USD", "muted")), header=True),
        ListItemVM(
            "ev:2",
            _l(("x ", "err"), ("02.257 schema assert", "fg")),
            search_text="schema assert",
        ),
        ListItemVM(
            "ev:3",
            _l(("x ", "err"), ("02.263 env diff", "fg")),
            search_text="env diff drift",
        ),
        ListItemVM(
            "ev:4",
            _l(("! ", "error-op"), ("04.012 connect refused", "fg")),
            search_text="connect refused error",
        ),
        ListItemVM("hdr:ev-4-sub", _l(("   List orders pro·USD", "muted")), header=True),
        ListItemVM("gap:ev-1", _GAP, header=True),
        ListItemVM(
            "hdr:ev-all", _l(("ALL EVENTS", "label"), ("  21 · by call", "muted")), header=True
        ),
        ListItemVM(
            "ev:call-checkout",
            _l(("▸ ", "muted"), ("Checkout · pro·USD", "fg"), ("  8", "muted")),
            dimmed=True,
        ),
        ListItemVM(
            "ev:call-quote",
            _l(("▸ ", "muted"), ("Price quote · pro·USD", "fg"), ("  4", "muted")),
            dimmed=True,
        ),
        ListItemVM("ev:call-more", _l(("▸ ", "muted"), ("… 4 more calls", "dim")), dimmed=True),
    )
    detail_for: dict[str, tuple[StyledLine, ...]] = {
        "ev:1": (
            _l(
                ("candidate status assertion ", "bright"),
                ("x FAILED", "status.failed"),
                ("   +02.254s · production · attempt 1 · 3ms", "muted"),
            ),
            _l(
                ("rule ", "muted"),
                ("status-2xx", "fg"),
                (" · observed 500 · gate effect: candidate assertions → x", "muted"),
            ),
            _l(("CAUSAL CHAIN", "label")),
            _l(
                ("response 500", "err"),
                ("  →  ", "dim"),
                ("this failure", "bright"),
                ("  →  ", "dim"),
                ("schema assert x", "err"),
                ("  →  ", "dim"),
                ("env diff x drift", "err"),
                ("  →  ", "dim"),
                ("issue created", "fg"),
            ),
            _l(
                (
                    "one 500 explains 3 downstream events — fix the cause, not the symptoms",
                    "dim",
                )
            ),
        ),
        "ev:2": (
            _l(
                ("schema assertion ", "bright"),
                ("x FAILED", "status.failed"),
                ("   +02.257s · production · 3ms after the status failure", "muted"),
            ),
            _l(
                ("schema ", "muted"),
                ("quote-response.json", "fg"),
                (" · error body does not match the declared 2xx schema", "muted"),
            ),
            _l(("CAUSAL CHAIN", "label")),
            _l(
                ("response 500", "err"),
                ("  →  ", "dim"),
                ("status assert x", "err"),
                ("  →  ", "dim"),
                ("this failure", "bright"),
                ("  →  ", "dim"),
                ("issue #1 evidence", "fg"),
            ),
            _l(("same root cause — schema noise follows the 500", "dim")),
        ),
        "ev:3": (
            _l(
                ("environment diff ", "bright"),
                ("x DRIFT", "status.drift"),
                ("   +02.263s · x-api-version differs between lanes", "muted"),
            ),
            _l(
                ("⌂ env ", "muted"),
                ("API_VERSION", "fg"),
                (" · staging ", "muted"),
                ("2024-05-01", "baseline"),
                (" · production ", "muted"),
                ("2024-06-01", "candidate"),
            ),
            _l(("CAUSAL CHAIN", "label")),
            _l(
                ("env drift", "warn"),
                ("  →  ", "dim"),
                ("sent ≠ 1:1", "status.outbound"),
                ("  →  ", "dim"),
                ("possible cause of response 500", "err"),
                ("  →  ", "dim"),
                ("flagged unfair", "fg"),
            ),
            _l(("this event PRECEDES the 500 — likely cause · d sent diff", "dim")),
        ),
        "ev:4": (
            _l(
                ("connection refused ", "bright"),
                ("! ERROR", "status.error"),
                ("   +04.012s · production · attempt 1 of 2 · retried in 1.0s", "muted"),
            ),
            _l(
                ("transport ", "muted"),
                ("econnrefused", "err"),
                (" · 10.0.4.12:443 · no HTTP exchange happened", "muted"),
            ),
            _l(("CAUSAL CHAIN", "label")),
            _l(
                ("connect refused", "err"),
                ("  →  ", "dim"),
                ("retry scheduled", "fg"),
                ("  →  ", "dim"),
                ("attempt 2 + 200 in 96ms", "ok"),
                ("  →  ", "dim"),
                ("call recovered", "ok"),
            ),
            _l(("different chain — infrastructure blip, not an API regression", "dim")),
        ),
    }
    facet_for = {
        "REQUEST": {"": _FACET_REQUEST},
        "HEADERS": {"": _FACET_HEADERS},
        "RAW": {"": _FACET_RAW},
        "RESPONSE": {"": _EVIDENCE_TREE},
    }
    return AnalyseIndexData(
        key="timeline",
        list_panel=PanelVM(
            title="TIMELINE — causal order",
            hint="↑↓ event · space unfold call",
            lines=tuple(item.line for item in items),
        ),
        detail_panel=PanelVM(title="EVENT — +02.254s candidate assert", lines=detail_for["ev:1"]),
        inspect_facet="EVENTS",
        inspect_lines=_SSE_TABLE,
        items=items,
        detail_titles={
            "ev:1": "EVENT — +02.254s candidate assert",
            "ev:2": "EVENT — +02.257s schema assert",
            "ev:3": "EVENT — +02.263s env diff",
            "ev:4": "EVENT — +04.012s connect refused",
        },
        detail_for=detail_for,
        inspect_for={
            "ev:1": _SSE_TABLE,
            "ev:4": _SSE_CONNECT,
        },
        facet_for=facet_for,
    )


# ── Deep dives ──────────────────────────────────────────────────────────────


def _deep_dives() -> dict[str, PanelVM]:
    """Build the two deep-dive panels reachable via z and d."""
    response_diff = PanelVM(
        title="» Checkout · pro·USD · INSPECT expanded — RESPONSE ⇄ DIFF",
        hint="n/p hunk · d diff⇄side · s split · esc back",
        lines=(
            _l(
                ("▌baseline ", "baseline"),
                ("200 · 126ms · 1.2 KB", "muted"),
                ("      ▌candidate ", "candidate"),
                ("500 · 118ms · 0.4 KB", "muted"),
                ("      4 hunks · hunk headers are JSON paths", "dim"),
            ),
            _l((" @ @@ $.status @@", "diff.hunk")),
            _l((" - HTTP/1.1 ", "diff.del"), ("200 OK", "diff.edel")),
            _l((" + HTTP/1.1 ", "diff.add"), ("500 Internal Server Error", "diff.eadd")),
            _l((" @ @@ $.success · $.error @@", "diff.hunk")),
            _l(("   {", "diff.ctx")),
            _l((' -   "success": ', "diff.del"), ("true", "diff.edel"), (",", "diff.del")),
            _l((' +   "success": ', "diff.add"), ("false", "diff.eadd"), (",", "diff.add")),
            _l((' +   "error": "Internal Server Error",', "diff.add")),
            _l((' +   "code": "INTERNAL_ERROR",', "diff.add")),
            _l((" @ @@ $.order_id · $.total @@", "diff.hunk")),
            _l((' -   "order_id": "ord_7f3a9b2c",', "diff.del")),
            _l((' -   "total": ', "diff.del"), ("1299", "diff.edel"), (",", "diff.del")),
            _l((' +   "request_id": "req_9d1e4f",', "diff.add")),
            _l(("··· 38 unchanged lines — space expands · anchored at $.metadata", "dim")),
            _l(("   }", "diff.ctx")),
            _l(
                ("Δ ", "label"),
                ("4 broken · 1 added · 1 removed", "err"),
                ("   ·   ignored by rules: $.headers.date · $.request_id pattern", "dim"),
            ),
        ),
    )
    sent_diff = PanelVM(
        title="» Checkout · basic·USD · INSPECT expanded — REQUEST ≠ SENT",
        hint="d declare expected · r rerun fair · esc back",
        tone="warning",
        lines=(
            _l(
                ("≠ OUTBOUND", "status.outbound"),
                (
                    "  the request sent to the candidate was not identical — "
                    "this comparison may be unfair",
                    "warn",
                ),
            ),
            _l(
                ("PART      VERDICT   ", "label"),
                ("BASELINE · staging              ", "baseline"),
                ("CANDIDATE · production          ", "candidate"),
                ("CAUSE", "label"),
            ),
            _l(
                (" method   ", "muted"),
                ("= same    ", "ok"),
                ("POST                            ", "method.POST"),
                ("POST                            ", "method.POST"),
                ("—", "dim"),
            ),
            _l(
                (" query    ", "muted"),
                ("= same    ", "ok"),
                ("plan=basic&currency=USD         ", "fg"),
                ("plan=basic&currency=USD         ", "fg"),
                ("—", "dim"),
            ),
            _l(
                (" headers  ", "muted"),
                ("≠ 1 of 6  ", "status.outbound"),
                ("x-api-version: 2024-05-01       ", "fg"),
                ("x-api-version: 2024-06-01       ", "warn"),
                ("⌂ env API_VERSION differs", "warn"),
            ),
            _l(
                (" body     ", "muted"),
                ("= same    ", "ok"),
                ("sha256 9c41…e2 · 842 B          ", "fg"),
                ("sha256 9c41…e2 · 842 B          ", "fg"),
                ("cell values injected once", "dim"),
            ),
            _l((" @ @@ headers · x-api-version @@", "diff.hunk")),
            _l(
                (" - x-api-version: ", "diff.del"),
                ("2024-05-01", "diff.edel"),
                ("      ← staging .env.staging:2", "diff.del"),
            ),
            _l(
                (" + x-api-version: ", "diff.add"),
                ("2024-06-01", "diff.eadd"),
                ("      ← production .env.production:2", "diff.add"),
            ),
            _l(
                (
                    "the candidate 500 may be caused by the newer API version — "
                    "fix the env, or declare the header expected-to-differ",
                    "fg",
                )
            ),
            _l(
                (" d ", "chip.active"),
                (" declare expected", "bright"),
                ("     r", "fg"),
                (" rerun with baseline value", "muted"),
                ("     o", "fg"),
                (" open .env.production:2", "muted"),
            ),
        ),
    )
    return {"response": response_diff, "sent": sent_diff}


def analyse() -> AnalyseVM:
    """Build the full analyse fixture."""
    return AnalyseVM(
        strip=STRIP,
        indexes=(
            _requests_index(),
            _issues_index(),
            _rules_index(),
            _fields_index(),
            _timeline_index(),
        ),
        deep_dives=_deep_dives(),
    )
