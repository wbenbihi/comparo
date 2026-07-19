"""The runtime redaction self-check runs a canary through every leak sink."""

import msgspec

from comparo.adapters.doctor import CANARY
from comparo.adapters.doctor import run_selfcheck
from comparo.adapters.reporters import JsonReporter
from comparo.core.compare import CellDiff
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.models import Request
from comparo.core.redaction import Redactor
from comparo.core.report import build_report

#: The exact sinks (name, detail) the Settings mockup and `comparo doctor` pin.
EXPECTED = [
    ("TUI display", "masked on render"),
    ("saved runs", ".runs/*.json"),
    ("saved reports", ".reports/*.json"),
    ("saved reports v1", "report record"),
    ("JUnit reporter", "reports/junit.xml"),
    ("SARIF reporter", "reports/comparo.sarif"),
    ("JSON reporter", "reports/comparo.json"),
    ("Markdown reporter", "GitHub step summary"),
    ("curl copy", "yanked command"),
    ("crash report", "traceback scrub"),
]


def test_selfcheck_returns_every_sink_in_order() -> None:
    checks = run_selfcheck()
    assert [check.name for check in checks] == [name for name, _ in EXPECTED]
    assert [check.detail for check in checks] == [detail for _, detail in EXPECTED]


def test_every_sink_masks_the_canary_today() -> None:
    checks = run_selfcheck()
    failed = [(check.name, check.detail) for check in checks if not check.ok]
    assert not failed, failed


def _canary_request() -> Request:
    return msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Request",
            "metadata": {"name": "Canary probe", "id": "request.canary"},
            "spec": {"request": {"method": "GET", "endpoint": "/probe"}},
        },
        type=Request,
    )


def test_selfcheck_is_not_vacuously_green() -> None:
    # The guard: the SAME canary scenario rendered WITHOUT redaction must leak the
    # canary. That proves a green self-check reflects real masking — not a sink
    # that happened to drop the value on the floor.
    request = _canary_request()
    fields = [FieldDiff(f"$.{CANARY}", State.DRIFT, "exact", f'"{CANARY}" -> "other"')]
    cell = CellDiff(request, f"token={CANARY}", fields, None, {CANARY: 1}, {CANARY: 2})

    leaked = JsonReporter().render(build_report(f"b {CANARY}", f"c {CANARY}", [cell], str))
    assert CANARY in leaked  # identity redaction: the sink WOULD write the canary

    masked = JsonReporter().render(
        build_report(f"b {CANARY}", f"c {CANARY}", [cell], Redactor((CANARY,)).text)
    )
    assert CANARY not in masked  # the real redactor closes that same leak
