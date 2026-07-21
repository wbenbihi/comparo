"""The v1 report builder projects results faithfully and redacts every value."""

import json
from pathlib import Path

import msgspec

from comparo.core.assertions import AssertionResult
from comparo.core.compare import CellDiff
from comparo.core.compare import compare_cell
from comparo.core.execute import Execution
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.redaction import MASK
from comparo.core.redaction import Redact
from comparo.core.redaction import Redactor
from comparo.core.report_builder import record_from_diff
from comparo.core.report_builder import record_from_run
from comparo.core.report_record import ReportRecord
from comparo.core.report_record import Selection
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import select_environment
from comparo.core.schema import report_schema

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def _loaded() -> tuple[LoadedProject, Environment, Request]:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    return loaded, env, request


def _execution(request: Request, env: Environment, body: dict[str, object]) -> Execution:
    response = HttpResponse(
        200, [("content-type", "application/json")], json.dumps(body).encode(), 5.0
    )
    resolved = ResolvedRequest("GET", "http://localhost:8080/json", [], {}, None, [])
    return Execution(request, env, "", response, resolved=resolved)


def _diff(
    env: Environment,
    cells: list[CellDiff],
    *,
    redact: Redact = str,
    selection: Selection | None = None,
) -> ReportRecord:
    return record_from_diff(
        env,
        env,
        cells,
        record_id="abc",
        created="t",
        tool="comparo 0.1.0",
        project=None,
        concurrency=4,
        redact=redact,
        selection=selection,
    )


def _run(
    env: Environment,
    cells: list[tuple[Execution, list[AssertionResult]]],
    *,
    redact: Redact = str,
) -> ReportRecord:
    return record_from_run(
        env,
        cells,
        record_id="abc",
        created="t",
        tool="comparo 0.1.0",
        project=None,
        concurrency=4,
        redact=redact,
    )


def test_diff_record_is_two_sided_with_a_comparison() -> None:
    loaded, env, request = _loaded()
    base = _execution(request, env, {"total": 10})
    cand = _execution(request, env, {"total": 12})
    record = _diff(env, [compare_cell(loaded, base, cand)])

    assert record.kind == "diff"
    assert record.summary.fields is not None
    assert record.summary.assertions is None  # a diff carries no assertions
    (cell_record,) = record.cells
    assert cell_record.verdict == "fail"  # a drifted cell is a failed cell
    assert cell_record.sides.candidate is not None
    assert cell_record.comparison is not None
    assert cell_record.comparison.verdict == "drift"
    # the drifted $.total field is projected with its structured before/after
    field = next(f for f in cell_record.comparison.fields if f.path == "$.total")
    assert field.baseline == 10
    assert field.candidate == 12


def test_run_record_is_one_sided_with_assertions() -> None:
    _, env, request = _loaded()
    execution = _execution(request, env, {"ok": True})
    good = AssertionResult(
        "status", "equals", True, "error", "200 == 200", expected=200, actual=200
    )
    record = _run(env, [(execution, [good])])

    assert record.kind == "run"
    assert record.summary.assertions is not None
    assert record.summary.fields is None
    assert record.summary.gate == "PASS"
    (cell,) = record.cells
    assert cell.verdict == "pass"
    assert cell.sides.candidate is None
    assert cell.sides.baseline.assertions is not None
    assert cell.sides.baseline.assertions[0].expected == 200


def test_run_record_gate_fails_on_a_failed_assertion() -> None:
    _, env, request = _loaded()
    execution = _execution(request, env, {"ok": True})
    bad = AssertionResult(
        "status", "equals", False, "error", "500 != 200", expected=200, actual=500
    )
    record = _run(env, [(execution, [bad])])
    assert record.summary.gate == "FAIL"
    assert record.summary.assertions is not None
    assert record.summary.assertions.failed == 1
    assert record.cells[0].verdict == "fail"


def test_empty_diff_gate_fails_closed() -> None:
    # A zero-cell run must not read as a pass — fail closed (aligns with the run
    # min-length guard), not the permissive "empty selection -> PASS".
    _, env, _request = _loaded()
    record = _diff(env, [])
    assert record.summary.calls == 0
    assert record.summary.gate == "FAIL"
    assert record.cells == []


def test_run_gate_reads_error_when_errors_are_the_only_failure() -> None:
    # Every rule on a response-less cell auto-fails with "no response" — those
    # were never judged, so they must not drag an errors-only run to FAIL.
    _, env, request = _loaded()
    errored = Execution(request, env, "", None, "ConnectError: boom", resolved=None)
    never_judged = AssertionResult(
        "status", "equals", False, "error", "no response", expected=200, actual=None
    )
    record = _run(env, [(errored, [never_judged])])
    assert record.summary.gate == "ERROR"
    assert record.cells[0].verdict == "error"


def test_run_gate_fail_outranks_error() -> None:
    _, env, request = _loaded()
    judged = _execution(request, env, {"ok": True})
    broke = AssertionResult(
        "status", "equals", False, "error", "500 != 200", expected=200, actual=500
    )
    errored = Execution(request, env, "", None, "ConnectError: boom", resolved=None)
    record = _run(env, [(judged, [broke]), (errored, [])])
    assert record.summary.gate == "FAIL"  # the broken rule outranks the errored cell


def test_empty_run_gate_fails_closed() -> None:
    # A run that judged nothing must never read green — mirrors the diff gate.
    _, env, _request = _loaded()
    record = _run(env, [])
    assert record.summary.gate == "FAIL"
    assert record.cells == []


def test_selection_is_redacted() -> None:
    _, env, _request = _loaded()
    secret = "s3cr3t-tag"
    record = _diff(
        env, [], redact=Redactor.from_values({secret}).text, selection=Selection(tags=[secret])
    )
    assert record.invocation.selection is not None
    assert record.invocation.selection.tags == [MASK]


def test_a_secret_echoed_into_a_response_body_is_masked_in_the_record() -> None:
    # The builder is the never-leak floor: a secret the server echoed into a
    # drifting body must be masked everywhere it lands in the serialized record.
    loaded, env, request = _loaded()
    secret = "SUPERSECRETVALUE"
    base = _execution(request, env, {"echo": "clean"})
    cand = _execution(request, env, {"echo": secret})  # the candidate leaked it
    cell = compare_cell(loaded, base, cand)
    record = _diff(env, [cell], redact=Redactor.from_values({secret}).text)

    blob = msgspec.json.encode(record).decode()
    assert secret not in blob
    assert MASK in blob


def test_every_kind_round_trips_and_validates_against_the_schema() -> None:
    import jsonschema

    loaded, env, request = _loaded()
    base = _execution(request, env, {"total": 10})
    cand = _execution(request, env, {"total": 12})
    good = AssertionResult("status", "equals", True, "error", "ok", expected=200, actual=200)
    schema = report_schema()

    for record in (_diff(env, [compare_cell(loaded, base, cand)]), _run(env, [(base, [good])])):
        raw = msgspec.json.encode(record)
        assert msgspec.json.decode(raw, type=ReportRecord) == record  # round-trips
        jsonschema.validate(json.loads(raw), schema)  # conforms to the emitted schema


def test_non_json_bodies_serialize_as_redacted_text() -> None:
    loaded, env, request = _loaded()
    secret = "tok-SECRET-123"
    redact = Redactor.from_values({secret}).text
    base = Execution(request, env, "", HttpResponse(200, [], f"hello {secret} world".encode(), 5.0))
    cand = Execution(request, env, "", HttpResponse(200, [], b"hello there world", 5.0))
    record = _diff(env, [compare_cell(loaded, base, cand)], redact=redact)
    response = record.cells[0].sides.baseline.response
    assert response is not None
    assert response.body is None
    assert response.body_text is not None
    assert secret not in response.body_text  # redacted before it is stored
    assert MASK in response.body_text


def test_binary_bodies_store_a_digest_and_drop_a_tainted_head() -> None:
    import hashlib

    loaded, env, request = _loaded()
    secret = "tok-SECRET-123"
    redact = Redactor.from_values({secret}).text
    clean = b"\x00\x89PNG" + b"\xff" * 32
    tainted = b"\x00\x89PNG" + secret.encode() + b"\xff" * 32
    base = Execution(request, env, "", HttpResponse(200, [], clean, 5.0))
    cand = Execution(request, env, "", HttpResponse(200, [], tainted, 5.0))
    record = _diff(env, [compare_cell(loaded, base, cand)], redact=redact)
    baseline = record.cells[0].sides.baseline.response
    candidate = record.cells[0].sides.candidate.response  # type: ignore[union-attr]
    assert baseline is not None
    assert candidate is not None
    assert baseline.sha256 == hashlib.sha256(clean).hexdigest()
    assert baseline.body_head == clean.hex()  # clean head survives, hex for the byte view
    # A secret anywhere in the body drops BOTH the head (hex side channel) and
    # the digest (an offline verification oracle) — fail closed on the pair.
    assert candidate.sha256 is None
    assert candidate.body_head is None
    assert secret.encode().hex() not in msgspec.json.encode(record).decode()


def test_binary_fail_closed_sees_secrets_past_the_head_and_in_utf8() -> None:
    loaded, env, request = _loaded()
    secret = "tok-SECRET-abcdefghij-0123456789"
    utf8_secret = "pässwörd-sécret-123"
    redact = Redactor.from_values({secret, utf8_secret}).text
    # The secret straddles the 1 KiB head cut — the whole-body check still sees it.
    straddler = b"\x00" + b"A" * 1000 + secret.encode() + b"\xff" * 32
    # A valid-UTF-8 secret garbles under latin-1; the lossy-UTF-8 view catches it.
    encoded = b"\x00\x89PNG" + utf8_secret.encode() + b"\xff" * 32
    base = Execution(request, env, "", HttpResponse(200, [], straddler, 5.0))
    cand = Execution(request, env, "", HttpResponse(200, [], encoded, 5.0))
    record = _diff(env, [compare_cell(loaded, base, cand)], redact=redact)
    baseline = record.cells[0].sides.baseline.response
    candidate = record.cells[0].sides.candidate.response  # type: ignore[union-attr]
    assert baseline is not None
    assert candidate is not None
    assert baseline.body_head is None
    assert baseline.sha256 is None
    assert candidate.body_head is None
    assert candidate.sha256 is None
    blob = msgspec.json.encode(record).decode()
    assert secret.encode().hex() not in blob
    assert utf8_secret.encode().hex() not in blob


def test_the_rule_inventory_covers_every_effective_rule_with_tallies() -> None:
    loaded, env, request = _loaded()
    base = _execution(request, env, {"total": 10})
    cand = _execution(request, env, {"total": 12})
    record = _diff(env, [compare_cell(loaded, base, cand)])
    assert record.rules is not None
    inventory = record.rules.diff
    assert inventory, "the diff rule inventory is empty"
    by_path = {rule.path: rule for rule in inventory}
    assert by_path["$status"].outcomes.held == 1  # the synthetic check is accounted
    assert by_path["$"].origin == "default"  # the catch-all is a listed rule
    assert by_path["$"].outcomes.broke == 1  # it governed the drifting $.total
    # every field references an inventory id
    comparison = record.cells[0].comparison
    assert comparison is not None
    ids = {rule.id for rule in inventory}
    assert all(field.rule_id in ids for field in comparison.fields)
    # same fields are serialized path-only
    same_rows = [field for field in comparison.fields if field.state == "same"]
    assert same_rows
    assert all(row.baseline is None and row.candidate is None for row in same_rows)


def test_the_outbound_layer_and_trail_serialize_per_cell() -> None:
    from comparo.core.provenance import Origin
    from comparo.core.provenance import Trail

    loaded, env, request = _loaded()
    base = Execution(
        request,
        env,
        "",
        HttpResponse(200, [], b"{}", 5.0),
        resolved=ResolvedRequest(
            "GET",
            "http://a/x",
            [],
            {"plan": "basic"},
            None,
            [Trail("query.plan", Origin.MATRIX, "plan=basic")],
        ),
    )
    cand = Execution(
        request,
        env,
        "",
        HttpResponse(200, [], b"{}", 5.0),
        resolved=ResolvedRequest("GET", "http://b/x", [], {"plan": "pro"}, None, []),
    )
    record = _diff(env, [compare_cell(loaded, base, cand)])
    cell = record.cells[0]
    assert cell.request_comparison is not None
    assert cell.request_comparison.verdict == "drift"
    labels = {entry.label for entry in cell.request_comparison.fields}
    assert "url" in labels
    assert "query plan" in labels
    trail = cell.sides.baseline.request.trail
    assert trail
    assert trail[0].origin == "matrix"
    assert trail[0].detail == "plan=basic"
