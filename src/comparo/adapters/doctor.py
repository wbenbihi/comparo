"""A runtime redaction self-check: run a canary secret through every leak sink.

The guarantee comparo makes is that a declared secret value never appears in any
string that leaves the process. This module proves it at runtime: it writes a
minimal canary project, declares a distinctive secret, and pushes that secret
through the real code of every redaction sink — the display renderer, the runs
and reports on disk, the four CI reporters, the yanked curl command, and the
crash-report scrubber. Each sink is fed output that *would* carry the canary if
unmasked; the check passes only when the canary is absent from what the sink
produced.

It powers the Settings "never-leak" self-check and the ``comparo doctor`` CLI.
Every check is defensive: a sink that raises is reported as a failure, so
:func:`run_selfcheck` never propagates an exception.
"""

import dataclasses
import json
import tempfile
from collections.abc import Callable
from pathlib import Path

import msgspec

from comparo.adapters.reporters import JsonReporter
from comparo.adapters.reporters import JUnitReporter
from comparo.adapters.reporters import MarkdownReporter
from comparo.adapters.reporters import SarifReporter
from comparo.core.archive import record_from_diff
from comparo.core.archive import save_record
from comparo.core.assertions import AssertionResult
from comparo.core.checks import Check
from comparo.core.compare import CellDiff
from comparo.core.curl import to_curl
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.export import RunEntry
from comparo.core.export import export_run
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.matrix import MatrixCell
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.redaction import Redact
from comparo.core.redaction import Redactor
from comparo.core.report_builder import record_from_diff as _v1_from_diff
from comparo.core.report_builder import record_from_run as _v1_from_run
from comparo.core.report_record import ReportRecord
from comparo.core.report_record import Selection
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink

#: The marker every canary value embeds. A leak in ANY form — the raw value, its
#: JSON-escaped form, or the surviving tail of an overlap — still exposes this
#: substring, so a single ``_MARKER not in output`` check catches every class.
_MARKER = "CANARY"

#: The base canary secret every sink is challenged with — distinctive, so an
#: accidental appearance in any output is unmistakable and never a coincidence.
CANARY = "s3cr3t-CANARY-a1b2c3d4e5f6"
#: A secret carrying JSON-special chars (``"``, ``\\``, newline): it must survive
#: the ``json.dumps`` a detail/body passes through before a sink redacts it.
CANARY_SPECIAL = 's3cr3t-CANARY-special-"\\-\n-end'
#: Two overlapping secrets — the shorter is a prefix of the longer, whose tail
#: also embeds the marker, so a non-longest-first redactor leaks ``CANARY``.
CANARY_OVERLAP_SHORT = "s3cr3t-CANARY-overlap"
CANARY_OVERLAP_LONG = "s3cr3t-CANARY-overlap-then-CANARY-tail"
#: A credential the SERVER issues (never declared) — only the header-name policy
#: masks it, so a missing policy leaks it into saved runs and reports.
CANARY_COOKIE = "session=srv-CANARY-issued-token"

_PROJECT_YAML = """\
apiVersion: comparo/v1
kind: Project
metadata:
  name: comparo-doctor-canary
spec:
  data: .
"""

#: Declared secrets, keyed by a name that never embeds the marker (so the marker
#: only ever appears as a secret *value* to be masked). ``$literal`` values are
#: emitted via ``json.dumps`` so special characters survive the YAML round-trip.
_SECRETS = {
    "DOCTOR_TOKEN": CANARY,
    "DOCTOR_SPECIAL": CANARY_SPECIAL,
    "DOCTOR_OVERLAP_A": CANARY_OVERLAP_SHORT,
    "DOCTOR_OVERLAP_B": CANARY_OVERLAP_LONG,
}

_ENVIRONMENT_YAML = (
    "apiVersion: comparo/v1\n"
    "kind: Environment\n"
    "metadata:\n"
    "  name: Canary\n"
    "  id: environment.canary\n"
    "spec:\n"
    "  baseUrl: https://canary.invalid\n"
    "  secrets:\n"
    + "".join(
        f"    {name}:\n      from:\n        - $literal: {json.dumps(value)}\n"
        for name, value in _SECRETS.items()
    )
)

_REQUEST_YAML = """\
apiVersion: comparo/v1
kind: Request
metadata:
  name: Canary probe
  id: request.canary
spec:
  request:
    method: GET
    endpoint: /probe
    headers:
      - key: authorization
        value:
          $secret: DOCTOR_TOKEN
"""


@dataclasses.dataclass(frozen=True, slots=True)
class SinkCheck:
    """One sink's verdict: its name, a short where/how note, and pass/fail."""

    name: str
    detail: str
    ok: bool


@dataclasses.dataclass(frozen=True, slots=True)
class _Scenario:
    """The loaded canary project plus the objects every sink producer needs."""

    directory: Path
    project: LoadedProject
    environment: Environment
    request: Request
    redact: Redact


def _build_scenario(directory: Path) -> _Scenario:
    """Write a minimal canary project into *directory*, load it, and index it."""
    (directory / "comparo.yaml").write_text(_PROJECT_YAML, encoding="utf-8")
    (directory / "environment.yaml").write_text(_ENVIRONMENT_YAML, encoding="utf-8")
    (directory / "request.yaml").write_text(_REQUEST_YAML, encoding="utf-8")
    project = load_project(directory)
    return _Scenario(
        directory=directory,
        project=project,
        environment=_first_environment(project),
        request=_canary_request(project),
        redact=Redactor.for_project(project).text,
    )


def _first_environment(project: LoadedProject) -> Environment:
    """Return the canary project's one environment, whose secret is the canary."""
    for obj in project.objects.values():
        if isinstance(obj, Environment):
            return obj
    message = "canary project declares no environment"
    raise RuntimeError(message)


def _canary_request(project: LoadedProject) -> Request:
    """Return the canary probe request, whose header references the secret."""
    request = project.objects.get("request.canary")
    if not isinstance(request, Request):
        message = "canary project declares no request.canary"
        raise RuntimeError(message)
    return request


def _tainted_cell(request: Request) -> CellDiff:
    """A cell whose response echoes every canary where a sink might persist it.

    Each canary appears as a body value AND a JSON key, as a drift and a skip
    field path, in a response-header name and value, and in the matrix case key —
    every place a server could reflect one back into what a report or archive
    writes. The drift detail is built the way the real engine builds it (a
    ``json.dumps``-ed value), so the JSON-escaped-secret leak path is exercised.
    """
    base = {CANARY: CANARY, "special": CANARY_SPECIAL, "overlap": CANARY_OVERLAP_LONG}
    cand = {CANARY: "other", "special": "other", "overlap": "other"}
    fields = [
        # detail rendered as the diff engine renders it — json.dumps BEFORE redaction
        FieldDiff(f"$.{CANARY}", State.DRIFT, "exact", f"{json.dumps(CANARY)} → {json.dumps('x')}"),
        FieldDiff("$.special", State.DRIFT, "exact", f'{json.dumps(CANARY_SPECIAL)} → "x"'),
        FieldDiff("$.overlap", State.DRIFT, "exact", f'{json.dumps(CANARY_OVERLAP_LONG)} → "x"'),
        FieldDiff(f"$.headers.{CANARY}", State.SKIP, "ignore", "volatile"),
    ]
    return CellDiff(
        request,
        f"token={CANARY}",
        fields,
        None,
        base,
        cand,
        status=200,
        latency_ms=42,
        size_bytes=128,
        response_headers=(
            (CANARY, CANARY),
            ("set-cookie", CANARY_COOKIE),
            ("content-type", "application/json"),
        ),
    )


def _display(scenario: _Scenario) -> str:
    """The TUI display sink masks a declared secret before it is drawn on screen."""
    return scenario.redact(f"authorization: Basic {CANARY} (as rendered on screen)")


def _saved_runs(scenario: _Scenario) -> str:
    """The runs/*.json export masks a secret echoed as a body value, key, or header.

    Includes the overlapping-secret pair (a non-longest-first redactor would leak
    the longer secret's tail) and an undeclared ``Set-Cookie`` (only the header
    policy masks it).
    """
    body = json.dumps(
        {
            "echo": CANARY,
            CANARY: "as-a-key",
            "special": CANARY_SPECIAL,
            "overlap": CANARY_OVERLAP_LONG,
        }
    ).encode()
    response = HttpResponse(
        200,
        [("x-echo", CANARY), (CANARY, "reflected"), ("set-cookie", CANARY_COOKIE)],
        body,
        5.0,
    )
    execution = Execution(
        request=scenario.request, environment=scenario.environment, cell_key="", response=response
    )
    entry = RunEntry(
        scenario.request,
        MatrixCell("", ()),
        execution,
        [Check("auth", ok=False, detail=f"server returned {CANARY}")],
    )
    return export_run(scenario.project, scenario.environment, [entry])


def _saved_reports(scenario: _Scenario) -> str:
    """The saved-report archive masks a secret before writing .reports/<id>.json."""
    record = record_from_diff(
        f"Stable {CANARY}",
        f"Canary {CANARY}",
        [_tainted_cell(scenario.request)],
        run_id="doctor",
        created="1970-01-01T00:00:00Z",
        redact=scenario.redact,
    )
    path = save_record(scenario.directory / "reports", record)
    return path.read_text(encoding="utf-8")


def _tainted_resolved() -> ResolvedRequest:
    """A resolved outbound request echoing every canary where a report might persist it.

    Each canary rides a channel the v1 record serializes: the url, a query param,
    a header value (declared and credential-bearing), the body (value and key),
    the request cookies, and the auth block (whose value must ALWAYS mask). This is
    the outbound surface the older ``.reports`` record never captured.
    """
    return ResolvedRequest(
        method="POST",
        url=f"https://api.invalid/checkout?token={CANARY}&overlap={CANARY_OVERLAP_LONG}",
        headers=[
            ("authorization", f"Bearer {CANARY}"),  # credential header — masked by name
            ("x-api-key", CANARY),  # credential header — masked by name
            ("cookie", CANARY_COOKIE),  # undeclared token — masked by header name
            (CANARY, CANARY),  # declared secret as a header name AND value
        ],
        query={"token": CANARY, CANARY: CANARY_SPECIAL},
        body={
            CANARY: "as-a-key",
            "cart": CANARY,
            "special": CANARY_SPECIAL,
            "overlap": CANARY_OVERLAP_LONG,
        },
        trail=[],
        body_type="json",
        auth={"bearer": CANARY},  # the value must never survive — auth is always masked
        cookies={"session": CANARY, CANARY: CANARY_SPECIAL},  # declared secrets
        streaming=False,
    )


def _tainted_response(*, events: bool) -> HttpResponse:
    """A response that reflects the canary into its body or event stream and headers."""
    body = json.dumps(
        {
            "echo": CANARY,
            CANARY: "as-a-key",
            "special": CANARY_SPECIAL,
            "overlap": CANARY_OVERLAP_LONG,
        }
    ).encode()
    stream: list[object] | None = (
        [{"data": CANARY, CANARY: CANARY_SPECIAL}, {"data": CANARY_OVERLAP_LONG}]
        if events
        else None
    )
    return HttpResponse(
        200,
        [("x-echo", CANARY), (CANARY, "reflected"), ("set-cookie", CANARY_COOKIE)],
        b"" if events else body,
        5.0,
        events=stream,
    )


def _tainted_execution(scenario: _Scenario, *, events: bool) -> Execution:
    return Execution(
        scenario.request,
        scenario.environment,
        f"token={CANARY}",
        _tainted_response(events=events),
        resolved=_tainted_resolved(),
    )


def _tainted_cell_v1(scenario: _Scenario, baseline: Execution, candidate: Execution) -> CellDiff:
    """A diff cell whose fields carry the canary in every serialized channel."""
    fields = [
        FieldDiff(
            f"$.{CANARY}",
            State.DRIFT,
            "exact",
            f"{json.dumps(CANARY)} → x",
            baseline=CANARY,
            candidate=CANARY_SPECIAL,
            rule=f"$.{CANARY}",
        ),
        FieldDiff(
            "$.special",
            State.DRIFT,
            "exact",
            "",
            baseline=CANARY_SPECIAL,
            candidate=CANARY_OVERLAP_LONG,
        ),
        FieldDiff(
            f"$.headers.{CANARY}", State.SKIP, "ignore", "volatile", rule=f"$.headers.{CANARY}"
        ),
    ]
    return CellDiff(
        scenario.request,
        f"token={CANARY}",
        fields,
        None,
        status=200,
        latency_ms=42,
        size_bytes=128,
        baseline=baseline,
        candidate=candidate,
    )


def _tainted_assertions() -> list[AssertionResult]:
    """Assertions carrying the canary in target/expected/actual/detail."""
    return [
        AssertionResult(
            f"body:$.{CANARY}",
            "equals",
            False,
            "error",
            f"got {json.dumps(CANARY)}",
            "label",
            expected=CANARY_OVERLAP_LONG,
            actual=CANARY,
        ),
        AssertionResult(
            "status",
            "oneOf",
            True,
            "warn",
            "ok",
            "label",
            expected=[CANARY_SPECIAL, CANARY],
            actual=CANARY,
        ),
    ]


def _tainted_diff_record(scenario: _Scenario) -> ReportRecord:
    """A two-sided ``diff`` record echoing the canary across every serialized channel."""
    baseline = _tainted_execution(scenario, events=False)  # exercises the JSON body channel
    candidate = _tainted_execution(scenario, events=True)  # exercises the events channel
    cell = _tainted_cell_v1(scenario, baseline, candidate)
    return _v1_from_diff(
        scenario.environment,
        scenario.environment,
        [cell],
        record_id="doctor",
        created="1970-01-01T00:00:00Z",
        tool="comparo 0.0.0",
        project=f"proj-{CANARY}",
        concurrency=4,
        redact=scenario.redact,
        selection=Selection(tags=[f"tag-{CANARY}"], requests=[f"req-{CANARY}"]),
    )


def _saved_reports_v1(scenario: _Scenario) -> str:
    """The v1 report record masks every secret across the whole request+response surface.

    Serializes a two-sided ``diff`` record (outbound request, response body,
    streamed events, and the structured field diff) and a ``run`` record (per-side
    assertions) — the never-leak gate for the new format.
    """
    baseline = _tainted_execution(scenario, events=False)
    run_record = _v1_from_run(
        scenario.environment,
        [(baseline, _tainted_assertions())],
        record_id="doctor",
        created="1970-01-01T00:00:00Z",
        tool="comparo 0.0.0",
        project=f"proj-{CANARY}",
        concurrency=4,
        redact=scenario.redact,
        selection=Selection(tags=[f"tag-{CANARY}"], requests=[f"req-{CANARY}"]),
    )
    diff_json = msgspec.json.encode(_tainted_diff_record(scenario)).decode()
    return diff_json + "\n" + msgspec.json.encode(run_record).decode()


def _report(scenario: _Scenario) -> ReportRecord:
    """The masked report record the CI reporters project from."""
    return _tainted_diff_record(scenario)


def _junit(scenario: _Scenario) -> str:
    """The JUnit reporter renders the masked report to reports/junit.xml."""
    return JUnitReporter().render(_report(scenario))


def _sarif(scenario: _Scenario) -> str:
    """The SARIF reporter renders the masked report to reports/comparo.sarif."""
    return SarifReporter().render(_report(scenario))


def _json_report(scenario: _Scenario) -> str:
    """The JSON reporter renders the masked report to reports/comparo.json."""
    return JsonReporter().render(_report(scenario))


def _markdown(scenario: _Scenario) -> str:
    """The Markdown reporter renders the masked report for a GitHub step summary."""
    return MarkdownReporter().render(_report(scenario))


def _curl(scenario: _Scenario) -> str:
    """The yanked curl command resolves via the DISPLAY sink, masking the secret."""
    resolved = Resolver(scenario.project, scenario.environment, Sink.DISPLAY).resolve_request(
        scenario.request
    )
    return to_curl(resolved)


def _crash(scenario: _Scenario) -> str:
    """A crash report scrubs any secret a captured traceback frame may carry."""
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "comparo/core/execute.py", line 66, in execute_request\n'
        f"    raise HttpError('sending authorization=Basic {CANARY}')\n"
        f"comparo.core.http.HttpError: sending authorization=Basic {CANARY}"
    )
    return scenario.redact(traceback)


#: The nine sinks in the stable order the UI mockup pins, each paired with its
#: display detail and the producer that runs its real code.
_SINKS: tuple[tuple[str, str, Callable[[_Scenario], str]], ...] = (
    ("TUI display", "masked on render", _display),
    ("saved runs", ".runs/*.json", _saved_runs),
    ("saved reports", ".reports/*.json", _saved_reports),
    ("saved reports v1", "report record", _saved_reports_v1),
    ("JUnit reporter", "reports/junit.xml", _junit),
    ("SARIF reporter", "reports/comparo.sarif", _sarif),
    ("JSON reporter", "reports/comparo.json", _json_report),
    ("Markdown reporter", "GitHub step summary", _markdown),
    ("curl copy", "yanked command", _curl),
    ("crash report", "traceback scrub", _crash),
)

#: The ``(name, detail)`` of every sink, in order — the display projection of
#: :data:`_SINKS`, so the TUI's Security panel never re-lists them by hand.
SINK_LABELS: tuple[tuple[str, str], ...] = tuple((name, detail) for name, detail, _ in _SINKS)


def _run_sink(
    name: str, detail: str, produce: Callable[[_Scenario], str], scenario: _Scenario
) -> SinkCheck:
    """Run one sink's producer; the check passes iff the canary is absent from it."""
    try:
        output = produce(scenario)
    except Exception as error:  # defence in depth — a broken sink is a failed check
        return SinkCheck(name, f"{detail} — {type(error).__name__}: {error}", ok=False)
    # Every canary embeds _MARKER, so any leak — raw, JSON-escaped, or overlap tail
    # — surfaces it. _MARKER never appears in legitimate output (secret names and
    # object names deliberately avoid it), so its presence is unambiguously a leak.
    return SinkCheck(name, detail, ok=_MARKER not in output)


def run_selfcheck() -> list[SinkCheck]:
    """Run a canary secret through every sink; one :class:`SinkCheck` per sink.

    Returns:
        One check per redaction sink, in a stable order, each reporting whether
        the canary was masked in that sink's real output. The function never
        raises: a sink (or the scenario build) that fails is reported ``ok=False``
        with the error noted in its detail.
    """
    try:
        with tempfile.TemporaryDirectory() as directory:
            scenario = _build_scenario(Path(directory))
            return [_run_sink(name, detail, produce, scenario) for name, detail, produce in _SINKS]
    except Exception as error:  # scenario build failed — report every sink as failing
        reason = f"{type(error).__name__}: {error}"
        return [SinkCheck(name, f"{detail} — {reason}", ok=False) for name, detail, _ in _SINKS]
