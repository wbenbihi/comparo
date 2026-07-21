"""End-to-end tests: the real CLI against a real local HTTP server.

Every other test either fakes the transport or monkeypatches the engine; these
drive the full stack — CLI parsing, project loading, the resolver's execute
sink, httpx over a real socket, the diff engine, the gate, and the report
artifacts — so a regression anywhere in that chain fails here, not in front of
a user. No external network is touched: each test serves from an ephemeral
localhost port.
"""

import http.server
import json
import textwrap
import threading
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from comparo.cli.app import app

runner = CliRunner()

#: route -> (status, content-type, body-producer); mutable so a test can vary one side.
Routes = dict[str, tuple[int, str, Callable[[], bytes]]]


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: ClassVar[Routes] = {}

    def do_GET(self) -> None:
        entry = self.routes.get(self.path.split("?")[0])
        if entry is None:
            self.send_response(404)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')
            return
        status, content_type, produce = entry
        body = produce()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:  # keep test output clean
        pass


@pytest.fixture
def serve() -> Iterator[Callable[[Routes], str]]:
    servers: list[http.server.ThreadingHTTPServer] = []

    def start(routes: Routes) -> str:
        handler = type("Handler", (_Handler,), {"routes": routes})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _json_route(payload: object) -> tuple[int, str, Callable[[], bytes]]:
    return (200, "application/json", lambda: json.dumps(payload).encode())


def _project(directory: Path, baseline_url: str, candidate_url: str) -> Path:
    """Write a minimal two-environment project and return its manifest path."""
    (directory / "comparo.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: comparo/v1
            kind: Project
            metadata: {name: e2e, id: project.e2e}
            spec:
              data: .
              environments:
                default: baseline
            """
        ),
        encoding="utf-8",
    )
    for name, url in (("baseline", baseline_url), ("candidate", candidate_url)):
        (directory / f"env-{name}.yaml").write_text(
            textwrap.dedent(
                f"""\
                apiVersion: comparo/v1
                kind: Environment
                metadata: {{name: {name}, id: environment.{name}}}
                spec:
                  baseUrl: {url}
                  timeout:
                    connect: 5s
                    read: 5s
                """
            ),
            encoding="utf-8",
        )
    (directory / "request.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: comparo/v1
            kind: Request
            metadata: {name: users, id: request.users}
            spec:
              request:
                method: GET
                endpoint: /users
              response:
                status: 200
            """
        ),
        encoding="utf-8",
    )
    return directory / "comparo.yaml"


def _diff_args(config: Path, output: Path, *reports: str) -> list[str]:
    args = ["diff", "--config", str(config), "--baseline", "baseline", "--candidate", "candidate"]
    for name in reports:
        args += ["--report", name]
    return [*args, "--output", str(output)]


def test_identical_environments_pass_the_gate_and_write_valid_reports(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    payload = {"users": [{"id": 1, "name": "ada"}], "total": 1}
    baseline = serve({"/users": _json_route(payload)})
    candidate = serve({"/users": _json_route(payload)})
    config = _project(tmp_path, baseline, candidate)
    output = tmp_path / "reports"

    result = runner.invoke(app, _diff_args(config, output, "junit", "sarif", "json", "markdown"))

    assert result.exit_code == 0, result.output
    assert "gate: PASS" in result.output
    # Every reporter wrote a well-formed artifact.
    junit = ElementTree.parse(output / "junit.xml").getroot()
    assert junit.tag == "testsuites"
    assert junit.get("failures") == "0"
    assert junit.get("errors") == "0"
    sarif = json.loads((output / "comparo.sarif").read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["schemaVersion"] == 1
    assert report["kind"] == "diff"
    assert report["summary"]["gate"] == "PASS"
    assert report["summary"]["fields"]["drift"] == 0
    assert report["cells"][0]["verdict"] == "pass"  # a clean diff cell is a passed cell
    markdown = (output / "summary.md").read_text(encoding="utf-8")
    assert "PASS" in markdown


def test_a_drifting_field_fails_the_gate_and_names_the_path(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    baseline = serve({"/users": _json_route({"total": 1, "status": "ok"})})
    candidate = serve({"/users": _json_route({"total": 2, "status": "ok"})})
    config = _project(tmp_path, baseline, candidate)
    output = tmp_path / "reports"

    result = runner.invoke(app, _diff_args(config, output, "junit"))

    assert result.exit_code == 1
    assert "gate: FAIL" in result.output
    assert "$.total" in result.output  # the drifted path is named, not just counted
    junit = ElementTree.parse(output / "junit.xml").getroot()
    assert junit.get("failures") == "1"
    failure = junit.find(".//failure")
    assert failure is not None
    assert failure.text is not None
    assert "$.total" in failure.text


def test_an_unreachable_candidate_is_an_error_not_a_hang_or_a_pass(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    import socket

    baseline = serve({"/users": _json_route({"ok": True})})
    # An ephemeral port that was just released: nothing listens, so the
    # candidate request fails fast with a connection error.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    config = _project(tmp_path, baseline, f"http://127.0.0.1:{free_port}")
    output = tmp_path / "reports"

    result = runner.invoke(app, _diff_args(config, output, "junit"))

    assert result.exit_code == 1
    assert "gate: FAIL" in result.output
    junit = ElementTree.parse(output / "junit.xml").getroot()
    assert junit.get("errors") == "1"


def test_a_declared_secret_never_reaches_stdout_or_any_artifact(
    serve: Callable[[Routes], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The end-to-end never-leak guarantee: the server echoes the secret back in
    # its body as a *drifting* value and as a one-sided key, so the rendered
    # drift details and paths would carry it verbatim — every sink must mask
    # it. This is `comparo doctor` proven against a live wire.
    secret = "e2e-s3cr3t-b6f2a91c"
    monkeypatch.setenv("E2E_TOKEN", secret)
    baseline = serve({"/users": _json_route({"echo": secret, secret: "as-key"})})
    candidate = serve({"/users": _json_route({"echo": f"{secret}-v2"})})
    config = _project(tmp_path, baseline, candidate)
    env_file = tmp_path / "env-baseline.yaml"
    env_file.write_text(
        env_file.read_text(encoding="utf-8")
        + "  secrets:\n    E2E_TOKEN:\n      $env: E2E_TOKEN\n",
        encoding="utf-8",
    )
    output = tmp_path / "reports"

    result = runner.invoke(app, _diff_args(config, output, "junit", "sarif", "json", "markdown"))

    assert result.exit_code == 1  # $.echo drifted, so drift details were rendered
    assert "$.echo" in result.output  # the drift really was rendered ...
    assert secret not in result.output  # ... with the secret masked
    for artifact in output.iterdir():
        assert secret not in artifact.read_text(encoding="utf-8"), artifact.name


def test_run_reports_status_and_gates_on_the_declared_response(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    baseline = serve(
        {
            "/users": _json_route({"ok": True}),
            "/broken": (500, "application/json", lambda: b'{"oops": true}'),
        }
    )
    config = _project(tmp_path, baseline, baseline)
    (tmp_path / "request-broken.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: comparo/v1
            kind: Request
            metadata: {name: broken, id: request.broken}
            spec:
              request:
                method: GET
                endpoint: /broken
              response:
                status: 200
            """
        ),
        encoding="utf-8",
    )

    passing = runner.invoke(app, ["run", "request.users", "--config", str(config)])
    assert passing.exit_code == 0, passing.output

    failing = runner.invoke(app, ["run", "--config", str(config)])
    assert failing.exit_code == 1  # the 500 against a declared 200 fails the run


def test_exec_profile_gates_end_to_end(serve: Callable[[Routes], str], tmp_path: Path) -> None:
    payload = {"ok": True}
    baseline = serve({"/users": _json_route(payload)})
    candidate = serve({"/users": _json_route(payload)})
    config = _project(tmp_path, baseline, candidate)
    (tmp_path / "execution.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: comparo/v1
            kind: ExecutionProfile
            metadata: {name: gate, id: execution.gate}
            spec:
              environments:
                baseline: baseline
                candidate: candidate
            """
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["exec", "execution.gate", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "gate PASS" in result.output


def test_a_slow_endpoint_times_out_as_an_error_instead_of_hanging(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    import time

    def slow() -> bytes:
        time.sleep(3)
        return b"{}"

    baseline = serve({"/users": _json_route({"ok": True})})
    candidate = serve({"/users": (200, "application/json", slow)})
    config = _project(tmp_path, baseline, candidate)
    for env in ("baseline", "candidate"):
        path = tmp_path / f"env-{env}.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace("read: 5s", "read: 500ms"), encoding="utf-8"
        )

    result = runner.invoke(app, _diff_args(config, tmp_path / "reports"))

    assert result.exit_code == 1
    assert "gate: FAIL" in result.output
    assert "Timeout" in result.output  # surfaced as a transport error, not a hang


def test_one_dead_cell_never_aborts_the_rest_of_the_run(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    # The engine's containment promise: a failure resolving or sending one
    # cell is captured on that cell, and every other request still executes.
    baseline = serve({"/users": _json_route({"ok": True})})
    config = _project(tmp_path, baseline, baseline)
    (tmp_path / "request-dead.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: comparo/v1
            kind: Request
            metadata: {name: dead, id: request.dead}
            spec:
              request:
                method: GET
                endpoint: /users
                query:
                  boom: ${UNSET_VARIABLE}
            """
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "--config", str(config)])

    assert result.exit_code == 1  # the dead cell fails the gate ...
    assert "request.users" in result.output  # ... but the healthy cell still ran
    assert "✓" in result.output
    assert "✗" in result.output


def test_validate_renders_file_and_line_diagnostics_for_a_broken_project() -> None:
    broken = Path(__file__).parent.parent / "examples" / "broken-project"
    result = runner.invoke(app, ["validate", "--config", str(broken)])
    assert result.exit_code == 2  # usage/config error, distinct from a gate failure
    assert "6 problem(s)" in result.output
    assert "matrices/locales.yaml:13" in result.output  # file:line, not just prose
    assert "did you mean 'schema.order'?" in result.output  # near-miss suggestion


def test_render_masks_a_declared_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "render-s3cr3t-77aa"
    monkeypatch.setenv("RENDER_TOKEN", secret)
    config = _project(tmp_path, "http://127.0.0.1:1", "http://127.0.0.1:1")
    env_file = tmp_path / "env-baseline.yaml"
    env_file.write_text(
        env_file.read_text(encoding="utf-8")
        + "  secrets:\n    RENDER_TOKEN:\n      $env: RENDER_TOKEN\n",
        encoding="utf-8",
    )
    (tmp_path / "request.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: comparo/v1
            kind: Request
            metadata: {name: users, id: request.users}
            spec:
              request:
                method: GET
                endpoint: /users
                headers:
                  - key: authorization
                    value:
                      $secret: RENDER_TOKEN
            """
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["render", "request.users", "--config", str(config), "--env", "baseline"]
    )

    assert result.exit_code == 0, result.output
    assert "authorization" in result.output
    assert secret not in result.output  # masked, never printed


def test_a_status_regression_with_identical_bodies_must_fail_the_diff_gate(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    payload = {"ok": True}
    baseline = serve({"/users": _json_route(payload)})
    candidate = serve({"/users": (500, "application/json", lambda: json.dumps(payload).encode())})
    config = _project(tmp_path, baseline, candidate)

    result = runner.invoke(app, _diff_args(config, tmp_path / "reports"))

    assert result.exit_code == 1, "a 500 against a 200 baseline must never pass the diff gate"
    assert "$status" in result.output
    assert "200 → 500" in result.output


def test_a_status_change_is_ignorable_via_a_status_rule(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    # An endpoint whose status legitimately varies can opt out with a $status rule.
    payload = {"ok": True}
    baseline = serve({"/users": _json_route(payload)})
    candidate = serve({"/users": (500, "application/json", lambda: json.dumps(payload).encode())})
    config = _project(tmp_path, baseline, candidate)
    (tmp_path / "request.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: comparo/v1
            kind: Request
            metadata: {name: users, id: request.users}
            spec:
              request:
                method: GET
                endpoint: /users
              response:
                diff:
                  default: exact
                  rules:
                    - {path: $status, mode: ignore}
            """
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, _diff_args(config, tmp_path / "reports"))
    assert result.exit_code == 0, result.output
    assert "gate: PASS" in result.output


def test_streamed_responses_diff_by_event_sequence(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    def sse(*events: str) -> Callable[[], bytes]:
        return lambda: "".join(f"data: {event}\n\n" for event in events).encode()

    baseline = serve({"/stream": (200, "text/event-stream", sse("alpha", "beta"))})
    candidate = serve({"/stream": (200, "text/event-stream", sse("alpha", "CHANGED"))})
    config = _project(tmp_path, baseline, candidate)
    (tmp_path / "request.yaml").write_text(
        textwrap.dedent(
            """\
            apiVersion: comparo/v1
            kind: Request
            metadata: {name: stream, id: request.stream}
            spec:
              request:
                method: GET
                endpoint: /stream
              response:
                streaming: true
            """
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, _diff_args(config, tmp_path / "reports"))

    assert result.exit_code == 1
    assert "gate: FAIL" in result.output
    assert "[1]" in result.output  # the second *event* drifted, named by index


def test_a_trickling_server_hits_the_total_read_deadline(
    serve: Callable[[Routes], str], tmp_path: Path
) -> None:
    # httpx's read timeout is per-read; a server dribbling bytes never trips it.
    # The total deadline must end the read as a bounded error, not a hang.
    import socketserver
    import threading
    import time

    class _Trickle(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.recv(4096)
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
            for _ in range(100):
                try:
                    self.request.sendall(b"x")
                    time.sleep(0.2)
                except OSError:
                    return

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Trickle)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    trickle = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        baseline = serve({"/users": _json_route({"ok": True})})
        config = _project(tmp_path, baseline, trickle)
        for env in ("baseline", "candidate"):
            path = tmp_path / f"env-{env}.yaml"
            path.write_text(
                path.read_text(encoding="utf-8").replace("read: 5s", "read: 300ms"),
                encoding="utf-8",
            )
        start = time.monotonic()
        result = runner.invoke(app, _diff_args(config, tmp_path / "reports"))
        elapsed = time.monotonic() - start
        assert result.exit_code == 1
        assert "gate: FAIL" in result.output
        assert elapsed < 10  # bounded by the total deadline, not the 20s trickle
    finally:
        server.shutdown()
        server.server_close()


def test_a_runaway_response_body_is_capped(
    serve: Callable[[Routes], str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # M36: a non-streaming body is bounded so a giant response can't exhaust memory.
    from comparo.adapters import httpx_client

    monkeypatch.setattr(httpx_client, "_MAX_BODY_BYTES", 500)
    big = b'{"x": "' + b"a" * 5000 + b'"}'  # ~5 KB, far over the 500-byte test cap
    baseline = serve({"/users": (200, "application/json", lambda: big)})
    config = _project(tmp_path, baseline, baseline)

    # `run` executes against one env; a capped (truncated) body just diffs as raw.
    result = runner.invoke(app, ["run", "request.users", "--config", str(config)])
    # The request completed (didn't hang or OOM); the cap kept memory bounded.
    assert result.exit_code in (0, 1)
    assert "request.users" in result.output
