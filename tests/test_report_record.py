"""The versioned report record round-trips and tolerates forward-additive fields."""

import json

import msgspec

from comparo.core.report_record import SCHEMA_VERSION
from comparo.core.report_record import AssertionRecord
from comparo.core.report_record import Cell
from comparo.core.report_record import Comparison
from comparo.core.report_record import Environments
from comparo.core.report_record import EnvRef
from comparo.core.report_record import FieldDiffRecord
from comparo.core.report_record import Invocation
from comparo.core.report_record import OutboundRequest
from comparo.core.report_record import RecordMeta
from comparo.core.report_record import ReportRecord
from comparo.core.report_record import ResponseRecord
from comparo.core.report_record import Side
from comparo.core.report_record import Sides
from comparo.core.report_record import Summary


def _meta() -> RecordMeta:
    return RecordMeta(id="abc123", created="2026-07-18T15:11:22Z", tool="comparo 0.1.0")


def _invocation(candidate: EnvRef | None) -> Invocation:
    return Invocation(
        command="comparo diff --baseline local --candidate prod",
        environments=Environments(EnvRef("local", "http://localhost:8080"), candidate),
        concurrency=4,
    )


def _diff_record() -> ReportRecord:
    side = Side(
        request=OutboundRequest(method="POST", url="http://x/checkout"),
        response=ResponseRecord(status=200, body={"total": 10}),
    )
    cand = Side(
        request=OutboundRequest(method="POST", url="http://y/checkout"),
        response=ResponseRecord(status=200, body={"total": 12}),
    )
    cell = Cell(
        request_id="request.checkout",
        name="Checkout",
        variant="region=eu",
        verdict="drift",
        sides=Sides(side, cand),
        comparison=Comparison(
            verdict="drift",
            same=3,
            drift=1,
            skipped=1,
            fields=[
                FieldDiffRecord("$.total", "drift", "exact", baseline=10, candidate=12),
                FieldDiffRecord("$.ts", "skip", "ignore", rule="$.ts"),
            ],
        ),
    )
    return ReportRecord(
        kind="diff",
        metadata=_meta(),
        invocation=_invocation(EnvRef("prod", "https://api.example.com", "environment.prod")),
        summary=Summary(gate="FAIL", calls=2, cells=1),
        cells=[cell],
    )


def _run_record() -> ReportRecord:
    side = Side(
        request=OutboundRequest(method="GET", url="http://x/json"),
        response=ResponseRecord(status=200, body={"ok": True}),
        assertions=[
            AssertionRecord("status", "equals", ok=True, severity="error", expected=200, actual=200)
        ],
    )
    cell = Cell(
        request_id="request.get-json",
        name="Get JSON",
        variant="",
        verdict="pass",
        sides=Sides(side, None),
    )
    return ReportRecord(
        kind="run",
        metadata=_meta(),
        invocation=_invocation(None),
        summary=Summary(gate="PASS", calls=1, cells=1),
        cells=[cell],
    )


def test_schema_version_is_a_stored_constant_one() -> None:
    assert SCHEMA_VERSION == 1
    assert _run_record().schema_version == 1
    # ...and it serializes as the first camelCased key.
    assert msgspec.json.encode(_run_record()).startswith(b'{"schemaVersion":1,')


def test_diff_record_round_trips() -> None:
    record = _diff_record()
    raw = msgspec.json.encode(record)
    back = msgspec.json.decode(raw, type=ReportRecord)
    assert back == record
    assert back.kind == "diff"
    assert back.cells[0].comparison is not None
    assert back.cells[0].comparison.fields[0].candidate == 12


def test_run_record_round_trips_with_one_side() -> None:
    record = _run_record()
    back = msgspec.json.decode(msgspec.json.encode(record), type=ReportRecord)
    assert back == record
    assert back.cells[0].sides.candidate is None
    assert back.cells[0].sides.baseline.assertions is not None


def test_an_unknown_field_is_tolerated_on_read() -> None:
    # Forward-compatibility: a newer writer's additive field must not break an
    # older reader — forbid_unknown_fields is deliberately off.
    payload = json.loads(msgspec.json.encode(_run_record()))
    payload["someFutureField"] = {"nested": [1, 2, 3]}
    payload["cells"][0]["anotherNew"] = 7
    back = msgspec.json.decode(json.dumps(payload).encode(), type=ReportRecord)
    assert back.kind == "run"
    assert back.cells[0].name == "Get JSON"


def test_auth_value_is_stored_as_given_mask() -> None:
    # The struct itself does not mask (the builder does); it faithfully stores what
    # it is handed, which for auth is always the mask glyph.
    from comparo.core.report_record import AuthRecord

    req = OutboundRequest(
        method="GET", url="http://x", auth=AuthRecord(scheme="bearer", value="••••••")
    )
    back = msgspec.json.decode(msgspec.json.encode(req), type=OutboundRequest)
    assert back.auth is not None
    assert back.auth.value == "••••••"
