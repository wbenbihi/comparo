"""Functional tests for the Cyclopts and Rich process boundary."""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError
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
from comparo.cli.contracts import CliColorMode
from comparo.cli.contracts import CliCommandResult
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliMessage
from comparo.cli.contracts import CliMessageLevel
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import DoctorRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import ValidateRequest
from comparo.cli.services import CliServices

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


@pytest.mark.parametrize("arguments", [[], ["--help"]])
def test_root_help_is_generated_without_running_services(arguments: list[str]) -> None:
    code, stdout, stderr = _invoke(arguments, services=_StaticServices())
    assert code == 0
    assert "Usage: comparo COMMAND" in stdout
    assert "schema" in stdout
    assert "validate" in stdout
    assert stderr == ""


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
