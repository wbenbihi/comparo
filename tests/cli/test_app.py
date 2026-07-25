"""Functional tests for the Cyclopts and Rich process boundary."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from io import BytesIO
from io import StringIO
from pathlib import Path
from typing import TextIO
from typing import cast
from typing import override

import pytest
from cyclopts import App

from comparo.cli import app as app_module
from comparo.cli import generate_cli_reference
from comparo.cli import main
from comparo.cli.contracts import ArtifactBlobRequest
from comparo.cli.contracts import ArtifactReadRequest
from comparo.cli.contracts import CliColorMode
from comparo.cli.contracts import CliCommandResult
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliMessage
from comparo.cli.contracts import CliMessageLevel
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import CliReportFormat
from comparo.cli.contracts import DoctorRequest
from comparo.cli.contracts import ExecutionRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import RenderRequest
from comparo.cli.contracts import ReportRequest
from comparo.cli.contracts import ValidateRequest
from comparo.cli.services import CliServices
from comparo.tui.contracts import TuiLaunchRequest

_SECRET_TOKEN = "malformed-super-secret-value"


def _human_result(exit_code: CliExitCode = CliExitCode.SUCCESS) -> CliCommandResult:
    level = CliMessageLevel.SUCCESS if exit_code is CliExitCode.SUCCESS else CliMessageLevel.ERROR
    return CliCommandResult(
        exit_code=exit_code,
        payload_kind=CliPayloadKind.HUMAN,
        messages=(CliMessage(level=level, text="static result"),),
    )


class _StaticServices:
    """Small fully typed service fake for adapter tests."""

    def __init__(self, result: CliCommandResult | None = None) -> None:
        self.result = _human_result() if result is None else result
        self.validate_request: ValidateRequest | None = None
        self.doctor_request: DoctorRequest | None = None
        self.plan_request: PlanRequest | None = None
        self.execution_request: ExecutionRequest | None = None
        self.artifact_inspect_request: ArtifactReadRequest | None = None
        self.artifact_verify_request: ArtifactReadRequest | None = None
        self.artifact_blob_request: ArtifactBlobRequest | None = None
        self.report_request: ReportRequest | None = None
        self.render_request: RenderRequest | None = None

    def configuration_schema(self) -> CliCommandResult:
        return self.result

    def artifact_schema(self) -> CliCommandResult:
        return self.result

    def report_schema(self) -> CliCommandResult:
        return self.result

    def policy_schema(self) -> CliCommandResult:
        return self.result

    def validate(self, request: ValidateRequest) -> CliCommandResult:
        self.validate_request = request
        return self.result

    def doctor(self, request: DoctorRequest) -> CliCommandResult:
        self.doctor_request = request
        return self.result

    def plan(self, request: PlanRequest) -> CliCommandResult:
        self.plan_request = request
        return self.result

    def execute(self, request: ExecutionRequest) -> CliCommandResult:
        self.execution_request = request
        return self.result

    def render(self, request: RenderRequest) -> CliCommandResult:
        self.render_request = request
        return self.result

    def artifact_inspect(self, request: ArtifactReadRequest) -> CliCommandResult:
        self.artifact_inspect_request = request
        return self.result

    def artifact_verify(self, request: ArtifactReadRequest) -> CliCommandResult:
        self.artifact_verify_request = request
        return self.result

    def artifact_blob(self, request: ArtifactBlobRequest) -> CliCommandResult:
        self.artifact_blob_request = request
        return self.result

    def report(self, request: ReportRequest) -> CliCommandResult:
        self.report_request = request
        return self.result


class _FailingServices(_StaticServices):
    """Service fake that raises one process-boundary failure."""

    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    @override
    def validate(self, request: ValidateRequest) -> CliCommandResult:
        del request
        raise self.failure


class _InvalidApp:
    """Callable fake that violates the Cyclopts callback-result contract."""

    def __call__(self, tokens: tuple[str, ...], **kwargs: object) -> object:
        del tokens, kwargs
        return object()


def _invoke(
    arguments: list[str],
    *,
    services: CliServices | None = None,
) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, services=services, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _invoke_binary(
    arguments: list[str],
    *,
    services: CliServices,
) -> tuple[int, str, str, bytes]:
    stdout = StringIO()
    stderr = StringIO()
    binary = BytesIO()
    code = main(
        arguments,
        services=services,
        stdout=stdout,
        stderr=stderr,
        binary_stdout=binary,
    )
    return code, stdout.getvalue(), stderr.getvalue(), binary.getvalue()


class _TerminalBytes(BytesIO):
    """Binary test sink representing an interactive terminal."""

    @override
    def isatty(self) -> bool:
        return True


class _TerminalText(StringIO):
    """In-memory text stream representing an interactive terminal."""

    @override
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("arguments", [[], ["--help"]])
def test_root_help_is_generated_without_running_services(arguments: list[str]) -> None:
    code, stdout, stderr = _invoke(arguments, services=_StaticServices())
    assert code == 0
    assert "Usage: comparo COMMAND" in stdout
    assert "schema" in stdout
    assert "validate" in stdout
    assert "artifact" in stdout
    assert "report" in stdout
    assert "run" in stdout
    assert "diff" in stdout
    assert "exec" in stdout
    assert "render" in stdout
    assert "tui" in stdout
    assert stderr == ""


def test_explicit_tui_command_builds_a_typed_launch_request() -> None:
    captured: list[TuiLaunchRequest] = []

    def launch(request: TuiLaunchRequest) -> int:
        captured.append(request)
        return 0

    stdout = StringIO()
    stderr = StringIO()
    code = main(
        [
            "tui",
            "--config",
            "project.yaml",
            "--source",
            "one.yaml",
            "--source",
            "two.yaml",
            "--policy",
            "policy.yaml",
            "--artifact-root",
            "reports",
        ],
        services=cast("CliServices", _StaticServices()),
        stdout=stdout,
        stderr=stderr,
        tui_launcher=launch,
    )
    assert code == 0
    assert captured == [
        TuiLaunchRequest(
            config=Path("project.yaml"),
            additional_sources=(Path("one.yaml"), Path("two.yaml")),
            policy=Path("policy.yaml"),
            artifact_root=Path("reports"),
        )
    ]
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == ""


def test_tui_command_threads_caller_owned_limits_into_one_launch_contract() -> None:
    captured: list[TuiLaunchRequest] = []

    def launch(request: TuiLaunchRequest) -> int:
        captured.append(request)
        return 0

    stdout = StringIO()
    stderr = StringIO()
    code = main(
        [
            "tui",
            "--maximum-config-sources",
            "17",
            "--maximum-encoded-body-bytes",
            "1234",
            "--maximum-report-entries",
            "23",
            "--maximum-outstanding-preparations",
            "3",
        ],
        services=cast("CliServices", _StaticServices()),
        stdout=stdout,
        stderr=stderr,
        tui_launcher=launch,
    )
    assert code == 0
    limits = captured[0].limits
    assert limits.project_load.maximum_sources == 17
    assert limits.execution_settings.transport.capture.maximum_encoded_body_bytes.value == 1234
    assert limits.report_archive.maximum_entries == 23
    assert limits.maximum_outstanding_preparations == 3
    assert limits.execution_settings.artifact.persist.value == "always"
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == ""


@pytest.mark.parametrize(
    "arguments",
    [
        ["--maximum-config-sources", "0"],
        ["--maximum-blobs", "0"],
        ["--maximum-report-entries", "0"],
        ["--maximum-outstanding-preparations", "0"],
    ],
)
def test_tui_command_rejects_invalid_limits_without_launching(arguments: list[str]) -> None:
    launched: list[TuiLaunchRequest] = []

    def launch(request: TuiLaunchRequest) -> int:
        launched.append(request)
        return 0

    stdout = StringIO()
    stderr = StringIO()
    code = main(
        ["tui", *arguments],
        services=cast("CliServices", _StaticServices()),
        stdout=stdout,
        stderr=stderr,
        tui_launcher=launch,
    )
    assert code == 2
    assert launched == []
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "Invalid command invocation. Run with --help.\n"


def test_empty_interactive_invocation_opens_tui_but_redirected_invocation_keeps_help() -> None:
    launched: list[TuiLaunchRequest] = []

    def launch(request: TuiLaunchRequest) -> int:
        launched.append(request)
        return 0

    terminal_input = _TerminalText()
    terminal_output = _TerminalText()
    terminal_error = _TerminalText()
    code = main(
        [],
        services=cast("CliServices", _StaticServices()),
        stdin=terminal_input,
        stdout=terminal_output,
        stderr=terminal_error,
        tui_launcher=launch,
    )
    assert code == 0
    assert launched == [TuiLaunchRequest()]
    assert terminal_output.getvalue() == ""
    assert terminal_error.getvalue() == ""

    redirected = _invoke([], services=cast("CliServices", _StaticServices()))
    assert redirected[0] == 0
    assert "Usage: comparo COMMAND" in redirected[1]


@pytest.mark.parametrize(
    ("launcher", "code", "message"),
    [
        (
            lambda _request: (_ for _ in ()).throw(RuntimeError(_SECRET_TOKEN)),
            4,
            "Comparo could not start the terminal interface safely.",
        ),
        (lambda _request: 256, 4, "Comparo received an invalid terminal exit status."),
        (
            lambda _request: (_ for _ in ()).throw(KeyboardInterrupt()),
            130,
            "Interrupted.",
        ),
    ],
)
def test_tui_launcher_failures_are_stable_and_value_free(
    launcher: Callable[[TuiLaunchRequest], int],
    code: int,
    message: str,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    result = main(
        ["tui"],
        services=cast("CliServices", _StaticServices()),
        stdout=stdout,
        stderr=stderr,
        tui_launcher=launcher,
    )
    assert result == code
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == f"{message}\n"
    assert _SECRET_TOKEN not in stderr.getvalue()


def test_default_tui_launcher_imports_the_terminal_adapter_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from comparo.tui import launch as launch_module

    request = TuiLaunchRequest()
    monkeypatch.setattr(launch_module, "run_tui", lambda received: 7 if received is request else 4)
    assert app_module._default_tui_launcher(request) == 7


@pytest.mark.parametrize("command", ["run", "diff", "exec"])
def test_execution_help_explains_shared_controls_and_security_defaults(command: str) -> None:
    code, stdout, stderr = _invoke([command, "--help"], services=_StaticServices())
    help_text = " ".join(stdout.replace("│", " ").split())
    assert code == 0
    assert "Project manifest or directory containing comparo.yaml." in help_text
    assert "permissive built-in trusted-local authority" in help_text
    assert "Approve requests classified as mutating" in help_text
    assert "Persist a sanitized artifact bundle; disabled by default." in help_text
    assert "Maximum aggregate bytes retained" in help_text
    assert "Maximum distinct HTTP authorization" in help_text
    assert "occurrences retained per run." in help_text
    assert "Maximum aggregate blob bytes admitted" in help_text
    assert "Shared planning, policy, persistence" not in help_text
    assert stderr == ""


def test_plan_and_render_help_disclose_authority_and_specific_render_limits() -> None:
    plan = _invoke(["plan", "--help"], services=_StaticServices())
    render = _invoke(["render", "--help"], services=_StaticServices())
    plan_help = " ".join(plan[1].replace("│", " ").split())
    render_help = " ".join(render[1].replace("│", " ").split())
    assert plan[0] == render[0] == 0
    assert "permissive built-in trusted-local authority" in plan_help
    assert "permissive built-in trusted-local authority" in render_help
    assert "Maximum bytes read from one project file value." in render_help
    assert "Maximum bytes emitted by the complete rendered preview." in render_help
    assert "Version-owned local-source" not in render_help
    assert plan[2] == render[2] == ""


def test_version_comes_from_installed_distribution() -> None:
    code, stdout, stderr = _invoke(["--version"])
    assert code == 0
    assert stdout.strip() == "0.2.0"
    assert stderr == ""


def test_missing_distribution_metadata_has_a_safe_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(distribution: str) -> str:
        del distribution
        raise PackageNotFoundError

    monkeypatch.setattr(app_module, "version", missing)
    assert app_module._package_version() == "unknown"


@pytest.mark.parametrize(
    ("target", "schema_file"),
    [
        ("config", "comparo-v1alpha1.schema.json"),
        ("artifact", "comparo-artifact-v1alpha1.schema.json"),
        ("report", "comparo-report-v1alpha1.schema.json"),
    ],
)
def test_schema_commands_emit_exact_checked_in_json(target: str, schema_file: str) -> None:
    code, stdout, stderr = _invoke(["schema", target])
    expected = Path(__file__).parents[2] / "src" / "comparo" / "schemas" / schema_file
    assert code == 0
    assert stdout == expected.read_text(encoding="utf-8")
    assert stderr == ""


def test_policy_schema_emits_exact_checked_in_json() -> None:
    code, stdout, stderr = _invoke(["schema", "policy"])
    expected = (
        Path(__file__).parents[2]
        / "src"
        / "comparo"
        / "schemas"
        / "comparo-policy-v1alpha1.schema.json"
    )
    assert code == 0
    assert stdout == expected.read_text(encoding="utf-8")
    assert stderr == ""


@pytest.mark.parametrize("output_format", ["markdown", "html", "rst"])
def test_cli_reference_is_generated_by_api_and_command(output_format: str) -> None:
    api_output = generate_cli_reference(cast("app_module.DocumentationFormat", output_format))
    code, command_output, stderr = _invoke(["schema", "cli", output_format])
    assert code == 0
    assert command_output == api_output
    assert "comparo" in api_output
    assert stderr == ""


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_is_static_safe_and_does_not_modify_shell_files(shell: str) -> None:
    code, stdout, stderr = _invoke(["completion", shell])
    assert code == 0
    assert "comparo" in stdout
    assert "--empty-source" not in stdout
    assert _SECRET_TOKEN not in stdout
    assert "python" not in stdout.lower()
    assert stderr == ""


def test_validate_callback_preserves_repeated_source_order_and_aliases() -> None:
    services = _StaticServices()
    code, stdout, stderr = _invoke(
        [
            "validate",
            "-C",
            "project.yaml",
            "-S",
            "first.yaml",
            "--source",
            "second.yaml",
            "--format",
            "json",
        ],
        services=services,
    )
    assert code == 0
    assert stdout == "static result\n"
    assert stderr == ""
    assert services.validate_request is not None
    assert services.validate_request.config == Path("project.yaml")
    assert services.validate_request.additional_sources == (
        Path("first.yaml"),
        Path("second.yaml"),
    )


@pytest.mark.parametrize(
    ("command", "request_kind"),
    [
        (["validate"], "validate"),
        (["plan"], "plan"),
        (["render"], "render"),
        (["run"], "execution"),
        (["diff"], "execution"),
        (["exec", "release"], "execution"),
    ],
)
def test_every_project_loading_journey_preserves_configuration_limits(
    command: list[str],
    request_kind: str,
) -> None:
    services = _StaticServices()
    limits = [
        "--maximum-config-sources",
        "2",
        "--maximum-config-yaml-bytes",
        "3",
        "--maximum-config-yaml-documents",
        "5",
        "--maximum-config-yaml-depth",
        "7",
        "--maximum-config-yaml-nodes",
        "11",
        "--maximum-policy-yaml-bytes",
        "13",
        "--maximum-policy-yaml-depth",
        "17",
        "--maximum-policy-yaml-nodes",
        "19",
    ]
    assert _invoke([*command, *limits], services=services) == (0, "static result\n", "")
    if request_kind == "validate":
        assert services.validate_request is not None
        load_limits = services.validate_request.load_limits
    elif request_kind == "plan":
        assert services.plan_request is not None
        load_limits = services.plan_request.load_limits
    elif request_kind == "render":
        assert services.render_request is not None
        load_limits = services.render_request.plan.load_limits
    else:
        assert services.execution_request is not None
        load_limits = services.execution_request.plan.load_limits
    assert tuple(getattr(load_limits, field) for field in load_limits.__struct_fields__) == (
        2,
        3,
        5,
        7,
        11,
        13,
        17,
        19,
    )


@pytest.mark.parametrize(
    "command",
    [
        ["validate"],
        ["plan"],
        ["render"],
        ["run"],
        ["diff"],
        ["exec"],
    ],
)
def test_every_project_loading_help_documents_configuration_limits(command: list[str]) -> None:
    code, stdout, stderr = _invoke([*command, "--help"], services=_StaticServices())
    assert (code, stderr) == (0, "")
    for option in (
        "--maximum-config-sources",
        "--maximum-config-yaml-bytes",
        "--maximum-config-yaml-depth",
        "--maximum-config-yaml-nodes",
        "--maximum-policy-yaml-bytes",
        "--maximum-policy-yaml-depth",
        "--maximum-policy-yaml-nodes",
    ):
        assert option in stdout
    assert "--maximum-config-yaml-docume" in stdout
    assert "Maximum project YAML sources discovered and" in stdout
    assert "Maximum documents admitted from one project" in stdout


@pytest.mark.parametrize(
    ("command", "request_kind"),
    [
        (["plan"], "plan"),
        (["render"], "render"),
        (["run"], "execution"),
        (["diff"], "execution"),
        (["exec", "release"], "execution"),
    ],
)
def test_every_planning_journey_preserves_value_resource_operands(
    command: list[str],
    request_kind: str,
) -> None:
    services = _StaticServices()
    operands = [
        "--parameters-from",
        "shared-params",
        "--parameters-from",
        "dev=Value/dev-params",
        "--variables-from",
        "shared-vars",
        "--secrets-from",
        "prod=Value/prod-secrets",
    ]
    assert _invoke([*command, *operands], services=services) == (0, "static result\n", "")
    if request_kind == "plan":
        assert services.plan_request is not None
        plan = services.plan_request
    elif request_kind == "render":
        assert services.render_request is not None
        plan = services.render_request.plan
    else:
        assert services.execution_request is not None
        plan = services.execution_request.plan
    assert plan.parameters_from == ("shared-params", "dev=Value/dev-params")
    assert plan.variables_from == ("shared-vars",)
    assert plan.secrets_from == ("prod=Value/prod-secrets",)


@pytest.mark.parametrize(
    "command",
    [["plan"], ["render"], ["run"], ["diff"], ["exec"]],
)
def test_every_planning_help_documents_value_resource_options(command: list[str]) -> None:
    code, stdout, stderr = _invoke([*command, "--help"], services=_StaticServices())
    assert (code, stderr) == (0, "")
    for option in ("--parameters-from", "--variables-from", "--secrets-from"):
        assert option in stdout
    assert "[ENV=]VALUE" in stdout
    assert "Value resource" in stdout


def test_doctor_callback_builds_framework_neutral_request() -> None:
    services = _StaticServices()
    code, stdout, stderr = _invoke(["doctor", "--format", "json"], services=services)
    assert code == 0
    assert stdout == "static result\n"
    assert stderr == ""
    assert services.doctor_request is not None
    assert services.doctor_request.output.value == "json"


def test_plan_callback_preserves_all_frontend_inputs_without_interpreting_them() -> None:
    services = _StaticServices()
    code, stdout, stderr = _invoke(
        [
            "plan",
            "-C",
            "project.yaml",
            "-S",
            "extra.yaml",
            "--policy",
            "operator.yaml",
            "--profile",
            "release",
            "--subject",
            "ping",
            "--subject",
            "health",
            "--env",
            "staging",
            "--baseline",
            "staging",
            "--candidate",
            "prod",
            "--matrix",
            "region=regions",
            "--trials",
            "2",
            "--maximum-attempts",
            "3",
            "--no-fail-fast",
            "--budget",
            "requests=4",
            "--format",
            "json",
        ],
        services=services,
    )
    assert (code, stdout, stderr) == (0, "static result\n", "")
    request = services.plan_request
    assert request is not None
    assert request.config == Path("project.yaml")
    assert request.additional_sources == (Path("extra.yaml"),)
    assert request.policy == Path("operator.yaml")
    assert request.execution_profile == "release"
    assert request.subjects == ("ping", "health")
    assert request.environments == ("staging",)
    assert request.baseline == "staging"
    assert request.candidates == ("prod",)
    assert request.matrices == ("region=regions",)
    assert request.trials == 2
    assert request.maximum_attempts == 3
    assert request.fail_fast is False
    assert request.budgets == ("requests=4",)
    assert request.output is CliOutputMode.JSON


def test_run_callback_preserves_shared_options_and_every_execution_limit() -> None:
    services = _StaticServices()
    arguments = [
        "run",
        "-C",
        "project.yaml",
        "-S",
        "extra.yaml",
        "--policy",
        "operator.yaml",
        "--subject",
        "ping",
        "--env",
        "staging",
        "--environment",
        "prod",
        "--matrix",
        "region=regions",
        "--trials",
        "2",
        "--maximum-attempts",
        "3",
        "--no-fail-fast",
        "--budget",
        "requests=4",
        "--approve-mutations",
        "--persist",
        "--artifact-root",
        "artifacts",
        "--format",
        "json",
        "--color",
        "never",
        "--body-memory-threshold-bytes",
        "2",
        "--maximum-transient-body-bytes",
        "43",
        "--maximum-authorization-occurrences",
        "47",
        "--maximum-encoded-body-bytes",
        "41",
        "--maximum-entity-body-bytes",
        "41",
        "--maximum-expansion-ratio",
        "3",
        "--maximum-stream-records",
        "5",
        "--maximum-stream-controls",
        "7",
        "--maximum-stream-errors",
        "11",
        "--maximum-stream-item-bytes",
        "13",
        "--maximum-decoded-body-bytes",
        "17",
        "--maximum-text-characters",
        "19",
        "--maximum-json-depth",
        "23",
        "--maximum-json-nodes",
        "29",
        "--maximum-json-tokens",
        "31",
        "--maximum-blobs",
        "7",
        "--maximum-blob-bytes",
        "11",
        "--maximum-total-blob-bytes",
        "13",
        "--maximum-manifest-bytes",
        "17",
        "--maximum-report-bytes",
        "19",
        "--maximum-redaction-patterns",
        "23",
        "--maximum-redaction-pattern-bytes",
        "29",
        "--maximum-redaction-value-bytes",
        "31",
        "--maximum-redaction-value-nodes",
        "37",
    ]
    assert _invoke(arguments, services=services) == (0, "static result\n", "")
    request = services.execution_request
    assert request is not None
    assert request.command.value == "run"
    assert request.plan.config == Path("project.yaml")
    assert request.plan.additional_sources == (Path("extra.yaml"),)
    assert request.plan.policy == Path("operator.yaml")
    assert request.plan.subjects == ("ping",)
    assert request.plan.environments == ("staging", "prod")
    assert request.plan.baseline is None
    assert request.plan.candidates == ()
    assert request.plan.matrices == ("region=regions",)
    assert request.plan.trials == 2
    assert request.plan.maximum_attempts == 3
    assert request.plan.fail_fast is False
    assert request.plan.budgets == ("requests=4",)
    assert request.plan.output is CliOutputMode.JSON
    assert request.approve_mutations
    assert request.persist
    assert request.artifact_root == Path("artifacts")
    assert request.tool_version == "0.2.0"
    runtime = request.limits
    assert tuple(
        getattr(runtime, field) for field in runtime.__struct_fields__ if field != "artifact"
    ) == (2, 43, 47, 41, 41, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31)
    assert tuple(
        getattr(runtime.artifact, field) for field in runtime.artifact.__struct_fields__
    ) == (7, 11, 13, 17, 19, 23, 29, 31, 37)


def test_diff_and_exec_callbacks_select_topology_without_interpreting_it() -> None:
    services = _StaticServices()
    assert _invoke(
        ["diff", "--baseline", "staging", "--candidate", "prod"],
        services=services,
    ) == (0, "static result\n", "")
    diff = services.execution_request
    assert diff is not None
    assert diff.command.value == "diff"
    assert diff.plan.baseline == "staging"
    assert diff.plan.candidates == ("prod",)
    assert not diff.persist

    assert _invoke(
        [
            "exec",
            "release",
            "--env",
            "dev",
            "--baseline",
            "staging",
            "--candidate",
            "prod",
            "--no-persist",
        ],
        services=services,
    ) == (0, "static result\n", "")
    profile = services.execution_request
    assert profile is not None
    assert profile.command.value == "exec"
    assert profile.plan.execution_profile == "release"
    assert profile.plan.environments == ("dev",)
    assert profile.plan.baseline == "staging"
    assert profile.plan.candidates == ("prod",)
    assert not profile.persist


def test_render_callback_preserves_planning_and_every_offline_limit() -> None:
    services = _StaticServices()
    arguments = [
        "render",
        "-C",
        "project.yaml",
        "-S",
        "extra.yaml",
        "--policy",
        "operator.yaml",
        "--profile",
        "release",
        "--subject",
        "ping",
        "--env",
        "dev",
        "--baseline",
        "staging",
        "--candidate",
        "prod",
        "--matrix",
        "region=regions",
        "--trials",
        "2",
        "--maximum-attempts",
        "3",
        "--no-fail-fast",
        "--budget",
        "requests=4",
        "--format",
        "json",
        "--color",
        "never",
        "--maximum-file-bytes",
        "2",
        "--maximum-file-path-bytes",
        "3",
        "--maximum-file-segments",
        "5",
        "--maximum-environment-files",
        "7",
        "--maximum-environment-file-bytes",
        "11",
        "--maximum-environment-total-bytes",
        "13",
        "--maximum-environment-entries",
        "17",
        "--maximum-environment-line-bytes",
        "19",
        "--maximum-yaml-bytes",
        "23",
        "--maximum-yaml-depth",
        "29",
        "--maximum-yaml-nodes",
        "31",
        "--maximum-redaction-patterns",
        "37",
        "--maximum-redaction-pattern-bytes",
        "41",
        "--maximum-redaction-value-bytes",
        "43",
        "--maximum-redaction-value-nodes",
        "47",
        "--maximum-output-bytes",
        "53",
    ]
    assert _invoke(arguments, services=services) == (0, "static result\n", "")
    request = services.render_request
    assert request is not None
    plan = request.plan
    assert plan.config == Path("project.yaml")
    assert plan.additional_sources == (Path("extra.yaml"),)
    assert plan.policy == Path("operator.yaml")
    assert plan.execution_profile == "release"
    assert plan.subjects == ("ping",)
    assert plan.environments == ("dev",)
    assert plan.baseline == "staging"
    assert plan.candidates == ("prod",)
    assert plan.matrices == ("region=regions",)
    assert plan.trials == 2
    assert plan.maximum_attempts == 3
    assert plan.fail_fast is False
    assert plan.budgets == ("requests=4",)
    assert plan.output is CliOutputMode.JSON
    assert tuple(getattr(request.limits, field) for field in request.limits.__struct_fields__) == (
        2,
        3,
        5,
        7,
        11,
        13,
        17,
        19,
        23,
        29,
        31,
        37,
        41,
        43,
        47,
        53,
    )


def test_artifact_callbacks_preserve_limits_coordinates_and_output_modes() -> None:
    services = _StaticServices()
    common_limits = [
        "--maximum-blobs",
        "2",
        "--maximum-blob-bytes",
        "3",
        "--maximum-total-blob-bytes",
        "4",
        "--maximum-manifest-bytes",
        "5",
        "--maximum-report-bytes",
        "6",
        "--maximum-redaction-patterns",
        "7",
        "--maximum-redaction-pattern-bytes",
        "8",
        "--maximum-redaction-value-bytes",
        "9",
        "--maximum-redaction-value-nodes",
        "10",
    ]
    inspected = _invoke(
        [
            "artifact",
            "inspect",
            "artifact-0",
            "--root",
            "store",
            "--format",
            "json",
            *common_limits,
        ],
        services=services,
    )
    assert inspected == (0, "static result\n", "")
    request = services.artifact_inspect_request
    assert request is not None
    assert request.root == Path("store")
    assert request.bundle_id == "artifact-0"
    assert request.output is CliOutputMode.JSON
    assert tuple(getattr(request.limits, name) for name in request.limits.__struct_fields__) == (
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
    )

    assert _invoke(
        ["artifact", "verify", "artifact-1"],
        services=services,
    ) == (0, "static result\n", "")
    assert services.artifact_verify_request is not None
    assert services.artifact_verify_request.bundle_id == "artifact-1"

    assert _invoke(
        [
            "artifact",
            "blob",
            "artifact-2",
            "a" * 64,
            "--root",
            "store",
            "--maximum-output-bytes",
            "11",
        ],
        services=services,
    ) == (0, "static result\n", "")
    blob = services.artifact_blob_request
    assert blob is not None
    assert blob.sha256 == "a" * 64
    assert blob.maximum_output_bytes == 11

    assert _invoke(
        ["report", "artifact-3", "--root", "store", "--format", "sarif"],
        services=services,
    ) == (0, "static result\n", "")
    report = services.report_request
    assert report is not None
    assert report.format is CliReportFormat.SARIF


def test_binary_payload_uses_only_the_explicit_raw_stdout_boundary() -> None:
    result = CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.BINARY,
        payload=b"\xff\x00body",
    )
    services = _StaticServices(result)
    written = _invoke_binary(
        ["artifact", "blob", "artifact-0", "a" * 64],
        services=services,
    )
    assert written == (0, "", "", b"\xff\x00body")

    unavailable = _invoke(
        ["artifact", "blob", "artifact-0", "a" * 64],
        services=services,
    )
    assert unavailable == (4, "", "Comparo could not write binary output safely.\n")

    stdout = StringIO()
    stderr = StringIO()
    terminal_code = main(
        ["artifact", "blob", "artifact-0", "a" * 64],
        services=services,
        stdout=stdout,
        stderr=stderr,
        binary_stdout=_TerminalBytes(),
    )
    assert terminal_code == 4
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "Comparo could not write binary output safely.\n"


@pytest.mark.parametrize("exit_code", [CliExitCode.GATE_FAILED, CliExitCode.EXECUTION_INCOMPLETE])
def test_structured_service_outcomes_control_process_exit(exit_code: CliExitCode) -> None:
    code, stdout, stderr = _invoke(
        ["doctor"],
        services=_StaticServices(_human_result(exit_code)),
    )
    assert code == int(exit_code)
    assert stdout == ""
    assert stderr == "static result\n"


@pytest.mark.parametrize(
    "arguments",
    [
        [f"--{_SECRET_TOKEN}"],
        ["validate", "--format", _SECRET_TOKEN],
        ["validate", "--empty-source", _SECRET_TOKEN],
        ["validate", "--source", "expected", _SECRET_TOKEN],
    ],
)
def test_parser_failures_never_echo_raw_tokens(arguments: list[str]) -> None:
    code, stdout, stderr = _invoke(arguments)
    assert code == int(CliExitCode.INVALID_INVOCATION)
    assert stdout == ""
    assert stderr == "Invalid command invocation. Run with --help.\n"
    assert _SECRET_TOKEN not in stdout + stderr


def test_keyboard_interrupt_and_unexpected_failures_are_closed_and_value_free() -> None:
    interrupted = _invoke(
        ["validate"],
        services=cast("CliServices", _FailingServices(KeyboardInterrupt())),
    )
    failed = _invoke(
        ["validate"],
        services=cast("CliServices", _FailingServices(RuntimeError(_SECRET_TOKEN))),
    )
    assert interrupted == (130, "", "Interrupted.\n")
    assert failed == (4, "", "Comparo could not complete the command safely.\n")
    assert _SECRET_TOKEN not in failed[2]


def test_invalid_callback_result_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "_DEFAULT_APP", cast("App", _InvalidApp()))
    code, stdout, stderr = _invoke(["doctor"])
    assert code == 4
    assert stdout == ""
    assert stderr == "Comparo received an invalid command result.\n"


def test_explicit_color_precedence_and_machine_output_cleanliness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    always = _invoke(["doctor", "--color=always"], services=_StaticServices())
    never = _invoke(["doctor", "--color", "never"], services=_StaticServices())
    machine = _invoke(["doctor", "--format", "json", "--color", "always"])
    assert "\x1b[" in always[1]
    assert "\x1b[" not in never[1] + never[2]
    assert "\x1b[" not in machine[1] + machine[2]
    assert json.loads(machine[1])["status"] == "passed"


def test_invalid_color_falls_back_safely_before_parser_rejection() -> None:
    code, stdout, stderr = _invoke(
        ["doctor", "--color=malformed-super-secret-value"],
        services=_StaticServices(),
    )
    assert code == 2
    assert stdout == ""
    assert stderr == "Invalid command invocation. Run with --help.\n"
    assert "malformed-super-secret-value" not in stderr


def test_main_uses_process_arguments_and_streams_when_not_injected(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["comparo", "doctor", "--format", "json"])
    assert main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["command"] == "doctor"
    assert captured.err == ""


def test_build_app_accepts_the_protocol_without_loading_project_state() -> None:
    cli = app_module.build_app(_StaticServices())
    assert isinstance(cli, App)


def test_static_error_writer_accepts_an_injected_text_stream() -> None:
    stream: TextIO = StringIO()
    console = app_module._console(stream, CliColorMode.NEVER)
    app_module._render_static_error(console, "fixed")
    assert cast("StringIO", stream).getvalue() == "fixed\n"
