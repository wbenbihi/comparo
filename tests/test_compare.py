"""Tests for the diff-run orchestration."""

import asyncio
from pathlib import Path

from comparo.core.compare import diff_run
from comparo.core.execute import Execution
from comparo.core.http import HttpResponse
from comparo.core.http import TimeoutBudget
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import Request
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import select_environment

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


class _BodyByHost:
    """Returns a canned body chosen by the request URL host."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        host = "prod" if "httpbin.org" in request.url else "local"
        return HttpResponse(200, [], self.bodies[host], 1.0)

    async def aclose(self) -> None:
        return None


def _get_json(loaded: LoadedProject) -> list[Request]:
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    return [request]


class _ConstClient:
    """Always returns the same canned body — a stand-in per environment."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        return HttpResponse(200, [], self.body, 1.0)

    async def aclose(self) -> None:
        return None


def test_compare_cell_diffs_streamed_event_sequences() -> None:
    from comparo.core.compare import compare_cell

    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]  # -> diff.strict (exact)
    assert isinstance(request, Request)
    env = select_environment(loaded, "local")

    def execution(events: list[object]) -> Execution:
        return Execution(request, env, "", HttpResponse(200, [], b"", 1.0, events=events))

    baseline_exec, candidate_exec = execution([{"n": 1}, {"n": 2}]), execution([{"n": 1}, {"n": 9}])
    cell = compare_cell(loaded, baseline_exec, candidate_exec)
    assert cell.drifted
    assert any("[1]" in field.path for field in cell.drifts)  # the second event drifted
    assert cell.baseline_body == [{"n": 1}, {"n": 2}]  # the event sequence is the diffed body
    # Both executions are threaded onto the cell so a report can serialize each side.
    assert cell.baseline is baseline_exec
    assert cell.candidate is candidate_exec


def test_diff_run_routes_candidate_to_its_own_client() -> None:
    loaded = load_project(SAMPLE)
    baseline = select_environment(loaded, "local")
    candidate = select_environment(loaded, "prod")
    base_client = _ConstClient(b'{"v": 1}')
    candidate_client = _ConstClient(b'{"v": 2}')
    results = asyncio.run(
        diff_run(loaded, baseline, candidate, _get_json(loaded), base_client, candidate_client)
    )
    # The candidate body came from its own client, so $.v drifts 1 -> 2.
    assert results[0].drifted


def test_diff_run_reports_same_when_bodies_match() -> None:
    loaded = load_project(SAMPLE)
    baseline = select_environment(loaded, "local")
    candidate = select_environment(loaded, "prod")
    body = b'{"slideshow": {"author": "x", "title": "t", "slides": []}}'
    client = _BodyByHost({"local": body, "prod": body})
    results = asyncio.run(diff_run(loaded, baseline, candidate, _get_json(loaded), client))
    assert len(results) == 1
    assert not results[0].drifted
    assert results[0].error is None


def test_diff_run_handles_empty_body() -> None:
    loaded = load_project(SAMPLE)
    baseline = select_environment(loaded, "local")
    candidate = select_environment(loaded, "prod")
    request = loaded.objects["request.health-status"]
    assert isinstance(request, Request)
    client = _BodyByHost({"local": b"", "prod": b""})
    results = asyncio.run(diff_run(loaded, baseline, candidate, [request], client))
    assert not results[0].drifted
    assert results[0].error is None


def test_diff_run_reports_drift_on_difference() -> None:
    loaded = load_project(SAMPLE)
    baseline = select_environment(loaded, "local")
    candidate = select_environment(loaded, "prod")
    unchanged = b'{"slideshow": {"author": "x", "title": "t", "slides": []}}'
    changed = b'{"slideshow": {"author": "CHANGED", "title": "t", "slides": []}}'
    client = _BodyByHost({"local": unchanged, "prod": changed})
    results = asyncio.run(diff_run(loaded, baseline, candidate, _get_json(loaded), client))
    assert results[0].drifted


def _cell_pair(
    baseline_headers: list[tuple[str, str]],
    candidate_headers: list[tuple[str, str]],
    body: bytes = b'{"v": 1}',
) -> tuple[LoadedProject, Execution, Execution]:
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    env = select_environment(loaded, "local")
    base = Execution(request, env, "", HttpResponse(200, baseline_headers, body, 1.0))
    cand = Execution(request, env, "", HttpResponse(200, candidate_headers, body, 1.0))
    return loaded, base, cand


def test_headers_join_the_diff_as_the_headers_namespace() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.diff import State

    loaded, base, cand = _cell_pair([("X-Api-Version", "2024-08")], [("x-api-version", "2025-01")])
    cell = compare_cell(loaded, base, cand)
    drift = next(field for field in cell.fields if field.path == "$headers.x-api-version")
    assert drift.state is State.DRIFT  # names case-fold before comparing
    assert drift.baseline == "2024-08"
    assert drift.candidate == "2025-01"


def test_volatile_headers_are_silenced_by_builtin_synthetic_rules() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.diff import State

    loaded, base, cand = _cell_pair(
        [("Date", "Mon, 01 Jan 2026 00:00:00 GMT"), ("Content-Length", "8")],
        [("Date", "Mon, 01 Jan 2026 00:00:01 GMT"), ("Content-Length", "9")],
    )
    cell = compare_cell(loaded, base, cand)
    assert not any(
        field.state is State.DRIFT and field.path.startswith("$headers") for field in cell.fields
    )
    date = next(field for field in cell.fields if field.path == "$headers.date")
    assert date.state is State.SKIP
    assert date.rule is not None
    assert date.rule.origin == "synthetic"
    assert date.rule.path == "$headers.date"


def test_credential_headers_are_masked_before_the_diff_sees_them() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.diff import State

    # Two different session cookies must compare equal as the mask — never drift,
    # and never land a real credential in a FieldDiff value.
    loaded, base, cand = _cell_pair(
        [("Set-Cookie", "session=aaa")], [("Set-Cookie", "session=bbb")]
    )
    cell = compare_cell(loaded, base, cand)
    cookie = next(field for field in cell.fields if field.path.startswith("$headers.set-cookie"))
    assert cookie.state is State.SAME
    assert "aaa" not in str(cookie.baseline)
    assert "bbb" not in str(cookie.candidate)


def test_a_body_field_named_headers_never_collides_with_the_namespace() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.diff import State

    # The echoed body has a "headers" key; a $headers rule must not touch it and
    # the body catch-all must still compare it.
    loaded, base, cand = _cell_pair([], [], body=b'{"headers": {"date": "x"}}')
    cell = compare_cell(loaded, base, cand)
    body_field = next(field for field in cell.fields if field.path == "$.headers.date")
    assert body_field.state is State.SAME  # compared (identical), not silenced by $headers.date


def test_rule_outcomes_cover_every_effective_rule() -> None:
    from comparo.core.compare import compare_cell

    loaded, base, cand = _cell_pair([("X-Api-Version", "1")], [("X-Api-Version", "2")])
    cell = compare_cell(loaded, base, cand)
    by_path = {outcome.ref.path: outcome for outcome in cell.rule_outcomes}
    assert by_path["$status"].outcome == "held"
    assert by_path["$headers"].outcome == "broke"  # the headers catch-all saw the drift
    assert by_path["$headers.date"].outcome == "absent"  # built-in matched nothing here
    assert by_path["$"].outcome == "held"  # the body catch-all compared $.v


def test_an_errored_cell_grades_every_rule_error_never_broke() -> None:
    from comparo.core.compare import compare_cell

    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    env = select_environment(loaded, "local")
    base = Execution(request, env, "", HttpResponse(200, [], b"{}", 1.0))
    dead = Execution(request, env, "", None, "ConnectError: boom")
    cell = compare_cell(loaded, base, dead)
    assert cell.error == "candidate: ConnectError: boom"
    assert cell.rule_outcomes  # the effective set is still enumerated
    assert all(outcome.outcome == "error" for outcome in cell.rule_outcomes)
    from comparo.core.compare import unused_rules

    assert unused_rules([cell]) == []  # error cells are inconclusive, never "typo"


def test_unused_rules_names_the_typo_and_spares_the_used() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.compare import unused_rules

    loaded, base, cand = _cell_pair([], [], body=b'{"real": 1}')
    override = {
        "default": "exact",
        "rules": [
            {"path": "$.real", "mode": "ignore"},
            {"path": "$.tpyo", "mode": "ignore"},
        ],
    }
    cell = compare_cell(loaded, base, cand, diff_override=override)
    unused = unused_rules([cell])
    assert [ref.path for ref in unused] == ["$.tpyo"]
    assert all(ref.origin in ("profile", "inline") for ref in unused)


def test_a_canonically_cased_header_rule_governs_the_folded_field() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.diff import State

    # Users write header names as the docs and devtools show them — the rule must
    # still beat the built-in volatile ignore (later-loaded wins the tie).
    loaded, base, cand = _cell_pair(
        [("Date", "Mon, 01 Jan 2026 00:00:00 GMT")],
        [("Date", "Mon, 01 Jan 2026 00:00:01 GMT")],
    )
    override = {"default": "exact", "rules": [{"path": "$headers.Date", "mode": "exact"}]}
    cell = compare_cell(loaded, base, cand, diff_override=override)
    date = next(field for field in cell.fields if field.path == "$headers.date")
    assert date.state is State.DRIFT  # re-checked, not silenced by the built-in
    assert date.rule is not None
    assert date.rule.path == "$headers.Date"  # the ref keeps the declared casing


def test_the_last_status_rule_wins_and_shadowed_ones_stay_in_the_ledger() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.diff import State
    from comparo.core.execute import Execution

    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    env = select_environment(loaded, "local")
    base = Execution(request, env, "", HttpResponse(200, [], b"{}", 1.0))
    cand = Execution(request, env, "", HttpResponse(500, [], b"{}", 1.0))
    override = {
        "default": "exact",
        "rules": [
            {"path": "$status", "mode": "ignore"},
            {"path": "$status", "mode": "exact"},
        ],
    }
    cell = compare_cell(loaded, base, cand, diff_override=override)
    status = next(field for field in cell.fields if field.path == "$status")
    assert status.state is State.DRIFT  # the later exact rule re-checked the status
    status_outcomes = [o for o in cell.rule_outcomes if o.ref.path == "$status"]
    assert len(status_outcomes) == 2  # the shadowed ignore rule stays in the ledger
    assert {o.outcome for o in status_outcomes} == {"broke", "absent"}


def test_unique_correlation_headers_never_drift_out_of_the_box() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.diff import State

    loaded, base, cand = _cell_pair(
        [("X-Request-Id", "aaaa-1111"), ("traceparent", "00-a1-b2-01")],
        [("X-Request-Id", "bbbb-2222"), ("traceparent", "00-c3-d4-01")],
    )
    cell = compare_cell(loaded, base, cand)
    assert not cell.drifted
    skipped = {f.path for f in cell.fields if f.state is State.SKIP}
    assert {"$headers.x-request-id", "$headers.traceparent"} <= skipped


def test_unused_rules_fold_by_written_identity_across_compositions() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.compare import unused_rules

    # The same written rule lands at different composed indices on different
    # cells; the fold must key on what the user wrote, not where it landed.
    loaded, base_a, cand_a = _cell_pair([], [], body=b'{"ts": 1}')
    cell_a = compare_cell(
        loaded,
        base_a,
        cand_a,
        diff_override=[
            {"default": "exact", "rules": [{"path": "$.other", "mode": "ignore"}]},
            {"default": "exact", "rules": [{"path": "$.ts", "mode": "ignore"}]},
        ],
    )
    _, base_b, cand_b = _cell_pair([], [], body=b'{"v": 1}')
    cell_b = compare_cell(
        loaded,
        base_b,
        cand_b,
        diff_override={"default": "exact", "rules": [{"path": "$.ts", "mode": "ignore"}]},
    )
    unused = unused_rules([cell_a, cell_b])
    # $.ts matched on cell A (silenced) so it is used everywhere; $.other never matched.
    assert [ref.path for ref in unused] == ["$.other"]
