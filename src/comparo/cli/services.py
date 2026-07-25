"""Framework-free implementations of Comparo's read-only CLI operations."""

from __future__ import annotations

import json
from typing import Protocol
from typing import final

from comparo.application.diagnostics import run_security_checks
from comparo.configuration.loader import compile_project_path
from comparo.core.artifacts.codec import render_artifact_schema
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.schema_generation import render_v1alpha1_schema
from comparo.reporters.json import encode_report_schema

from .contracts import CliCommandResult
from .contracts import CliExitCode
from .contracts import CliMessage
from .contracts import CliMessageLevel
from .contracts import CliOutputMode
from .contracts import CliPayloadKind
from .contracts import DoctorRequest
from .contracts import ValidateRequest

_CLI_API_VERSION = "comparo.cli/v1alpha1"


class CliServices(Protocol):
    """Narrow framework-neutral operations consumed by command callbacks."""

    def configuration_schema(self) -> CliCommandResult:
        """Return the public configuration schema."""

    def artifact_schema(self) -> CliCommandResult:
        """Return the public artifact schema."""

    def report_schema(self) -> CliCommandResult:
        """Return the public report schema."""

    def policy_schema(self, *, machine: bool) -> CliCommandResult:
        """Return the policy schema or a structured unavailable result."""

    def validate(self, request: ValidateRequest) -> CliCommandResult:
        """Compile a project without performing runtime effects."""

    def doctor(self, request: DoctorRequest) -> CliCommandResult:
        """Run offline security self-checks."""


def _canonical_json(value: object) -> bytes:
    """Encode deterministic UTF-8 JSON with one trailing newline."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{text}\n".encode()


def _human(
    exit_code: CliExitCode,
    *messages: tuple[CliMessageLevel, str],
) -> CliCommandResult:
    """Create an immutable human presentation from known-safe message parts."""
    return CliCommandResult(
        exit_code=exit_code,
        payload_kind=CliPayloadKind.HUMAN,
        messages=tuple(CliMessage(level=level, text=text) for level, text in messages),
    )


def _document(payload: str, kind: CliPayloadKind) -> CliCommandResult:
    """Create a successful complete document result."""
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=kind,
        payload=payload.encode(),
    )


def _configuration_schema() -> CliCommandResult:
    """Return the canonical public configuration schema."""
    return _document(render_v1alpha1_schema(), CliPayloadKind.JSON)


def _artifact_schema() -> CliCommandResult:
    """Return the canonical public run-artifact schema."""
    return _document(render_artifact_schema(), CliPayloadKind.JSON)


def _report_schema() -> CliCommandResult:
    """Return the canonical public JSON-report schema."""
    return _document(encode_report_schema(), CliPayloadKind.JSON)


def _unsupported_policy_schema(*, machine: bool) -> CliCommandResult:
    """Report the intentionally unavailable external policy schema."""
    if not machine:
        return _human(
            CliExitCode.INTERNAL_ERROR,
            (CliMessageLevel.ERROR, "The external policy schema is not available yet."),
        )
    return CliCommandResult(
        exit_code=CliExitCode.INTERNAL_ERROR,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "code": "cli.schema.policy.unsupported",
                "status": "unsupported",
            }
        ),
    )


def _diagnostic_record(diagnostic: ConfigurationDiagnostic) -> dict[str, object]:
    """Project one value-free compiler diagnostic to stable machine fields."""
    start = diagnostic.primary.start
    return {
        "code": diagnostic.code.value,
        "column": start.column.value,
        "document": start.document.value,
        "line": start.line.value,
        "severity": diagnostic.severity.value,
        "source": start.source_id.value,
    }


def _validate_project(request: ValidateRequest) -> CliCommandResult:
    """Load and compile a project without resolution, planning, or network activity."""
    loaded = compile_project_path(
        request.config,
        additional_sources=request.additional_sources,
    )
    compilation = loaded.compilation
    machine = request.output is CliOutputMode.JSON
    if compilation.project is not None:
        resource_count = len(compilation.project.resources) + 1
        if not machine:
            return _human(
                CliExitCode.SUCCESS,
                (CliMessageLevel.SUCCESS, "Configuration is valid."),
                (CliMessageLevel.INFO, f"Compiled {resource_count} resource(s)."),
            )
        return CliCommandResult(
            exit_code=CliExitCode.SUCCESS,
            payload_kind=CliPayloadKind.JSON,
            payload=_canonical_json(
                {
                    "apiVersion": _CLI_API_VERSION,
                    "command": "validate",
                    "resourceCount": resource_count,
                    "status": "valid",
                }
            ),
        )
    diagnostics = compilation.diagnostics
    if not machine:
        messages: list[tuple[CliMessageLevel, str]] = [
            (CliMessageLevel.ERROR, f"Configuration is invalid ({len(diagnostics)} issue(s)).")
        ]
        messages.extend(
            (CliMessageLevel.ERROR, f"{item.code.value} at {item.primary.start.source_id.value}")
            for item in diagnostics
        )
        return _human(CliExitCode.INVALID_INVOCATION, *messages)
    return CliCommandResult(
        exit_code=CliExitCode.INVALID_INVOCATION,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "command": "validate",
                "diagnostics": [_diagnostic_record(item) for item in diagnostics],
                "status": "invalid",
            }
        ),
    )


def _doctor_checks() -> tuple[tuple[str, bool], ...]:
    """Exercise live-value containment without publishing the canary."""
    return tuple((item.name, item.passed) for item in run_security_checks())


def _doctor(request: DoctorRequest) -> CliCommandResult:
    """Run deterministic, offline security-boundary self-checks."""
    checks = _doctor_checks()
    passed = all(result for _, result in checks)
    exit_code = CliExitCode.SUCCESS if passed else CliExitCode.INTERNAL_ERROR
    machine = request.output is CliOutputMode.JSON
    if not machine:
        messages = tuple(
            (
                CliMessageLevel.SUCCESS if result else CliMessageLevel.ERROR,
                f"{name}: {'passed' if result else 'failed'}",
            )
            for name, result in checks
        )
        return _human(exit_code, *messages)
    return CliCommandResult(
        exit_code=exit_code,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "checks": [{"name": name, "passed": result} for name, result in checks],
                "command": "doctor",
                "status": "passed" if passed else "failed",
            }
        ),
    )


@final
class DefaultCliServices:
    """Production read-only services used by the installed CLI."""

    def configuration_schema(self) -> CliCommandResult:
        """Return the canonical public configuration schema."""
        return _configuration_schema()

    def artifact_schema(self) -> CliCommandResult:
        """Return the canonical public run-artifact schema."""
        return _artifact_schema()

    def report_schema(self) -> CliCommandResult:
        """Return the canonical public JSON-report schema."""
        return _report_schema()

    def policy_schema(self, *, machine: bool) -> CliCommandResult:
        """Return a structured result until the policy frontend exists."""
        return _unsupported_policy_schema(machine=machine)

    def validate(self, request: ValidateRequest) -> CliCommandResult:
        """Compile a project without resolution or network activity."""
        return _validate_project(request)

    def doctor(self, request: DoctorRequest) -> CliCommandResult:
        """Run deterministic, offline security-boundary self-checks."""
        return _doctor(request)
