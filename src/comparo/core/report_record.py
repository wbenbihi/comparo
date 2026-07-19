"""The versioned report record — the single artifact comparo writes per invocation.

One shape covers all three kinds. A ``run`` has one side; a ``diff`` and an
``execution`` have two (``baseline`` + ``candidate``). Everything else follows
from ``kind``. It captures the *whole* interaction — the resolved outbound
request **and** the response, per side — so a saved report replays in full detail
offline, feeds the TUI's Report tab, and is the source the CI reporters project.

These structs mirror ``docs/report-format.md``. ``schemaVersion`` is a stored
constant ``1`` (this is the first published format — pre-alpha, so no migration
story). Unknown fields are tolerated on read (``forbid_unknown_fields`` is off),
so an additive field never breaks an older reader. ``kind``/``state``/``mode`` and
the verdicts are stored ``Literal`` fields, not msgspec tags.

Every value here is already redacted by the builder — url, headers, query, body,
cookies, auth, JSON paths, names, and error messages. ``auth.value`` is always the
mask glyph. The never-leak invariant holds unconditionally over this surface.
"""

from typing import Any
from typing import Literal

import msgspec

#: The one format version. Bumped only on a breaking change (a field removed or
#: renamed, or a type changed); additive fields and new ``kind`` values do not.
SCHEMA_VERSION = 1

Kind = Literal["run", "diff", "execution"]
Gate = Literal["PASS", "FAIL", "ERROR"]
#: A cell verdict spans both domains: diff-side (same/drift) and assert-side (pass/fail).
CellVerdict = Literal["same", "drift", "error", "pass", "fail"]
DiffVerdict = Literal["same", "drift", "error"]


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


class DiffTally(msgspec.Struct, rename="camel"):
    """Field/cell drift counts — present for ``diff``/``execution``."""

    same: int = 0
    drift: int = 0
    error: int = 0
    skipped: int = 0


class AssertTally(msgspec.Struct, rename="camel"):
    """Assertion counts — present for ``run``/``execution``."""

    passed: int = 0
    failed: int = 0
    warned: int = 0
    not_asserted: int = 0


class Summary(msgspec.Struct, rename="camel"):
    """The precomputed verdict and tallies, so a reader never recomputes from cells."""

    gate: Gate
    calls: int
    cells: int
    diff: DiffTally | None = None
    assertions: AssertTally | None = None


class AuthRecord(msgspec.Struct, rename="camel"):
    """The request's Basic/Bearer auth — the value is *always* the mask glyph."""

    scheme: Literal["basic", "bearer"]
    value: str  # always "••••••"


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


class ResponseRecord(msgspec.Struct, rename="camel"):
    """What came back — status, headers, timing, and the parsed, redacted body."""

    status: int
    headers: list[tuple[str, str]] = []  # ordered response headers (redacted)
    latency_ms: float = 0.0
    size_bytes: int = 0  # materialized-body length
    body: Any = None  # parsed, redacted body (JSON) — None for non-JSON
    events: list[Any] | None = None  # ordered parsed records, for a stream
    body_text: str | None = None  # optional raw redacted text for a non-JSON body


class AssertionRecord(msgspec.Struct, rename="camel"):
    """One assertion evaluated against one side's response (run/execution)."""

    target: str  # status, latency, schema, a header, or a $.path (redacted)
    op: str
    ok: bool
    severity: Literal["error", "warn"]
    expected: Any = None  # expected value (redacted)
    actual: Any = None  # observed value (redacted)
    detail: str | None = None  # human message (redacted)


class FieldDiffRecord(msgspec.Struct, rename="camel"):
    """One non-same field of a comparison — a drift or a deliberately skipped path."""

    path: str  # JSON path (redacted)
    state: Literal["drift", "skip"]
    mode: Literal["exact", "ignore", "shape", "type", "tolerance"]
    baseline: Any = None  # baseline value (redacted) — present for drift
    candidate: Any = None  # candidate value (redacted) — present for drift
    rule: str | None = None  # the DiffProfile rule path that governed this


class Comparison(msgspec.Struct, rename="camel"):
    """The diff of the two sides — present for ``diff``/``execution``.

    ``fields`` holds only the **non-same** paths (drift + skip); same-valued fields
    are omitted (derivable from the two ``response.body`` blobs) to stay compact.
    """

    verdict: DiffVerdict
    same: int = 0
    drift: int = 0
    skipped: int = 0
    fields: list[FieldDiffRecord] = []


class Side(msgspec.Struct, rename="camel"):
    """One environment's half of a cell — exactly what was sent and what came back."""

    request: OutboundRequest
    response: ResponseRecord | None = None  # None if the call errored before a response
    assertions: list[AssertionRecord] | None = None  # checks vs this response
    error: str | None = None  # transport/resolution error (redacted), else None


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
    comparison: Comparison | None = None  # present for diff/execution


class ReportRecord(msgspec.Struct, rename="camel", kw_only=True):
    """The whole report — one ``kind``-discriminated record for run/diff/execution."""

    schema_version: int = SCHEMA_VERSION
    kind: Kind
    metadata: RecordMeta
    invocation: Invocation
    summary: Summary
    cells: list[Cell] = []
