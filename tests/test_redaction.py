"""A secret echoed into a drifting response must never reach a report or archive."""

import json
from pathlib import Path

from comparo.core.archive import record_from_execution
from comparo.core.assertions import AssertionResult
from comparo.core.compare import CellDiff
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionResult
from comparo.core.export import RunEntry
from comparo.core.export import export_run
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.matrix import MatrixCell
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.redaction import MASK
from comparo.core.redaction import Redactor
from comparo.core.report import build_report

SAMPLE = Path(__file__).parent.parent / "examples" / "canary-project"
SECRET = "cG9zdG1hbjpwYXNzd29yZA=="  # the canary BASIC_AUTH literal


def _request(loaded: object) -> Request:
    request = loaded.objects["request.basic-auth"]  # type: ignore[attr-defined]
    assert isinstance(request, Request)
    return request


def test_secret_values_are_collected() -> None:
    loaded = load_project(SAMPLE)
    assert SECRET in Redactor.for_project(loaded).values


def test_build_report_redacts_a_leaked_secret() -> None:
    loaded = load_project(SAMPLE)
    redact = Redactor.for_project(loaded).text
    # The server echoed the secret back into a field that drifted.
    field = FieldDiff("$.authenticated", State.DRIFT, "exact", f'"{SECRET}" → "other"')
    cell = CellDiff(_request(loaded), "", [field])
    report = build_report("Stable", "Canary", [cell], redact)
    detail = report.cells[0].drifts[0].detail
    assert SECRET not in detail
    assert MASK in detail


def test_run_detail_tree_masks_a_secret_echoed_into_the_response() -> None:
    # The Run screen's DETAIL tree renders the real server response — a secret it
    # echoes into a body/header/check/error must be masked, like every other sink.
    import json

    from textual.widgets import Tree

    from comparo.core.checks import Check
    from comparo.core.execute import Execution
    from comparo.core.http import HttpResponse
    from comparo.core.matrix import MatrixCell
    from comparo.core.models import Environment
    from comparo.tui.app import _build_report_tree

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    redact = Redactor(values=(SECRET,)).text
    response = HttpResponse(200, [("x-echo", SECRET)], json.dumps({"tok": SECRET}).encode(), 5.0)
    execution = Execution(request=request, environment=environment, response=response, cell_key="")
    tree: Tree[object] = Tree("root")
    _build_report_tree(
        tree,
        loaded,
        environment,
        request,
        MatrixCell("", ()),
        execution,
        "ok",
        [Check("auth", ok=False, detail=f"got {SECRET}")],
        redact,
    )

    def labels(node: object) -> list[str]:
        out = [str(node.label)]  # type: ignore[attr-defined]
        for child in node.children:  # type: ignore[attr-defined]
            out += labels(child)
        return out

    rendered = "\n".join(labels(tree.root))
    assert SECRET not in rendered


def test_run_tree_body_redacts_before_truncating_at_every_boundary() -> None:
    # A secret straddling a body-render truncation boundary (SSE 200, text 4000,
    # HTML 20000) must be masked — redaction happens before the clip.
    from textual.widgets import Tree

    from comparo.core.execute import Execution
    from comparo.core.http import HttpResponse
    from comparo.core.matrix import MatrixCell
    from comparo.core.models import Environment
    from comparo.tui.app import _build_report_tree

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    redact = Redactor(values=(SECRET,)).text
    cases = [
        ("text/event-stream", ("data: " + "x" * 250 + SECRET + "\n\n").encode()),
        ("text/plain", ("y" * 4050 + SECRET).encode()),
        ("text/html", ("<p>" + "z" * 20050 + SECRET + "</p>").encode()),
    ]
    for content_type, body in cases:
        response = HttpResponse(200, [("content-type", content_type)], body, 5.0)
        execution = Execution(
            request=request, environment=environment, response=response, cell_key=""
        )
        tree: Tree[object] = Tree("root")
        _build_report_tree(
            tree, loaded, environment, request, MatrixCell("", ()), execution, "ok", [], redact
        )

        def labels(node: object) -> list[str]:
            out = [str(node.label)]  # type: ignore[attr-defined]
            for child in node.children:  # type: ignore[attr-defined]
                out += labels(child)
            return out

        rendered = "\n".join(labels(tree.root))
        assert "cG9zdG1hbj" not in rendered  # not even the secret's prefix leaks


def test_a_secret_echoed_as_a_json_key_is_masked_on_disk_and_screen() -> None:
    # Redaction masks values AND keys/paths: a server can echo a secret as an
    # object key, which becomes a drift field path — it must not reach disk/screen.
    from rich.console import Console

    from comparo.core.archive import record_from_diff
    from comparo.core.compare import CellDiff
    from comparo.core.diff import diff
    from comparo.tui.app import _diff_body_view

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    redact = Redactor(values=(SECRET,)).text
    base = {"tokens": {SECRET: 1}}
    cand = {"tokens": {SECRET: 2}}
    fields = diff(base, cand, "exact", [])
    cell = CellDiff(request, "", fields, None, base, cand)

    # Disk: the archive record's drift_paths must not carry the secret key.
    record = record_from_diff("A", "B", [cell], run_id="k1", created="now", redact=redact)
    assert not any(SECRET in path for row in record.requests for path in row.drift_paths)

    # Screen: the live git-diff body must mask the echoed key too.
    drift = next(field for field in fields if field.state is State.DRIFT)
    console = Console(width=200)
    group = (drift.path, [(cell, drift)])
    with console.capture() as capture:
        console.print(_diff_body_view(group, None, unified=True, redact=redact))
    assert SECRET not in capture.get()


def test_export_run_masks_a_secret_echoed_as_a_json_key() -> None:
    # The runs/*.json disk export must mask a secret echoed as an object KEY, not
    # only as a value — otherwise the Run screen's `s` save writes it to disk.
    import json

    from comparo.core.execute import Execution
    from comparo.core.export import RunEntry
    from comparo.core.export import export_run
    from comparo.core.http import HttpResponse
    from comparo.core.matrix import MatrixCell
    from comparo.core.models import Environment

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    body = json.dumps({"tokens": {SECRET: 1}}).encode()
    response = HttpResponse(200, [("x-echo", SECRET)], body, 5.0)
    execution = Execution(request=request, environment=environment, cell_key="", response=response)
    entry = RunEntry(request, MatrixCell("", ()), execution, [])
    document = export_run(loaded, environment, [entry])
    assert SECRET not in document
    assert "cG9zdG1hbj" not in document  # not even the secret's prefix


def test_execution_screen_renders_mask_a_secret_echoed_as_a_key_or_path() -> None:
    # Every Execution-screen render site (drift table, verdict path + error, skip
    # legend, git hunk header, skip panel) must mask a secret echoed as a JSON
    # key / drift path — not only as a value.
    from rich.console import Console

    from comparo.core.compare import CellDiff
    from comparo.core.diff import FieldDiff
    from comparo.core.diff import diff
    from comparo.core.execution import CellOutcome
    from comparo.core.execution import ExecutionResult
    from comparo.tui.app import _cell_verdict
    from comparo.tui.app import _diff_body_view
    from comparo.tui.app import _diff_skip_view
    from comparo.tui.app import _drift_change
    from comparo.tui.app import _exec_diff_legend

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    redact = Redactor(values=(SECRET,)).text
    base = {"tokens": {SECRET: 1}}
    cand = {"tokens": {SECRET: 2}}
    fields = diff(base, cand, "exact", [])
    drift = next(field for field in fields if field.state is State.DRIFT)
    path = drift.path  # a field path carrying the echoed secret as its key
    drift_cell = CellDiff(request, "", fields, None, base, cand)
    drift_outcome = CellOutcome("request.basic-auth", "", [], [], drift_cell)
    err_outcome = CellOutcome("request.basic-auth", "", [], [], None, error=f"boom {path}")
    skip_cell = CellDiff(
        request, "", [FieldDiff(path, State.SKIP, "ignore", "volatile")], None, base, cand
    )
    skip_result = ExecutionResult(
        "exec.x",
        "Stable",
        "Candidate",
        True,
        True,
        [CellOutcome("request.basic-auth", "", [], [], skip_cell)],
    )
    console = Console(width=200)

    renders = [
        _drift_change(drift_outcome, redact),  # drift-table change column
        _cell_verdict(drift_outcome, redact),  # verdict path branch
        _cell_verdict(err_outcome, redact),  # verdict error branch
        _exec_diff_legend(skip_result, redact),  # skip legend
        _diff_skip_view(path, (path, [(drift_cell, drift)]), redact),  # skip panel
        _diff_body_view((path, [(drift_cell, drift)]), None, unified=True, redact=redact),  # hunk
    ]
    for render in renders:
        with console.capture() as capture:
            console.print(render)
        assert SECRET not in capture.get()
        assert "cG9zdG1hbj" not in capture.get()


def test_cli_diff_masks_a_secret_echoed_as_a_json_key_path() -> None:
    # `comparo diff` prints each drifted field path to stdout / CI logs; a secret
    # echoed as a JSON key becomes that path and must be masked there too.
    import io
    from contextlib import redirect_stdout

    from comparo.cli.app import _print_diffs
    from comparo.core.compare import CellDiff
    from comparo.core.diff import diff

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    redact = Redactor(values=(SECRET,)).text
    base = {"tokens": {SECRET: 1}}
    cand = {"tokens": {SECRET: 2}}
    cell = CellDiff(request, "", diff(base, cand, "exact", []), None, base, cand)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _print_diffs([cell], "Stable", "Canary", redact)
    printed = buffer.getvalue()
    assert SECRET not in printed
    assert "cG9zdG1hbj" not in printed


def test_assertion_label_carrying_a_secret_is_masked_on_disk_and_screen() -> None:
    # A rule's label embeds its asserted value (``authorization contains <value>``).
    # If a user asserts against a secret literal, the label must be masked on disk
    # (.reports/*.json) and on the Execution screen — not only the offending detail.
    from rich.console import Console

    from comparo.tui.app import _exec_assert_body

    loaded = load_project(SAMPLE)
    redact = Redactor.for_project(loaded).text
    label = f"authorization contains {SECRET}"
    leaked = AssertionResult("authorization", "contains", False, "error", "differs", label)
    outcome = CellOutcome("request.basic-auth", "", [leaked], [], None)
    result = ExecutionResult("exec.x", "Stable", "Canary", True, True, [outcome])

    # Disk: the persisted assertion line's label must not carry the secret.
    record = record_from_execution(result, run_id="lab1", created="now", name="X", redact=redact)
    lines = record.baseline_assertions.lines
    assert lines
    assert SECRET not in lines[0].label
    assert MASK in lines[0].label

    # Screen: the live Execution assertion render must mask it too.
    console = Console(width=200)
    with console.capture() as capture:
        console.print(_exec_assert_body([("request.basic-auth", leaked)], redact))
    assert SECRET not in capture.get()


def test_export_run_masks_a_secret_used_as_a_response_header_name() -> None:
    # A server can reflect a query-param name into a response-header name; the
    # declared secret named that way must be masked as a KEY on disk, not echoed.
    from comparo.core.execute import Execution
    from comparo.core.export import RunEntry
    from comparo.core.export import export_run
    from comparo.core.http import HttpResponse
    from comparo.core.matrix import MatrixCell
    from comparo.core.models import Environment

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    response = HttpResponse(200, [(SECRET, "reflected")], b"{}", 5.0)
    execution = Execution(request=request, environment=environment, cell_key="", response=response)
    entry = RunEntry(request, MatrixCell("", ()), execution, [])
    document = export_run(loaded, environment, [entry])
    assert SECRET not in document
    assert "cG9zdG1hbj" not in document
    # A secret longer than the old 40-char detail clip must still be masked: the
    # diff detail now carries the full value so a whole-value redactor catches it.
    from comparo.core.diff import diff

    long_secret = "sk-" + "z" * 80  # 83 chars, well past the old 40-char clip
    fields = diff({"token": long_secret}, {"token": "other"}, "exact", [])
    drift = next(field for field in fields if field.state is State.DRIFT)
    assert long_secret in drift.detail  # the FULL secret reaches the sink …
    redact = Redactor(values=(long_secret,)).text
    assert long_secret not in redact(drift.detail)  # … so redaction masks all of it
    assert MASK in redact(drift.detail)


def test_cell_key_equal_to_a_secret_is_masked_in_report_and_cli() -> None:
    # A matrix case value can equal a declared secret; the case key ``token=<value>``
    # then carries it into every reporter (JSON/JUnit/SARIF/Markdown) and the CLI.
    import io
    from contextlib import redirect_stdout

    from comparo.cli.app import _print_diffs
    from comparo.core.compare import CellDiff
    from comparo.core.diff import diff

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    redact = Redactor(values=(SECRET,)).text
    fields = diff({"a": 1}, {"a": 2}, "exact", [])
    cell = CellDiff(request, f"token={SECRET}", fields, None, {"a": 1}, {"a": 2})
    report = build_report("Stable", "Canary", [cell], redact)
    assert SECRET not in report.cells[0].cell_key
    assert MASK in report.cells[0].cell_key
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _print_diffs([cell], "Stable", "Canary", redact)
    assert SECRET not in buffer.getvalue()


def test_sse_event_name_echoing_a_secret_is_masked() -> None:
    # A server can name an SSE event after a secret (`event: <secret>`); the Run
    # detail tree must mask it, like the sibling id/data fields.
    from textual.widgets import Tree

    from comparo.core.execute import Execution
    from comparo.core.http import HttpResponse
    from comparo.core.matrix import MatrixCell
    from comparo.core.models import Environment
    from comparo.tui.app import _build_report_tree

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    redact = Redactor(values=(SECRET,)).text
    body = f"event: {SECRET}\nid: 1\ndata: hello\n\n".encode()
    response = HttpResponse(200, [("content-type", "text/event-stream")], body, 5.0)
    execution = Execution(request=request, environment=environment, response=response, cell_key="")
    tree: Tree[object] = Tree("root")
    _build_report_tree(
        tree, loaded, environment, request, MatrixCell("", ()), execution, "ok", [], redact
    )

    def labels(node: object) -> list[str]:
        out = [str(node.label)]  # type: ignore[attr-defined]
        for child in node.children:  # type: ignore[attr-defined]
            out += labels(child)
        return out

    assert SECRET not in "\n".join(labels(tree.root))


def test_request_preview_masks_an_untainted_declared_secret() -> None:
    # A value equal to a declared secret can arrive untainted (a plain literal or a
    # non-secret variable). The request preview (Explorer screen + `comparo render`)
    # must apply the string-match backstop, like _build_report_tree does.
    import io
    from contextlib import redirect_stdout

    from rich.console import Console

    from comparo.cli.app import _print_resolved
    from comparo.core.resolve import ResolvedRequest
    from comparo.tui.app import _request_detail

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    redact = Redactor(values=(SECRET,)).text
    resolved = ResolvedRequest(
        method="GET",
        url=f"https://x/?q={SECRET}",
        headers=[("x-echo", SECRET)],
        query={"q": SECRET},
        body={"field": SECRET},
        trail=[],
    )
    console = Console(width=200)
    with console.capture() as capture:
        console.print(_request_detail(loaded, request, resolved, raw=False, redact=redact))
    assert SECRET not in capture.get()

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _print_resolved(resolved, "Stable", redact)
    assert SECRET not in buffer.getvalue()


def test_export_run_masks_a_secret_in_the_matrix_case_key() -> None:
    # A matrix case value can equal a declared secret; the case key ``token=<value>``
    # is written to the `case` field of runs/*.json and must be masked on disk.
    from comparo.core.execute import Execution
    from comparo.core.export import RunEntry
    from comparo.core.export import export_run
    from comparo.core.http import HttpResponse
    from comparo.core.matrix import MatrixCell
    from comparo.core.models import Environment

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    response = HttpResponse(200, [], b"{}", 5.0)
    execution = Execution(
        request=request, environment=environment, cell_key=f"token={SECRET}", response=response
    )
    entry = RunEntry(request, MatrixCell(f"token={SECRET}", ()), execution, [])
    document = export_run(loaded, environment, [entry])
    assert SECRET not in document
    assert "cG9zdG1hbj" not in document


def test_explorer_config_views_mask_an_untainted_secret() -> None:
    # A declared secret placed as a matrix case value or in a manifest config
    # section renders in Explorer detail panels via the string-match backstop.
    import msgspec
    from rich.console import Console

    from comparo.core.models import ExecutionProfile
    from comparo.core.models import Project
    from comparo.tui.app import _execution_profile_detail
    from comparo.tui.app import _project_detail

    redact = Redactor(values=(SECRET,)).text
    console = Console(width=200)

    profile = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "ExecutionProfile",
            "metadata": {"id": "exec.x", "name": "X"},
            "spec": {"matrix": {"tokens": {"override": [{"token": SECRET}]}}},
        },
        type=ExecutionProfile,
    )
    with console.capture() as capture:
        console.print(_execution_profile_detail(profile, redact))
    assert SECRET not in capture.get()

    manifest = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Project",
            "metadata": {"name": "P"},
            "spec": {"selection": {"tags": [SECRET]}},
        },
        type=Project,
    )
    with console.capture() as capture:
        console.print(_project_detail(manifest, redact))
    assert SECRET not in capture.get()


def test_exec_header_masks_a_secret_in_select_tags_or_requests() -> None:
    # The Execution screen header renders select tags/request-ids (spec values); a
    # declared secret used as a tag or request-id must be masked, as it is in the
    # sibling ExecutionProfile detail view.
    import msgspec
    from rich.console import Console

    from comparo.core.execution import ExecutionResult
    from comparo.core.models import ExecutionProfile
    from comparo.tui.app import _exec_header

    profile = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "ExecutionProfile",
            "metadata": {"id": "exec.x", "name": "X"},
            "spec": {"select": {"tags": [SECRET], "requests": [f"req.{SECRET}"]}},
        },
        type=ExecutionProfile,
    )
    result = ExecutionResult("exec.x", SECRET, f"c-{SECRET}", True, True, [])
    redact = Redactor(values=(SECRET,)).text
    console = Console(width=300)
    with console.capture() as capture:
        console.print(_exec_header(profile, result, redact))
    assert SECRET not in capture.get()


def test_report_and_archive_mask_env_names() -> None:
    # Env names flow to JSON/Markdown reporters and to .reports/*.json; on the
    # vanishing chance a name equals a declared secret it is masked (the backstop).
    from comparo.adapters.reporters import MarkdownReporter
    from comparo.core.archive import record_from_diff
    from comparo.core.compare import CellDiff

    loaded = load_project(SAMPLE)
    request = _request(loaded)
    redact = Redactor(values=(SECRET,)).text
    cell = CellDiff(request, "", [])
    report = build_report(SECRET, f"c-{SECRET}", [cell], redact)
    assert SECRET not in report.baseline
    assert SECRET not in (report.candidate or "")
    assert SECRET not in MarkdownReporter().render(report)
    record = record_from_diff(
        SECRET, f"c-{SECRET}", [cell], run_id="r", created="now", redact=redact
    )
    assert SECRET not in record.baseline
    assert SECRET not in (record.candidate or "")


def test_provenance_masks_a_matrix_case_value() -> None:
    # A MATRIX-origin provenance detail is a case_key (``token=<value>``) that can
    # carry a declared secret; the provenance renderer must mask it.
    from rich.console import Console

    from comparo.core.provenance import Origin
    from comparo.core.provenance import Trail
    from comparo.tui.app import _render_provenance

    redact = Redactor(values=(SECRET,)).text
    trail = [Trail("headers.x", Origin.MATRIX, f"token={SECRET}")]
    console = Console(width=200)
    with console.capture() as capture:
        console.print(_render_provenance(trail, redact))
    assert SECRET not in capture.get()


def test_project_and_environment_detail_mask_config_secrets() -> None:
    # DIFF PAIRS names and a health-check endpoint that equal a declared secret
    # (untainted manifest/env literals) render via the string-match backstop.
    import msgspec
    from rich.console import Console

    from comparo.core.models import Environment
    from comparo.core.models import Project
    from comparo.tui.app import _environment_detail
    from comparo.tui.app import _project_detail

    redact = Redactor(values=(SECRET,)).text
    console = Console(width=200)

    manifest = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Project",
            "metadata": {"name": "P"},
            "spec": {
                "data": SECRET,
                "environments": {
                    "diffPairs": [{"name": SECRET, "baseline": SECRET, "candidate": SECRET}]
                },
            },
        },
        type=Project,
    )
    with console.capture() as capture:
        console.print(_project_detail(manifest, redact))
    assert SECRET not in capture.get()

    env = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Environment",
            "metadata": {"id": "env.x", "name": "X"},
            "spec": {
                "baseUrl": "https://h",
                "health": [{"method": "GET", "endpoint": f"/probe/{SECRET}"}],
            },
        },
        type=Environment,
    )
    with console.capture() as capture:
        console.print(_environment_detail(env, None, redact))
    assert SECRET not in capture.get()


def test_environment_detail_masks_a_secret_in_base_url_or_a_variable() -> None:
    # A credential embedded in base_url, or a variable whose value equals a declared
    # secret (the untainted vector), must be masked in the Explorer Environment view.
    import msgspec
    from rich.console import Console

    from comparo.core.models import Environment
    from comparo.tui.app import _environment_detail

    env = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Environment",
            "metadata": {"id": "env.x", "name": "X"},
            "spec": {"baseUrl": f"https://u:{SECRET}@h", "variables": {"mirror": SECRET}},
        },
        type=Environment,
    )
    redact = Redactor(values=(SECRET,)).text
    console = Console(width=200)
    with console.capture() as capture:
        console.print(_environment_detail(env, None, redact))
    assert SECRET not in capture.get()


def test_assertion_profile_detail_masks_a_secret_rule_value() -> None:
    # The Explorer's AssertionProfile view renders each rule's expected value; a
    # rule asserting against a declared secret literal must not show it raw.
    import msgspec
    from rich.console import Console

    from comparo.core.models import AssertionProfile
    from comparo.tui.app import _assertion_profile_detail

    profile = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "AssertionProfile",
            "metadata": {"id": "assert.x", "name": "X"},
            "spec": {
                "rules": [{"target": "header:authorization", "op": "contains", "value": SECRET}]
            },
        },
        type=AssertionProfile,
    )
    redact = Redactor(values=(SECRET,)).text
    console = Console(width=200)
    with console.capture() as capture:
        console.print(_assertion_profile_detail(profile, redact))
    assert SECRET not in capture.get()


def test_build_report_redacts_a_long_leaked_secret() -> None:
    from comparo.core.diff import diff

    long_secret = "tok_" + "a" * 90
    fields = diff({"echo": long_secret}, {"echo": "x"}, "exact", [])
    cell = CellDiff(_request(load_project(SAMPLE)), "", fields)
    redact = Redactor(values=(long_secret,)).text
    report = build_report("Stable", "Canary", [cell], redact)
    detail = report.cells[0].drifts[0].detail
    assert long_secret not in detail
    assert MASK in detail


def test_archive_redacts_leaked_secret_in_assertion_detail() -> None:
    loaded = load_project(SAMPLE)
    redact = Redactor.for_project(loaded).text
    leaked = AssertionResult(
        "body:$.token", "equals", False, "error", f'"{SECRET}" != "expected"', "token check"
    )
    outcome = CellOutcome("request.basic-auth", "", [leaked], [], None)
    result = ExecutionResult("exec.x", "Stable", "Canary", True, True, [outcome])
    record = record_from_execution(result, run_id="abc123", created="now", name="X", redact=redact)
    lines = record.baseline_assertions.lines
    assert lines
    assert SECRET not in lines[0].detail
    assert MASK in lines[0].detail


def _tainted_cell() -> CellDiff:
    # A cell whose response echoes the secret everywhere the saved replay stores:
    # as a body value AND key, as a drift/skip field path, in a header name/value,
    # and in the matrix case key.
    loaded = load_project(SAMPLE)
    request = _request(loaded)
    base = {SECRET: SECRET, "tokens": {SECRET: 1}}
    cand = {SECRET: SECRET, "tokens": {SECRET: 2}}
    fields = [
        FieldDiff(f"$.{SECRET}", State.DRIFT, "exact", f'"{SECRET}" → "x"'),
        FieldDiff(f"$.headers.{SECRET}", State.SKIP, "ignore", "volatile"),
    ]
    return CellDiff(
        request,
        f"token={SECRET}",
        fields,
        None,
        base,
        cand,
        status=200,
        latency_ms=42,
        size_bytes=128,
        response_headers=((SECRET, SECRET), ("content-type", "application/json")),
    )


def test_saved_cell_body_and_metrics_mask_a_secret_on_disk() -> None:
    # The saved-replay CellRecord persists the before/after bodies, response headers
    # and field paths to .reports/*.json — a secret the server echoes into ANY of
    # those must be masked before it reaches disk.
    import json
    import tempfile

    from comparo.core.archive import record_from_diff
    from comparo.core.archive import save_record

    redact = Redactor(values=(SECRET,)).text
    record = record_from_diff(
        "A", "B", [_tainted_cell()], run_id="cellsec", created="now", redact=redact
    )
    assert record.cells
    cell = record.cells[0]
    assert SECRET not in json.dumps(cell.baseline_body)
    assert SECRET not in json.dumps(cell.candidate_body)
    assert not any(SECRET in key or SECRET in value for key, value in cell.response_headers.items())
    assert not any(SECRET in path for path in cell.drift_paths)
    assert not any(SECRET in path for path in cell.skip_paths)
    assert SECRET not in cell.variant

    # The strongest check: the whole record as written to disk carries no secret.
    with tempfile.TemporaryDirectory() as directory:
        path = save_record(Path(directory), record)
        text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "cG9zdG1hbj" not in text  # not even the secret's prefix


def test_execution_record_cells_mask_a_secret_on_disk() -> None:
    # The execution save path (`s` on the Execution results) persists per-cell bodies
    # too — the same secret masking must hold through record_from_execution.
    import json

    redact = Redactor(values=(SECRET,)).text
    outcome = CellOutcome("request.basic-auth", f"token={SECRET}", [], [], _tainted_cell())
    result = ExecutionResult("exec.x", "Stable", "Canary", True, True, [outcome])
    record = record_from_execution(result, run_id="execsec", created="now", name="X", redact=redact)
    assert record.cells
    cell = record.cells[0]
    assert SECRET not in json.dumps(cell.baseline_body)
    assert SECRET not in json.dumps(cell.candidate_body)
    assert not any(SECRET in path for path in cell.drift_paths + cell.skip_paths)
    assert not any(SECRET in key or SECRET in value for key, value in cell.response_headers.items())


# ── encoding-robust redaction, overlapping secrets, server-issued credentials ──


def _project_with_secrets(
    tmp_path: Path, secrets: dict[str, str], *, with_request: bool = False
) -> LoadedProject:
    """Write a minimal project declaring *secrets* as ``$literal`` values."""
    manifest = "apiVersion: comparo/v1\nkind: Project\n"
    manifest += "metadata: {name: x, id: project.x}\nspec: {data: .}\n"
    (tmp_path / "comparo.yaml").write_text(manifest, encoding="utf-8")
    lines = [
        "apiVersion: comparo/v1",
        "kind: Environment",
        "metadata: {name: e, id: environment.e}",
        "spec:",
        "  baseUrl: http://127.0.0.1:1",
    ]
    if secrets:
        lines.append("  secrets:")
        for name, value in secrets.items():
            lines += [f"    {name}:", "      from:", f"        - $literal: {json.dumps(value)}"]
    (tmp_path / "env.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if with_request:
        (tmp_path / "req.yaml").write_text(
            "apiVersion: comparo/v1\nkind: Request\n"
            "metadata: {name: p, id: request.probe}\n"
            "spec:\n  request: {method: GET, endpoint: /x}\n",
            encoding="utf-8",
        )
    return load_project(tmp_path / "comparo.yaml")


def _run_entry(loaded: LoadedProject, response: HttpResponse) -> tuple[Environment, RunEntry]:
    env = loaded.objects["environment.e"]
    request = loaded.objects["request.probe"]
    assert isinstance(env, Environment)
    assert isinstance(request, Request)
    execution = Execution(request, env, "", response)
    return env, RunEntry(request, MatrixCell("", ()), execution, [])


def test_a_secret_with_json_special_chars_is_masked_after_json_dumps(tmp_path: Path) -> None:
    # A detail/body is json.dumps-ed before a sink redacts it; a secret with a
    # quote/backslash/newline appears escaped and a raw match would miss it.
    secret = 'LEAKME"tok\\en\nTAIL'
    loaded = _project_with_secrets(tmp_path, {"TOKEN": secret})
    redact = Redactor.for_project(loaded).text
    escaped = json.dumps(secret)  # exactly what diff._short / assertions._short emit
    masked = redact(escaped)
    assert "LEAKME" not in masked
    assert "TAIL" not in masked
    assert MASK in masked
    assert "LEAKME" not in redact(f"header={secret}")  # plain form masked too


def test_export_run_masks_overlapping_secrets_longest_first(tmp_path: Path) -> None:
    # A non-longest-first redactor masks the shorter secret first, leaving the
    # longer secret's tail on disk. export_run must mask the long secret whole.
    loaded = _project_with_secrets(
        tmp_path, {"A": "tok-SHORT", "B": "tok-SHORT-and-LONGTAIL"}, with_request=True
    )
    body = b'{"echo": "tok-SHORT-and-LONGTAIL"}'
    env, entry = _run_entry(loaded, HttpResponse(200, [], body, 3.0))
    out = export_run(loaded, env, [entry])
    assert "LONGTAIL" not in out
    assert "tok-SHORT-and-LONGTAIL" not in out


def test_export_masks_a_server_issued_set_cookie(tmp_path: Path) -> None:
    # A Set-Cookie the server issues was never declared, so value-matching can't
    # mask it; the header-name policy must.
    loaded = _project_with_secrets(tmp_path, {}, with_request=True)
    response = HttpResponse(200, [("set-cookie", "session=SERVERSIDETOKEN-xyz")], b"{}", 3.0)
    env, entry = _run_entry(loaded, response)
    out = export_run(loaded, env, [entry])
    assert "SERVERSIDETOKEN" not in out
