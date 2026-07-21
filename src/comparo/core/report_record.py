"""The versioned report record — the single artifact comparo writes per invocation.

One envelope covers all three kinds; ``kind`` selects which optional sections are
present. A ``run`` is sides + assertions (no candidate, no comparison); a ``diff``
is sides + comparison (no assertions); an ``execution`` is both **over the same
sides** — the exchange is stored once per cell-side, never duplicated. It
captures the *whole* interaction — the resolved outbound request (with its
provenance trail) **and** the response, per side — so a saved report replays in
full detail offline, feeds the TUI's Report tab, and is the source the CI
reporters project.

Non-repetition rules: rule inventories live once at record level and cells
reference them by id; ``same`` fields serialize path-only (values recovered by
pure lookup into the stored side — recomputing a diff from redacted bodies is
unsound, so stored verdicts are authoritative); the body is exactly one of
parsed ``body`` ⊕ ``events`` ⊕ ``bodyText`` ⊕ a binary digest; derived values
(latency deltas, unused rules, exit codes) are never stored.

These structs mirror ``docs/report-format.md``. ``schemaVersion`` is a stored
constant ``1`` (this is the first published format — pre-alpha, so no migration
story). Unknown fields are tolerated on read (``forbid_unknown_fields`` is off),
so an additive field never breaks an older reader. ``kind``/``state``/``mode`` and
the verdicts are stored ``Literal`` fields, not msgspec tags.

Every value here is already redacted by the builder — url, headers, query, body,
cookies, auth, JSON paths, names, labels, and error messages. ``auth.value`` is
always the mask glyph. The never-leak invariant holds unconditionally over this
surface.
"""

from typing import Any
from typing import Literal

import msgspec

#: The one format version. Bumped only on a breaking change (a field removed or
#: renamed, or a type changed); additive fields and new ``kind`` values do not.
SCHEMA_VERSION = 1

Kind = Literal["run", "diff", "execution"]
Gate = Literal["PASS", "FAIL", "ERROR"]
#: One cell vocabulary for every kind: a diff cell is ``fail`` when it drifted
#: and ``pass`` when clean ("clean" is display copy, not a verdict). ``not_run``
#: is reserved for the roster — builders record deselected cells in ``notRun``,
#: never inside ``cells``; a reader may still meet the value defensively.
CellVerdict = Literal["pass", "fail", "error", "not_run"]
#: The diff *dimension*'s verdict on one cell — field-level vocabulary survives
#: here and in tallies, never at cell level.
DiffVerdict = Literal["same", "drift", "error"]
#: How one rule fared — the shared five-state vocabulary (`core.outcomes`).
CheckOutcome = Literal["held", "broke", "silenced", "absent", "error"]
#: Where a rule came from.
RuleOrigin = Literal["profile", "inline", "default", "synthetic"]


class EnvRef(msgspec.Struct, rename="camel"):
    """A reference to one environment the report ran against."""

    name: str
    base_url: str  # redacted
    id: str | None = None


class Environments(msgspec.Struct, rename="camel"):
    """The environment pair — ``candidate`` is ``None`` for a ``run``."""

    baseline: EnvRef
    candidate: EnvRef | None = None


class Selection(msgspec.Struct, rename="camel"):
    """Which requests ran — the tag/request filters, for reproducibility."""

    tags: list[str] | None = None
    requests: list[str] | None = None


class Invocation(msgspec.Struct, rename="camel"):
    """Everything needed to reproduce the report."""

    command: str  # the equivalent headless command (redacted)
    environments: Environments
    concurrency: int
    selection: Selection | None = None
    profile: str | None = None  # the ExecutionProfile id, for kind = execution


class RecordMeta(msgspec.Struct, rename="camel"):
    """Who/when/what produced this report."""

    id: str  # short unique id; the filename stem
    created: str  # ISO 8601 UTC
    tool: str  # "comparo <version>"
    project: str | None = None  # project name (redacted)
    title: str | None = None  # optional human label (e.g. an execution profile's name)


class FieldTally(msgspec.Struct, rename="camel"):
    """Field-path comparison counts across all cells — ``diff``/``execution``."""

    same: int = 0
    drift: int = 0
    skipped: int = 0


class CellTally(msgspec.Struct, rename="camel"):
    """Cell-verdict counts — one unit, never mixed with field counts.

    ``advisory`` counts passed cells with at least one broken warn rule.
    ``notRun`` counts the roster — so these counts span ``cells`` + ``notRun``
    and may sum past ``summary.cells`` (which counts executed cells only).
    """

    passed: int = 0
    failed: int = 0
    errors: int = 0
    not_run: int = 0
    advisory: int = 0


class AssertTally(msgspec.Struct, rename="camel"):
    """Assertion counts — present for ``run``/``execution``.

    ``unjudged`` counts rows evaluated against a response-less side: those rules
    never ran (they are the cell's error, not broken rules) and belong to
    neither ``passed`` nor ``failed``.
    """

    passed: int = 0
    failed: int = 0
    warned: int = 0
    not_asserted: int = 0
    unjudged: int = 0


class Summary(msgspec.Struct, rename="camel"):
    """The precomputed verdict and tallies, so a reader never recomputes from cells."""

    gate: Gate
    calls: int
    cells: int
    fields: FieldTally | None = None  # diff/execution
    cell_verdicts: CellTally | None = None
    assertions: AssertTally | None = None  # run/execution


class RuleTally(msgspec.Struct, rename="camel"):
    """Per-rule outcome counts, in cells, across the whole record.

    A rule with every count zero matched nothing anywhere — "unused" is derived,
    never stored.
    """

    broke: int = 0
    held: int = 0
    silenced: int = 0
    absent: int = 0
    error: int = 0
    warn_broke: int = 0
    warn_held: int = 0


class DiffRuleRecord(msgspec.Struct, rename="camel"):
    """One effective diff rule — inventory entry cells reference by ``id``."""

    id: str  # stable within this record (e.g. "d0")
    path: str  # declared path (redacted): $.…, $status, $headers.…, or a root for a catch-all
    mode: str  # exact | ignore | shape | type | tolerance
    origin: RuleOrigin
    profile: str | None = None  # owning DiffProfile id (redacted); origin == "profile"
    array_length: Literal["exact", "tolerant"] | None = None
    tolerance: float | None = None
    outcomes: RuleTally = msgspec.field(default_factory=RuleTally)


class AssertRuleRecord(msgspec.Struct, rename="camel"):
    """One effective assertion rule — inventory entry cells reference by ``id``."""

    id: str  # stable within this record (e.g. "a0")
    target: str  # redacted
    op: str
    severity: Literal["error", "warn"]
    label: str  # redacted human form ("status == 200")
    origin: RuleOrigin  # profile | inline
    profile: str | None = None  # owning AssertionProfile id (redacted)
    request: str | None = None  # owning request id for inline sugar/specs (redacted)
    expected: Any = None  # the declared expectation (redacted)
    outcomes: RuleTally = msgspec.field(default_factory=RuleTally)


class Rules(msgspec.Struct, rename="camel"):
    """The record-level rule inventories — stored once, referenced by id."""

    diff: list[DiffRuleRecord] = []
    assertions: list[AssertRuleRecord] = []


class AuthRecord(msgspec.Struct, rename="camel"):
    """The request's Basic/Bearer auth — the value is *always* the mask glyph."""

    scheme: Literal["basic", "bearer"]
    value: str  # always "••••••"


class TrailRecord(msgspec.Struct, rename="camel"):
    """Where one resolved request value came from — the provenance annotation."""

    path: str  # e.g. "headers.authorization", "query.plan", "endpoint" (redacted)
    origin: str  # variable | secret | instance | matrix | file (lowercase, as serialized)
    detail: str  # e.g. "env staging · vars.tenant", "matrix plan" (redacted)


class OutboundRequest(msgspec.Struct, rename="camel"):
    """What was sent — the resolved outbound request, masked; the replay fidelity."""

    method: str
    url: str  # fully resolved absolute URL (redacted)
    headers: list[tuple[str, str]] = []  # ordered, duplicates preserved, redacted
    query: dict[str, Any] = {}  # resolved query params (redacted)
    body: Any = None  # resolved request body (redacted), or None
    body_type: Literal["json", "form", "raw"] | None = None
    auth: AuthRecord | None = None
    cookies: dict[str, Any] = {}  # name -> value cookies sent (redacted)
    streaming: bool = False
    trail: list[TrailRecord] = []  # provenance of every injected value


class ResponseRecord(msgspec.Struct, rename="camel"):
    """What came back — status, wire metadata, and exactly one body representation.

    The body is parsed ``body`` (JSON) ⊕ ``events`` (a stream) ⊕ ``bodyText``
    (non-JSON text, redacted then truncated) ⊕ a binary digest (``sha256`` +
    ``bodyHead``, when the body is not text). ``bodyHead`` is hex of at most the
    first KiB and is dropped entirely if the redactor would touch its text view —
    fail closed, never leak through hex.
    """

    status: int
    headers: list[tuple[str, str]] = []  # ordered response headers (redacted)
    latency_ms: float = 0.0
    size_bytes: int = 0  # materialized-body length
    http_version: str = ""  # e.g. "HTTP/1.1" — the raw facet's status line
    reason_phrase: str = ""
    body: Any = None  # parsed, redacted body (JSON) — None for non-JSON
    events: list[Any] | None = None  # ordered parsed records, for a stream
    body_text: str | None = None  # redacted raw text for a non-JSON text body
    body_truncated: bool = False  # bodyText was cut after redaction (sizeBytes is true size)
    sha256: str | None = None  # hex digest of the raw body — binary bodies only
    body_head: str | None = None  # hex of the first bytes, for the hex/magic view


class AssertionRecord(msgspec.Struct, rename="camel"):
    """One assertion evaluated against one side's response (run/execution)."""

    target: str  # status, latency, schema, a header, or a $.path (redacted)
    op: str
    ok: bool
    severity: Literal["error", "warn"]
    label: str = ""  # redacted human form — replay shows what the screen showed
    rule_id: str | None = None  # into rules.assertions
    outcome: CheckOutcome | None = None  # "error" = never judged (dead side)
    expected: Any = None  # expected value (redacted)
    actual: Any = None  # observed value (redacted)
    detail: str | None = None  # human message (redacted)


class FieldDiffRecord(msgspec.Struct, rename="camel"):
    """One compared field — drift, deliberate skip, or a path-only ``same`` entry.

    ``same`` entries carry no values (recovered by path lookup into the stored
    side); they exist so rule outcomes and field indexes replay without
    re-diffing redacted bodies.
    """

    path: str  # JSON path (redacted)
    state: Literal["same", "drift", "skip"]
    mode: Literal["exact", "ignore", "shape", "type", "tolerance"]
    baseline: Any = None  # baseline value (redacted) — present for drift
    candidate: Any = None  # candidate value (redacted) — present for drift
    rule_id: str | None = None  # into rules.diff


class OutboundDiffRecord(msgspec.Struct, rename="camel"):
    """One differing outbound field — redacted before → after, with its source."""

    label: str
    baseline: str
    candidate: str
    source: str  # the config surface it came from ("env · header", …)


class RequestComparison(msgspec.Struct, rename="camel"):
    """Did we send the same request to both sides — the outbound layer, stored."""

    verdict: Literal["same", "drift"]
    fields: list[OutboundDiffRecord] = []


class Comparison(msgspec.Struct, rename="camel"):
    """The diff of the two sides — present for ``diff``/``execution``."""

    verdict: DiffVerdict
    same: int = 0
    drift: int = 0
    skipped: int = 0
    fields: list[FieldDiffRecord] = []
    profiles: list[str] = []  # composed DiffProfile ids, composition order (redacted)
    default_mode: str | None = None


class Side(msgspec.Struct, rename="camel"):
    """One environment's half of a cell — exactly what was sent and what came back."""

    request: OutboundRequest
    response: ResponseRecord | None = None  # None if the call errored before a response
    assertions: list[AssertionRecord] | None = None  # checks vs this response
    error: str | None = None  # transport/resolution error (redacted), else None
    attempts: int = 1  # transport attempts made (1 = no retry fired)
    retry_policy: str | None = None  # e.g. "exponential x3"


class Sides(msgspec.Struct, rename="camel"):
    """A cell's two sides — ``candidate`` is ``None`` for a ``run``."""

    baseline: Side
    candidate: Side | None = None


class Cell(msgspec.Struct, rename="camel"):
    """One executed ``(request, matrix-variant)`` unit."""

    request_id: str  # the request's metadata.id (redacted)
    name: str  # display name (redacted)
    variant: str  # the matrix cell key (redacted); "" when there is no matrix
    verdict: CellVerdict
    sides: Sides
    advisory: bool = False  # passed, but at least one warn rule broke
    error: str | None = None  # cell-level error (redacted): pairing, empty matrix, …
    comparison: Comparison | None = None  # present for diff/execution
    request_comparison: RequestComparison | None = None  # the outbound layer


class NotRunCell(msgspec.Struct, rename="camel"):
    """A cell deselected at prepare — recorded so the ⊘ roster replays."""

    request_id: str  # redacted
    name: str  # redacted
    variant: str = ""


class ReportRecord(msgspec.Struct, rename="camel", kw_only=True):
    """The whole report — one ``kind``-discriminated record for run/diff/execution."""

    schema_version: int = SCHEMA_VERSION
    kind: Kind
    metadata: RecordMeta
    invocation: Invocation
    summary: Summary
    rules: Rules | None = None
    cells: list[Cell] = []
    not_run: list[NotRunCell] = []
