"""Framework-free implementations of Comparo's command-line operations."""

from __future__ import annotations

import asyncio
import json
from typing import Protocol
from typing import final
from uuid import uuid4

import msgspec

from comparo.application.artifacts import ArtifactInspection
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.artifacts import inspect_artifact
from comparo.application.artifacts import read_artifact_blob
from comparo.application.artifacts import render_artifact_report
from comparo.application.artifacts import verify_artifact
from comparo.application.diagnostics import run_security_checks
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.planning import BudgetOverride
from comparo.application.planning import InvocationBuildError
from comparo.application.planning import MatrixOverride
from comparo.application.planning import PlanningOptions
from comparo.application.planning import ValueResourceOverride
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import inspect_project_plan
from comparo.application.render_preview import RenderedRequestPreview
from comparo.configuration.loader import compile_project_path
from comparo.core.artifacts.codec import render_artifact_schema
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.schema_generation import render_v1alpha1_schema
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.policy import render_policy_schema
from comparo.reporters import ReportFormat
from comparo.reporters.json import encode_report_schema

from .contracts import ArtifactBlobRequest
from .contracts import ArtifactReadRequest
from .contracts import CliArtifactLimits
from .contracts import CliCommandResult
from .contracts import CliExitCode
from .contracts import CliMessage
from .contracts import CliMessageLevel
from .contracts import CliOutputMode
from .contracts import CliPayloadKind
from .contracts import CliReportFormat
from .contracts import DoctorRequest
from .contracts import ExecutionArtifactSummary
from .contracts import ExecutionRequest
from .contracts import PlanRequest
from .contracts import RenderRequest
from .contracts import ReportRequest
from .contracts import ValidateRequest
from .execution import CliExecutionOutcome
from .execution import execute_cli_request
from .loading import policy_load_limits
from .loading import project_load_limits
from .rendering import render_cli_request

_CLI_API_VERSION = "comparo.cli/v1alpha1"


class CliServices(Protocol):
    """Narrow framework-neutral operations consumed by command callbacks."""

    def configuration_schema(self) -> CliCommandResult:
        """Return the public configuration schema."""

    def artifact_schema(self) -> CliCommandResult:
        """Return the public artifact schema."""

    def report_schema(self) -> CliCommandResult:
        """Return the public report schema."""

    def policy_schema(self) -> CliCommandResult:
        """Return the public external ExecutionPolicy schema."""

    def validate(self, request: ValidateRequest) -> CliCommandResult:
        """Compile a project without performing runtime effects."""

    def doctor(self, request: DoctorRequest) -> CliCommandResult:
        """Run offline security self-checks."""

    def plan(self, request: PlanRequest) -> CliCommandResult:
        """Compile and inspect one deterministic plan without runtime effects."""

    def execute(self, request: ExecutionRequest) -> CliCommandResult:
        """Execute run, diff, and profile commands through one production path."""

    def render(self, request: RenderRequest) -> CliCommandResult:
        """Resolve and sanitize planned requests without network activity."""

    def artifact_inspect(self, request: ArtifactReadRequest) -> CliCommandResult:
        """Authenticate and summarize one artifact bundle."""

    def artifact_verify(self, request: ArtifactReadRequest) -> CliCommandResult:
        """Authenticate one artifact bundle and all declared blobs."""

    def artifact_blob(self, request: ArtifactBlobRequest) -> CliCommandResult:
        """Read one explicitly selected sanitized blob."""

    def report(self, request: ReportRequest) -> CliCommandResult:
        """Render one authenticated artifact report to stdout."""


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


def _policy_schema() -> CliCommandResult:
    """Return the canonical public external-policy schema."""
    return _document(render_policy_schema(), CliPayloadKind.JSON)


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
    try:
        load_limits = project_load_limits(request.load_limits)
        policy_load_limits(request.load_limits)
    except (TypeError, ValueError):
        return _invalid_configuration_load_limits(request.output)
    loaded = compile_project_path(
        request.config,
        additional_sources=request.additional_sources,
        limits=load_limits,
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


def _invalid_configuration_load_limits(output: CliOutputMode) -> CliCommandResult:
    """Render one stable invocation failure without opening a project source."""
    code = "cli.configuration-load.limits.invalid"
    if output is CliOutputMode.HUMAN:
        return _human(
            CliExitCode.INVALID_INVOCATION,
            (CliMessageLevel.ERROR, "Configuration load limits are invalid."),
            (CliMessageLevel.ERROR, f"{code} (invocation)"),
        )
    return CliCommandResult(
        exit_code=CliExitCode.INVALID_INVOCATION,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "command": "validate",
                "diagnostics": [{"code": code, "severity": "error", "stage": "invocation"}],
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


class _PlanOptionError(ValueError):
    """One closed CLI planning-option failure that never retains its raw token."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _assignment(value: str, *, code: str) -> tuple[str, str]:
    """Split one bounded NAME=VALUE option without retaining failures."""
    if type(value) is not str or value.count("=") != 1:
        raise _PlanOptionError(code)
    name, assigned = value.split("=", 1)
    if not name or not assigned:
        raise _PlanOptionError(code)
    return name, assigned


def _value_resource_override(
    namespace: ValueNamespace,
    value: str,
) -> ValueResourceOverride:
    """Parse one value-free ``[ENV=]VALUE`` coordinate without accepting literals."""
    if type(value) is not str or value.count("=") > 1:
        raise _PlanOptionError("invocation.invalid-value-resource")
    raw_environment, separator, resource = value.partition("=")
    environment: str | None = raw_environment
    if not separator:
        environment = None
        resource = value
    elif not environment or not resource:
        raise _PlanOptionError("invocation.invalid-value-resource")
    return ValueResourceOverride(
        namespace=namespace,
        environment=environment,
        resource=resource,
    )


def _planning_options(request: PlanRequest) -> PlanningOptions:
    """Translate CLI override syntax to the shared frontend-neutral planning contract."""
    try:
        matrices = tuple(
            MatrixOverride(binding=binding, matrix=matrix)
            for binding, matrix in (
                _assignment(value, code="cli.plan.matrix.invalid") for value in request.matrices
            )
        )
        budgets: list[BudgetOverride] = []
        for value in request.budgets:
            resource, raw_limit = _assignment(value, code="cli.plan.budget.invalid")
            if not raw_limit.isascii() or not raw_limit.isdecimal():
                raise _PlanOptionError("cli.plan.budget.invalid")
            try:
                budget_resource = BudgetResource(resource)
            except ValueError as error:
                raise _PlanOptionError("cli.plan.budget.invalid") from error
            budgets.append(BudgetOverride(resource=budget_resource, limit=int(raw_limit)))
        return PlanningOptions(
            execution_profile=request.execution_profile,
            subjects=request.subjects,
            environments=request.environments,
            baseline=request.baseline,
            candidates=request.candidates,
            matrices=matrices,
            trials=request.trials,
            maximum_attempts=request.maximum_attempts,
            fail_fast=request.fail_fast,
            budgets=tuple(budgets),
            value_resources=tuple(
                _value_resource_override(namespace, value)
                for namespace, values in (
                    (ValueNamespace.PARAMETERS, request.parameters_from),
                    (ValueNamespace.VARIABLES, request.variables_from),
                    (ValueNamespace.SECRETS, request.secrets_from),
                )
                for value in values
            ),
        )
    except InvocationBuildError as error:
        raise _PlanOptionError(f"invocation.{error.code.value}") from error


def _application_diagnostic_record(diagnostic: ApplicationDiagnostic) -> dict[str, object]:
    """Project one normalized diagnostic to deterministic machine fields."""
    source = diagnostic.source
    return {
        "code": diagnostic.code,
        "occurrences": diagnostic.occurrences,
        "path": list(diagnostic.path),
        "resource": diagnostic.resource,
        "severity": diagnostic.severity,
        "source": (
            None
            if source is None
            else {
                "column": source.column,
                "document": source.document,
                "line": source.line,
                "source": source.source,
            }
        ),
        "stage": diagnostic.stage,
    }


def _invalid_plan(
    output: CliOutputMode,
    diagnostics: tuple[ApplicationDiagnostic, ...],
) -> CliCommandResult:
    """Render a rejected preflight with value-free diagnostics."""
    if output is CliOutputMode.HUMAN:
        messages: list[tuple[CliMessageLevel, str]] = [
            (CliMessageLevel.ERROR, f"Plan is invalid ({len(diagnostics)} issue(s)).")
        ]
        messages.extend(
            (CliMessageLevel.ERROR, f"{item.code} ({item.stage})") for item in diagnostics
        )
        return _human(CliExitCode.INVALID_INVOCATION, *messages)
    return CliCommandResult(
        exit_code=CliExitCode.INVALID_INVOCATION,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "command": "plan",
                "diagnostics": [_application_diagnostic_record(item) for item in diagnostics],
                "status": "invalid",
            }
        ),
    )


def _human_plan(inspection: PlanInspection) -> CliCommandResult:
    """Render a compact structural plan summary without resolving any value."""
    cardinality = inspection.cardinality
    policy = inspection.policy
    return _human(
        CliExitCode.SUCCESS,
        (CliMessageLevel.SUCCESS, "Plan is valid."),
        (
            CliMessageLevel.INFO,
            f"Plan {inspection.fingerprint}; run {inspection.run_id}; "
            f"invocation {inspection.invocation_id}.",
        ),
        (
            CliMessageLevel.INFO,
            f"Policy {policy.namespace}/{policy.kind}/{policy.resource_id} ({policy.fingerprint}).",
        ),
        (
            CliMessageLevel.INFO,
            f"Work: {cardinality.cells} cell(s), {cardinality.trials} trial(s), "
            f"{cardinality.environment_executions} environment execution(s), "
            f"{cardinality.comparisons} comparison(s), "
            f"{cardinality.maximum_attempts} maximum attempt(s).",
        ),
    )


def _plan(request: PlanRequest) -> CliCommandResult:
    """Run one complete offline preflight through the shared application service."""
    try:
        load_limits = project_load_limits(request.load_limits)
        policy_limits = policy_load_limits(request.load_limits)
    except (TypeError, ValueError):
        return _invalid_plan(
            request.output,
            (
                ApplicationDiagnostic(
                    code="cli.configuration-load.limits.invalid",
                    severity="error",
                    stage="invocation",
                ),
            ),
        )
    try:
        options = _planning_options(request)
    except _PlanOptionError as error:
        return _invalid_plan(
            request.output,
            (
                ApplicationDiagnostic(
                    code=error.code,
                    severity="error",
                    stage="invocation",
                ),
            ),
        )
    outcome = inspect_project_plan(
        ProjectPlanRequest(
            config=request.config,
            additional_sources=request.additional_sources,
            policy=request.policy,
            options=options,
            invocation_id=uuid4(),
            run_id=uuid4(),
            load_limits=load_limits,
            policy_load_limits=policy_limits,
        )
    )
    if outcome.inspection is None:
        return _invalid_plan(request.output, outcome.diagnostics)
    if request.output is CliOutputMode.HUMAN:
        return _human_plan(outcome.inspection)
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(msgspec.to_builtins(outcome.inspection)),
    )


def _invalid_execution(
    request: ExecutionRequest,
    diagnostics: tuple[ApplicationDiagnostic, ...],
) -> CliCommandResult:
    """Render a value-free invalid invocation before execution starts."""
    return _failed_execution(request, CliExitCode.INVALID_INVOCATION, diagnostics)


def _failed_execution(
    request: ExecutionRequest,
    exit_code: CliExitCode,
    diagnostics: tuple[ApplicationDiagnostic, ...],
) -> CliCommandResult:
    """Render typed execution failure without inferring a class from diagnostic text."""
    _level, human_status, machine_status = _execution_status(exit_code)
    if request.plan.output is CliOutputMode.HUMAN:
        messages: list[tuple[CliMessageLevel, str]] = [
            (
                CliMessageLevel.ERROR,
                f"{human_status.removesuffix('.')} ({len(diagnostics)} issue(s)).",
            )
        ]
        messages.extend(
            (CliMessageLevel.ERROR, f"{item.code} ({item.stage})") for item in diagnostics
        )
        return _human(exit_code, *messages)
    return CliCommandResult(
        exit_code=exit_code,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "command": request.command.value,
                "diagnostics": [_application_diagnostic_record(item) for item in diagnostics],
                "status": machine_status,
            }
        ),
    )


def _execution_status(exit_code: CliExitCode) -> tuple[CliMessageLevel, str, str]:
    """Map safe artifact status to human severity and stable machine status."""
    if exit_code is CliExitCode.SUCCESS:
        return CliMessageLevel.SUCCESS, "Execution completed.", "complete"
    if exit_code is CliExitCode.GATE_FAILED:
        return CliMessageLevel.ERROR, "Execution completed; gate failed.", "gate-failed"
    if exit_code is CliExitCode.INVALID_INVOCATION:
        return CliMessageLevel.ERROR, "Execution was rejected.", "invalid"
    if exit_code is CliExitCode.EXECUTION_INCOMPLETE:
        return CliMessageLevel.ERROR, "Execution did not complete.", "incomplete"
    return CliMessageLevel.ERROR, "Execution failed internally.", "internal-error"


def _human_execution(
    outcome: CliExecutionOutcome,
    summary: ExecutionArtifactSummary,
) -> CliCommandResult:
    """Render only identifiers and aggregate counts from one sanitized artifact."""
    level, status, _ = _execution_status(outcome.exit_code)
    storage = f"Persistence: {summary.persistence}."
    if summary.bundle_id is not None:
        storage = f"Persistence: {summary.persistence}; bundle {summary.bundle_id}."
    return _human(
        outcome.exit_code,
        (level, status),
        (
            CliMessageLevel.INFO,
            f"Artifact {summary.artifact_id}; run {summary.run_id}; "
            f"invocation {summary.invocation_id}.",
        ),
        (
            CliMessageLevel.INFO,
            f"Completion: {summary.completion}; gate: {summary.gate}.",
        ),
        (
            CliMessageLevel.INFO,
            f"Results: {summary.executions} execution(s), "
            f"{summary.comparisons} comparison(s), {summary.assertions} assertion(s), "
            f"{summary.diffs} diff(s), {summary.coverage_issues} coverage issue(s).",
        ),
        (CliMessageLevel.INFO, storage),
    )


def _render_execution(
    request: ExecutionRequest,
    outcome: CliExecutionOutcome,
) -> CliCommandResult:
    """Render a bridge result without accepting any live execution object."""
    if outcome.summary is None:
        return _failed_execution(request, outcome.exit_code, outcome.diagnostics)
    if request.plan.output is CliOutputMode.HUMAN:
        return _human_execution(outcome, outcome.summary)
    _, _, status = _execution_status(outcome.exit_code)
    return CliCommandResult(
        exit_code=outcome.exit_code,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "artifact": msgspec.to_builtins(outcome.summary),
                "command": request.command.value,
                "status": status,
            }
        ),
    )


def _execute(request: ExecutionRequest) -> CliCommandResult:
    """Translate planning syntax once and invoke the shared asynchronous execution bridge."""
    try:
        options = _planning_options(request.plan)
    except _PlanOptionError as error:
        return _invalid_execution(
            request,
            (
                ApplicationDiagnostic(
                    code=error.code,
                    severity="error",
                    stage="invocation",
                ),
            ),
        )
    outcome = asyncio.run(
        execute_cli_request(
            request,
            options,
            invocation_id=uuid4(),
            run_id=uuid4(),
        )
    )
    return _render_execution(request, outcome)


def _invalid_render(
    request: RenderRequest,
    diagnostics: tuple[ApplicationDiagnostic, ...],
) -> CliCommandResult:
    """Render one all-or-nothing offline-preview failure without source values."""
    if request.plan.output is CliOutputMode.HUMAN:
        messages: list[tuple[CliMessageLevel, str]] = [
            (CliMessageLevel.ERROR, f"Request rendering is invalid ({len(diagnostics)} issue(s)).")
        ]
        messages.extend(
            (CliMessageLevel.ERROR, f"{item.code} ({item.stage})") for item in diagnostics
        )
        return _human(CliExitCode.INVALID_INVOCATION, *messages)
    return CliCommandResult(
        exit_code=CliExitCode.INVALID_INVOCATION,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "command": "render",
                "diagnostics": [_application_diagnostic_record(item) for item in diagnostics],
                "status": "invalid",
            }
        ),
    )


def _quoted(value: str) -> str:
    """Escape terminal control characters using JSON string syntax."""
    return json.dumps(value, ensure_ascii=True)


def _human_preview(preview: RenderedRequestPreview) -> tuple[str, ...]:
    """Render one sanitized request as terminal-safe, body-free text lines."""
    lines = [
        f"[{preview.ordinal}] {_quoted(preview.method)} {_quoted(preview.url)}",
        f"  execution: {_quoted(preview.execution_id)}",
        f"  trial: {_quoted(preview.trial_id)}; cell: {_quoted(preview.cell_id)}",
        (
            "  subject: "
            f"{_quoted(preview.subject.namespace)}/"
            f"{_quoted(preview.subject.kind)}/"
            f"{_quoted(preview.subject.resource_id)}"
        ),
        (
            "  environment: "
            f"{_quoted(preview.environment.namespace)}/"
            f"{_quoted(preview.environment.kind)}/"
            f"{_quoted(preview.environment.resource_id)}"
        ),
    ]
    lines.extend(
        f"  header: {_quoted(item.name)}: {_quoted(item.value)}; "
        f"redacted={str(item.redacted).lower()}"
        for item in preview.headers
    )
    lines.extend(
        f"  cookie: {_quoted(item.name)}={_quoted(item.value)}; redacted=true"
        for item in preview.cookies
    )
    lines.append(f"  body: omitted ({preview.body.reason.value})")
    return tuple(lines)


def _render_previews(
    request: RenderRequest,
    previews: tuple[RenderedRequestPreview, ...],
) -> CliCommandResult:
    """Encode the complete sanitized preview collection within the caller's output cap."""
    if request.plan.output is CliOutputMode.JSON:
        kind = CliPayloadKind.JSON
        payload = _canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "command": "render",
                "requests": msgspec.to_builtins(previews),
                "status": "rendered",
            }
        )
    else:
        kind = CliPayloadKind.TEXT
        lines = [f"Rendered {len(previews)} request(s)."]
        for preview in previews:
            lines.extend(_human_preview(preview))
        payload = ("\n".join(lines) + "\n").encode()
    if len(payload) > request.limits.maximum_output_bytes:
        return _invalid_render(
            request,
            (
                ApplicationDiagnostic(
                    code="cli.render.output-limit-exceeded",
                    severity="error",
                    stage="render",
                ),
            ),
        )
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=kind,
        payload=payload,
    )


def _render(request: RenderRequest) -> CliCommandResult:
    """Translate shared planning syntax and invoke the offline preview application service."""
    try:
        options = _planning_options(request.plan)
    except _PlanOptionError as error:
        return _invalid_render(
            request,
            (
                ApplicationDiagnostic(
                    code=error.code,
                    severity="error",
                    stage="invocation",
                ),
            ),
        )
    outcome = asyncio.run(
        render_cli_request(
            request,
            options,
            invocation_id=uuid4(),
            run_id=uuid4(),
        )
    )
    if outcome.diagnostics:
        return _invalid_render(request, outcome.diagnostics)
    return _render_previews(request, outcome.previews)


def _artifact_limits(value: CliArtifactLimits) -> ArtifactReadLimits:
    """Translate explicit CLI limits to the application-owned admission contract."""
    return ArtifactReadLimits(
        maximum_blobs=value.maximum_blobs,
        maximum_blob_bytes=value.maximum_blob_bytes,
        maximum_total_blob_bytes=value.maximum_total_blob_bytes,
        maximum_manifest_bytes=value.maximum_manifest_bytes,
        maximum_report_bytes=value.maximum_report_bytes,
        maximum_redaction_patterns=value.maximum_redaction_patterns,
        maximum_redaction_pattern_bytes=value.maximum_redaction_pattern_bytes,
        maximum_redaction_value_bytes=value.maximum_redaction_value_bytes,
        maximum_redaction_value_nodes=value.maximum_redaction_value_nodes,
    )


def _artifact_failure(
    error: ArtifactOperationError,
    *,
    command: str,
    output: CliOutputMode,
) -> CliCommandResult:
    """Return one value-free closed artifact failure in the requested representation."""
    code = error.code.value
    if output is CliOutputMode.HUMAN:
        return _human(
            CliExitCode.INVALID_INVOCATION,
            (CliMessageLevel.ERROR, f"Artifact operation failed ({code})."),
        )
    return CliCommandResult(
        exit_code=CliExitCode.INVALID_INVOCATION,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "code": code,
                "command": command,
                "status": "invalid",
            }
        ),
    )


def _human_artifact_inspection(value: ArtifactInspection) -> CliCommandResult:
    """Render a compact authenticated artifact summary without body content."""
    return _human(
        CliExitCode.SUCCESS,
        (CliMessageLevel.SUCCESS, "Artifact bundle is authentic."),
        (
            CliMessageLevel.INFO,
            f"Completion: {value.completion}; gate: {value.gate}.",
        ),
        (
            CliMessageLevel.INFO,
            f"Results: {value.executions} execution(s), {value.comparisons} comparison(s), "
            f"{value.assertions} assertion(s), {value.diffs} diff(s).",
        ),
        (
            CliMessageLevel.INFO,
            f"Storage: {value.blobs} blob(s), {value.blob_bytes} byte(s), "
            f"{value.coverage_issues} coverage issue(s).",
        ),
    )


def _artifact_inspect(request: ArtifactReadRequest) -> CliCommandResult:
    """Authenticate and inspect through the application artifact service."""
    try:
        inspection = inspect_artifact(
            request.root,
            request.bundle_id,
            _artifact_limits(request.limits),
        )
    except ArtifactOperationError as error:
        return _artifact_failure(error, command="artifact inspect", output=request.output)
    if request.output is CliOutputMode.HUMAN:
        return _human_artifact_inspection(inspection)
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "artifact": msgspec.to_builtins(inspection),
                "command": "artifact inspect",
                "status": "authenticated",
            }
        ),
    )


def _artifact_verify(request: ArtifactReadRequest) -> CliCommandResult:
    """Authenticate every artifact component without publishing its content."""
    try:
        verify_artifact(request.root, request.bundle_id, _artifact_limits(request.limits))
    except ArtifactOperationError as error:
        return _artifact_failure(error, command="artifact verify", output=request.output)
    if request.output is CliOutputMode.HUMAN:
        return _human(
            CliExitCode.SUCCESS,
            (CliMessageLevel.SUCCESS, "Artifact bundle is authentic."),
        )
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.JSON,
        payload=_canonical_json(
            {
                "apiVersion": _CLI_API_VERSION,
                "command": "artifact verify",
                "status": "verified",
            }
        ),
    )


def _artifact_blob(request: ArtifactBlobRequest) -> CliCommandResult:
    """Read one authenticated sanitized blob as exact raw bytes."""
    try:
        payload = asyncio.run(
            read_artifact_blob(
                request.root,
                request.bundle_id,
                request.sha256,
                _artifact_limits(request.limits),
                maximum_output_bytes=request.maximum_output_bytes,
            )
        )
    except ArtifactOperationError as error:
        return _artifact_failure(
            error,
            command="artifact blob",
            output=CliOutputMode.HUMAN,
        )
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.BINARY,
        payload=payload,
    )


_REPORT_PAYLOAD_KINDS = {
    CliReportFormat.JSON: CliPayloadKind.JSON,
    CliReportFormat.JUNIT: CliPayloadKind.TEXT,
    CliReportFormat.SARIF: CliPayloadKind.JSON,
    CliReportFormat.MARKDOWN: CliPayloadKind.MARKDOWN,
}


def _report(request: ReportRequest) -> CliCommandResult:
    """Render one complete authenticated report without filesystem output writes."""
    try:
        report = render_artifact_report(
            request.root,
            request.bundle_id,
            _artifact_limits(request.limits),
            ReportFormat(request.format.value),
        )
    except ArtifactOperationError as error:
        return _artifact_failure(error, command="report", output=CliOutputMode.HUMAN)
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=_REPORT_PAYLOAD_KINDS[request.format],
        payload=report.content,
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

    def policy_schema(self) -> CliCommandResult:
        """Return the canonical public external-policy schema."""
        return _policy_schema()

    def validate(self, request: ValidateRequest) -> CliCommandResult:
        """Compile a project without resolution or network activity."""
        return _validate_project(request)

    def doctor(self, request: DoctorRequest) -> CliCommandResult:
        """Run deterministic, offline security-boundary self-checks."""
        return _doctor(request)

    def plan(self, request: PlanRequest) -> CliCommandResult:
        """Compile and inspect one deterministic plan without runtime effects."""
        return _plan(request)

    def execute(self, request: ExecutionRequest) -> CliCommandResult:
        """Execute through the shared production composition and safe artifact boundary."""
        return _execute(request)

    def render(self, request: RenderRequest) -> CliCommandResult:
        """Render sanitized planned requests through the offline application service."""
        return _render(request)

    def artifact_inspect(self, request: ArtifactReadRequest) -> CliCommandResult:
        """Authenticate and summarize one artifact bundle."""
        return _artifact_inspect(request)

    def artifact_verify(self, request: ArtifactReadRequest) -> CliCommandResult:
        """Authenticate one artifact bundle and every declared blob."""
        return _artifact_verify(request)

    def artifact_blob(self, request: ArtifactBlobRequest) -> CliCommandResult:
        """Read one explicitly selected sanitized blob."""
        return _artifact_blob(request)

    def report(self, request: ReportRequest) -> CliCommandResult:
        """Render one authenticated artifact report to stdout."""
        return _report(request)
