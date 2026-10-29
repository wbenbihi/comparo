"""The showcase universe — ported verbatim from the validated clickable v2."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass(frozen=True)
class RequestFx:
    """One catalog request."""

    id: str
    name: str
    method: str
    tags: tuple[str, ...]
    desc: str
    file: str
    matrix: bool = False
    write: bool = False
    stream: bool = False
    schema: str = ""


REQS: tuple[RequestFx, ...] = (
    RequestFx(
        "quote",
        "Price quote",
        "GET",
        ("pricing",),
        "The flagship drift, x3 plans: candidate bumps price +6.0 and "
        'taxRate "0.20" to "0.25" on every plan.',
        "requests/quote.yaml",
        matrix=True,
        schema="Contract (strict)",
    ),
    RequestFx(
        "feed",
        "Price feed",
        "GET",
        ("pricing",),
        "Streams 5 pricing events over SSE, diffed event-by-event.",
        "requests/feed.yaml",
        stream=True,
    ),
    RequestFx(
        "checkout",
        "Checkout",
        "POST",
        ("checkout",),
        "Creates an order from the selected quote — the one write in the showcase.",
        "requests/checkout.yaml",
        write=True,
        schema="Contract (strict)",
    ),
    RequestFx(
        "orderstat",
        "Order status",
        "GET",
        ("checkout",),
        "Polls the created order — depends on Checkout via $use order_id.",
        "requests/order-status.yaml",
    ),
    RequestFx(
        "product",
        "Get product",
        "GET",
        ("catalog",),
        "Single product by id — the boring stable one.",
        "requests/product.yaml",
    ),
    RequestFx(
        "products",
        "List products",
        "GET",
        ("catalog",),
        "Paged catalog listing; page/size from params.",
        "requests/products.yaml",
    ),
    RequestFx(
        "health",
        "Health",
        "GET",
        ("health",),
        'The tiny health contract: {"status":"ok"}.',
        "requests/health.yaml",
        schema="Health",
    ),
    RequestFx(
        "refund",
        "Refund order",
        "POST",
        ("checkout",),
        "Reverses an order — write-capable, excluded from the showcase gate.",
        "requests/refund.yaml",
        write=True,
    ),
)
REQ_BY_ID = {r.id: r for r in REQS}
TAGS = ("all", "pricing", "checkout", "catalog", "health")
PLAN_CELLS = ("basic", "pro", "scale")


@dataclass(frozen=True)
class CellFx:
    """One executed cell of the showcase run."""

    key: str
    req: str
    cell: str
    verdict: str  # same | drift
    b: tuple[str, str, str]
    c: tuple[str, str, str]
    fields: tuple[str, ...] = ()
    slow: bool = False
    stream: bool = False
    write: bool = False


CELLS: tuple[CellFx, ...] = (
    CellFx(
        "quote-basic",
        "Price quote",
        "basic",
        "drift",
        ("200", "41ms", "312 B"),
        ("200", "96ms", "318 B"),
        ("price", "taxRate", "total"),
    ),
    CellFx(
        "quote-pro",
        "Price quote",
        "pro",
        "drift",
        ("200", "39ms", "312 B"),
        ("200", "92ms", "318 B"),
        ("price", "taxRate", "total"),
    ),
    CellFx(
        "quote-scale",
        "Price quote",
        "scale",
        "drift",
        ("200", "44ms", "314 B"),
        ("200", "98ms", "320 B"),
        ("price", "taxRate", "total"),
    ),
    CellFx(
        "checkout",
        "Checkout",
        "—",
        "same",
        ("200", "58ms", "204 B"),
        ("200", "280ms", "204 B"),
        slow=True,
        write=True,
    ),
    CellFx(
        "feed",
        "Price feed",
        "stream",
        "same",
        ("200", "5 events", "—"),
        ("200", "5 events", "—"),
        stream=True,
    ),
    CellFx(
        "product", "Get product", "—", "same", ("200", "12ms", "188 B"), ("200", "14ms", "188 B")
    ),
)
CELL_BY_KEY = {c.key: c for c in CELLS}


@dataclass(frozen=True)
class FieldFx:
    """One drifted field across cells."""

    key: str
    path: str
    cells: str
    mode: str
    b: str
    c: str
    why: str
    outbound: bool = False


FIELDS: tuple[FieldFx, ...] = (
    FieldFx(
        "price",
        "$.price",
        "x3 cells",
        "exact",
        "94.0",
        "100.0",
        "candidate bumps every plan by 6.0",
    ),
    FieldFx(
        "taxRate",
        "$.taxRate",
        "x3 cells",
        "exact",
        '"0.20"',
        '"0.25"',
        "outbound differs — candidate env sends TAX_RATE=0.25",
        outbound=True,
    ),
    FieldFx(
        "total",
        "$.total",
        "x3 cells",
        "tolerance ±0.5",
        "112.80",
        "125.00",
        "follows price + taxRate — same root cause",
    ),
)
FIELD_BY_KEY = {f.key: f for f in FIELDS}


@dataclass(frozen=True)
class RuleFx:
    """One silencing rule of the diff profile."""

    key: str
    path: str
    mode: str
    profile: str
    absorbed: str
    why: str


RULES: tuple[RuleFx, ...] = (
    RuleFx(
        "headers",
        "$.headers.*",
        "ignore",
        "diff.showcase",
        "5 fields · 3 requests",
        "volatile — trace ids, hosts, timestamps",
    ),
    RuleFx(
        "total-tol",
        "$.total",
        "tolerance ±0.5",
        "diff.showcase",
        "0 this run (drift exceeds it)",
        "rounding drift is expected up to ±0.5",
    ),
)
RULE_BY_KEY = {r.key: r for r in RULES}


@dataclass(frozen=True)
class ReportFx:
    """One saved run in the archive."""

    key: str
    state: str
    sc: str  # same | drift | warn
    when: str
    scope: str
    drift: int
    calls: int
    dur: str
    exit_code: int
    top: tuple[str, ...]
    note: str


REPORTS: tuple[ReportFx, ...] = (
    ReportFx(
        "8c3e11",
        "✗ FAIL",
        "drift",
        "today 14:42",
        "stable ⇄ canary · pricing+",
        3,
        12,
        "4s",
        1,
        ("$.price x3", "$.taxRate x3 · outbound", "$.total x3"),
        "one root cause across 3 cells — candidate pricing bump",
    ),
    ReportFx(
        "7b21aa",
        "✓ PASS",
        "same",
        "today 12:37",
        "stable ⇄ canary · smoke",
        0,
        6,
        "2s",
        0,
        (),
        "clean — 6 calls, 41 fields compared, 5 silenced by rules",
    ),
    ReportFx(
        "6f09c2",
        "✗ ERROR",
        "warn",
        "yesterday 18:03",
        "stable only · assertions",
        0,
        0,
        "0s",
        2,
        ("connect refused — canary.internal:443",),
        "nothing judged: transport failed before any call completed",
    ),
    ReportFx(
        "5aa000",
        "✓ PASS",
        "same",
        "yesterday 09:12",
        "stable ⇄ canary · catalog",
        0,
        8,
        "3s",
        0,
        (),
        "catalog surface clean across 8 calls",
    ),
)


@dataclass
class ScopeState:
    """Mutable launch-scope selection state."""

    on: dict[str, bool] = field(
        default_factory=lambda: {
            "quote": True,
            "feed": True,
            "checkout": True,
            "product": True,
            "orderstat": False,
            "products": False,
            "health": False,
            "refund": False,
        }
    )
    cells: dict[str, bool] = field(
        default_factory=lambda: {
            "basic": True,
            "pro": True,
            "scale": True,
        }
    )
    cand: bool = True
    tag: str = "all"
    query: str = ""
    trials: int = 1
    attempts: int = 2
    failfast: str = "off"
    rate: int = 100
    timeout: str = "5s"
    writes: str = "ask"

    def counts(self) -> tuple[int, int, int, int, int]:
        """reqs, cells, writes, envs, calls."""
        reqs = cells = writes = 0
        for rid, on in self.on.items():
            if not on:
                continue
            fx = REQ_BY_ID[rid]
            reqs += 1
            cells += sum(1 for v in self.cells.values() if v) if fx.matrix else 1
            if fx.write:
                writes += 1
        envs = 2 if self.cand else 1
        return reqs, cells, writes, envs, cells * envs
