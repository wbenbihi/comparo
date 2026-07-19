"""The runtime redaction self-check runs a canary through every leak sink."""

import msgspec

from comparo.adapters.doctor import CANARY
from comparo.adapters.doctor import run_selfcheck
from comparo.adapters.reporters import JsonReporter
from comparo.core.compare import CellDiff
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.models import Environment
from comparo.core.models import EnvironmentSpec
from comparo.core.models import Meta
from comparo.core.models import Request
from comparo.core.redaction import Redactor
from comparo.core.report_builder import record_from_diff

#: The exact sinks (name, detail) the Settings mockup and `comparo doctor` pin.
EXPECTED = [
    ("TUI display", "masked on render"),
    ("saved runs", ".runs/*.json"),
    ("saved reports", ".reports/*.json"),
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
    fields = [
        FieldDiff(f"$.{CANARY}", State.DRIFT, "exact", "", baseline=CANARY, candidate="other")
    ]
    cell = CellDiff(request, f"token={CANARY}", fields, None)
    env = Environment(
        api_version="comparo/v1",
        metadata=Meta(name=f"env {CANARY}", id="environment.x"),
        spec=EnvironmentSpec(base_url=f"http://{CANARY}.test"),
    )

    def _render(redact: object) -> str:
        record = record_from_diff(
            env,
            env,
            [cell],
            record_id="d",
            created="t",
            tool="comparo 0",
            project=None,
            concurrency=1,
            redact=redact,  # type: ignore[arg-type]
        )
        return JsonReporter().render(record)

    assert CANARY in _render(str)  # identity redaction: the sink WOULD write the canary
    assert CANARY not in _render(Redactor((CANARY,)).text)  # the real redactor closes it
