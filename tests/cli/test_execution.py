"""Tests for the shared framework-neutral CLI production execution bridge."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.planning import PlanningOptions
from comparo.application.production_execution import ProductionExecutionFailure
from comparo.application.production_execution import ProductionExecutionFailureKind
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.cli import execution as execution_module
from comparo.cli.contracts import DEFAULT_CLI_ARTIFACT_ROOT_V1
from comparo.cli.contracts import DEFAULT_CLI_EXECUTION_LIMITS_V1
from comparo.cli.contracts import CliExecutionCommandKind
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import ExecutionRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.execution import execute_cli_request
from comparo.core.artifacts.common import ArtifactCompletionKind
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.lifecycle import ArtifactCompletion
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.ports import ArtifactPersistenceOutcome
from comparo.core.artifacts.ports import ArtifactPersistenceStatus
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.artifacts.ports import ArtifactStoreFailure
from tests.core.artifacts.test_artifact_contracts import _artifact
from tests.reporters.test_reporters import _failed_artifact

_INVOCATION_ID = UUID("00000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000002")


def _write_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec:\n"
        "  data: .comparo\n"
        "  defaults:\n"
        "    subjects: [{$use: Request/ping}]\n"
        "    baseline: {$use: staging}\n"
        "    candidates: [{$use: prod}]\n",
        encoding="utf-8",
    )
    data = root / ".comparo"
    data.mkdir()
    (data / "resources.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: staging, name: Staging}\n"
        "spec: {baseUrl: https://staging.example.test}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: prod, name: Prod}\n"
        "spec: {baseUrl: https://prod.example.test}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Request\n"
        "metadata: {id: ping, name: Ping}\n"
        "spec: {request: {method: GET, endpoint: /ping}}\n",
        encoding="utf-8",
    )
    return config


def _request(
    config: Path,
    command: CliExecutionCommandKind,
    *,
    persist: bool = False,
) -> ExecutionRequest:
    return ExecutionRequest(
        command=command,
        plan=PlanRequest(
            config=config,
            additional_sources=(),
            policy=None,
            execution_profile=None,
            subjects=(),
            environments=(),
            baseline=None,
            candidates=(),
            matrices=(),
            trials=None,
            maximum_attempts=None,
            fail_fast=None,
            budgets=(),
            output=CliOutputMode.HUMAN,
        ),
        approve_mutations=False,
        persist=persist,
        artifact_root=DEFAULT_CLI_ARTIFACT_ROOT_V1,
        limits=DEFAULT_CLI_EXECUTION_LIMITS_V1,
        tool_version="test",
    )


async def _complete_result(_request: ProductionExecutionRequest) -> ProductionExecutionResult:
    return ProductionExecutionResult(artifact=_artifact())


@pytest.mark.parametrize("command", [CliExecutionCommandKind.DIFF, CliExecutionCommandKind.EXEC])
def test_comparison_topology_commands_share_the_production_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: CliExecutionCommandKind,
) -> None:
    captured: list[ProductionExecutionRequest] = []

    async def execute(request: ProductionExecutionRequest) -> ProductionExecutionResult:
        captured.append(request)
        return ProductionExecutionResult(artifact=_artifact())

    monkeypatch.setattr(execution_module, "execute_production", execute)
    execution = asyncio.run(
        execute_cli_request(
            _request(_write_project(tmp_path), command),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert execution.exit_code is CliExitCode.SUCCESS
    assert execution.summary is not None
    assert execution.summary.comparisons == 1
    assert len(captured) == 1
    assert captured[0].settings.artifact.persist is ArtifactPersistMode.NEVER
    assert not (tmp_path / ".comparo" / "artifacts").exists()


@pytest.mark.parametrize("command", [CliExecutionCommandKind.RUN, CliExecutionCommandKind.EXEC])
def test_assertion_topology_commands_share_the_production_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: CliExecutionCommandKind,
) -> None:
    monkeypatch.setattr(execution_module, "execute_production", _complete_result)
    result = asyncio.run(
        execute_cli_request(
            _request(_write_project(tmp_path), command),
            PlanningOptions(environments=("staging",)),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert result.exit_code is CliExitCode.SUCCESS
    assert result.summary is not None


@pytest.mark.parametrize(
    ("command", "options", "code"),
    [
        (
            CliExecutionCommandKind.RUN,
            PlanningOptions(),
            "cli.execution.topology.assertion-required",
        ),
        (
            CliExecutionCommandKind.DIFF,
            PlanningOptions(environments=("staging",)),
            "cli.execution.topology.comparison-required",
        ),
    ],
)
def test_command_topology_is_rejected_before_production_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: CliExecutionCommandKind,
    options: PlanningOptions,
    code: str,
) -> None:
    called = False

    async def execute(_request: ProductionExecutionRequest) -> ProductionExecutionResult:
        nonlocal called
        called = True
        return ProductionExecutionResult(artifact=_artifact())

    monkeypatch.setattr(execution_module, "execute_production", execute)
    result = asyncio.run(
        execute_cli_request(
            _request(_write_project(tmp_path), command),
            options,
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    assert result.diagnostics[0].code == code
    assert not called


@pytest.mark.parametrize(
    ("kind", "exit_code"),
    [
        (ProductionExecutionFailureKind.INVALID, CliExitCode.INVALID_INVOCATION),
        (ProductionExecutionFailureKind.INCOMPLETE, CliExitCode.EXECUTION_INCOMPLETE),
        (ProductionExecutionFailureKind.INTERNAL, CliExitCode.INTERNAL_ERROR),
    ],
)
def test_preparation_and_production_diagnostics_remain_value_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: ProductionExecutionFailureKind,
    exit_code: CliExitCode,
) -> None:
    missing = asyncio.run(
        execute_cli_request(
            _request(tmp_path / "sensitive-missing.yaml", CliExecutionCommandKind.DIFF),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert missing.exit_code is CliExitCode.INVALID_INVOCATION
    assert missing.diagnostics

    project = _write_project(tmp_path / "project")

    async def reject(_request: ProductionExecutionRequest) -> ProductionExecutionResult:
        return ProductionExecutionResult(
            failure=ProductionExecutionFailure(
                kind=kind,
                diagnostics=(
                    ApplicationDiagnostic(
                        code="runtime.capability.unsupported",
                        severity="error",
                        stage="runtime-capability",
                    ),
                ),
            )
        )

    monkeypatch.setattr(execution_module, "execute_production", reject)
    rejected = asyncio.run(
        execute_cli_request(
            _request(project, CliExecutionCommandKind.DIFF),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert rejected.exit_code is exit_code
    assert rejected.diagnostics[0].code == "runtime.capability.unsupported"


def test_invalid_limits_are_rejected_before_production_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def execute(_request: ProductionExecutionRequest) -> ProductionExecutionResult:
        nonlocal called
        called = True
        return ProductionExecutionResult(artifact=_artifact())

    monkeypatch.setattr(execution_module, "execute_production", execute)
    limits = msgspec.structs.replace(
        DEFAULT_CLI_EXECUTION_LIMITS_V1,
        maximum_stream_records=0,
    )
    request = msgspec.structs.replace(
        _request(_write_project(tmp_path), CliExecutionCommandKind.DIFF),
        limits=limits,
    )
    result = asyncio.run(
        execute_cli_request(
            request,
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    assert result.diagnostics[0].code == "cli.execution.limits.invalid"
    assert not called


def test_settings_apply_every_caller_limit_and_explicit_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ProductionExecutionRequest] = []

    async def execute(request: ProductionExecutionRequest) -> ProductionExecutionResult:
        captured.append(request)
        return ProductionExecutionResult(artifact=_artifact())

    monkeypatch.setattr(execution_module, "execute_production", execute)
    original = DEFAULT_CLI_EXECUTION_LIMITS_V1
    artifact_limits = msgspec.structs.replace(
        original.artifact,
        maximum_blobs=7,
        maximum_blob_bytes=11,
        maximum_total_blob_bytes=13,
        maximum_manifest_bytes=17,
        maximum_report_bytes=19,
        maximum_redaction_patterns=23,
        maximum_redaction_pattern_bytes=29,
        maximum_redaction_value_bytes=31,
        maximum_redaction_value_nodes=37,
    )
    limits = msgspec.structs.replace(
        original,
        body_memory_threshold_bytes=2,
        maximum_transient_body_bytes=43,
        maximum_encoded_body_bytes=41,
        maximum_entity_body_bytes=41,
        maximum_expansion_ratio=3,
        maximum_stream_records=5,
        maximum_stream_controls=7,
        maximum_stream_errors=11,
        maximum_stream_item_bytes=13,
        maximum_decoded_body_bytes=17,
        maximum_text_characters=19,
        maximum_json_depth=23,
        maximum_json_nodes=29,
        maximum_json_tokens=31,
        artifact=artifact_limits,
    )
    request = msgspec.structs.replace(
        _request(_write_project(tmp_path), CliExecutionCommandKind.DIFF, persist=True),
        approve_mutations=True,
        limits=limits,
    )
    result = asyncio.run(
        execute_cli_request(
            request,
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert result.exit_code is CliExitCode.SUCCESS
    production = captured[0]
    settings = production.settings
    assert production.approve_mutations
    assert settings.artifact.persist is ArtifactPersistMode.ALWAYS
    assert settings.body_store.memory_threshold.value == 2
    assert settings.body_store.maximum_transient_bytes.value == 43
    assert settings.transport.capture.maximum_encoded_body_bytes.value == 41
    assert settings.transport.capture.maximum_entity_body_bytes.value == 41
    assert settings.transport.capture.maximum_expansion_ratio.numerator == 3
    assert settings.streams.maximum_records.value == 5
    assert settings.streams.maximum_controls.value == 7
    assert settings.streams.maximum_errors.value == 11
    assert settings.streams.maximum_item_bytes.value == 13
    assert settings.ordinary.maximum_body_bytes.value == 17
    assert settings.ordinary.maximum_text_characters.value == 19
    assert settings.ordinary.maximum_json_depth.value == 23
    assert settings.ordinary.maximum_json_nodes.value == 29
    assert settings.ordinary.maximum_json_tokens.value == 31
    assert settings.artifact.limits.maximum_blobs.value == 7
    assert settings.artifact.limits.maximum_blob_bytes.value == 11
    assert settings.artifact.limits.maximum_total_blob_bytes.value == 13
    assert settings.artifact.limits.maximum_manifest_bytes.value == 17
    assert settings.artifact.limits.maximum_report_bytes.value == 19
    sanitization = settings.artifact.limits.sanitization
    assert sanitization.maximum_patterns.value == 23
    assert sanitization.maximum_pattern_bytes.value == 29
    assert sanitization.maximum_value_bytes.value == 31
    assert sanitization.maximum_value_nodes.value == 37


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        (_artifact(), CliExitCode.SUCCESS),
        (_failed_artifact(), CliExitCode.GATE_FAILED),
        (
            msgspec.structs.replace(
                _artifact(),
                completion=ArtifactCompletion(
                    kind=ArtifactCompletionKind.PARTIAL,
                    instant_unix_nanoseconds=3,
                    reasons=("execution-incomplete",),
                ),
            ),
            CliExitCode.EXECUTION_INCOMPLETE,
        ),
        (
            msgspec.structs.replace(
                _artifact(),
                completion=ArtifactCompletion(
                    kind=ArtifactCompletionKind.REJECTED,
                    instant_unix_nanoseconds=None,
                    stage="preflight",
                    code="invalid",
                ),
            ),
            CliExitCode.INVALID_INVOCATION,
        ),
    ],
)
def test_exit_codes_derive_only_from_safe_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact: object,
    expected: CliExitCode,
) -> None:
    typed_artifact = cast("RunArtifact", artifact)

    async def execute(_request: ProductionExecutionRequest) -> ProductionExecutionResult:
        persistence = ArtifactPersistenceOutcome(
            status=ArtifactPersistenceStatus.PERSISTED,
            bundle_id="bundle-safe",
        )
        return ProductionExecutionResult(artifact=typed_artifact, persistence=persistence)

    monkeypatch.setattr(execution_module, "execute_production", execute)
    result = asyncio.run(
        execute_cli_request(
            _request(_write_project(tmp_path), CliExecutionCommandKind.DIFF),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert result.exit_code is expected
    assert result.summary is not None
    assert result.summary.bundle_id == "bundle-safe"
    assert result.summary.gate == typed_artifact.gate.verdict.value
    assert result.summary.gate in {item.value for item in ArtifactGateVerdict}


def test_unsuccessful_persistence_is_safely_summarized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute(_request: ProductionExecutionRequest) -> ProductionExecutionResult:
        return ProductionExecutionResult(
            artifact=_artifact(),
            persistence=ArtifactPersistenceOutcome(
                status=ArtifactPersistenceStatus.FAILED,
                failure=ArtifactStoreFailure(code=ArtifactStoreErrorCode.IO_FAILED),
            ),
        )

    monkeypatch.setattr(execution_module, "execute_production", execute)
    result = asyncio.run(
        execute_cli_request(
            _request(_write_project(tmp_path), CliExecutionCommandKind.DIFF),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert result.summary is not None
    assert result.summary.persistence == "failed"
    assert result.summary.bundle_id is None


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("request", "ExecutionRequest"),
        ("options", "PlanningOptions"),
        ("invocation_id", "UUID"),
        ("run_id", "UUID"),
    ],
)
def test_execution_bridge_rejects_untyped_boundary_inputs(field: str, message: str) -> None:
    values: dict[str, object] = {
        "request": _request(Path("unused.yaml"), CliExecutionCommandKind.DIFF),
        "options": PlanningOptions(),
        "invocation_id": _INVOCATION_ID,
        "run_id": _RUN_ID,
    }
    values[field] = object()
    with pytest.raises(TypeError, match=message):
        asyncio.run(
            execute_cli_request(
                cast("ExecutionRequest", values["request"]),
                cast("PlanningOptions", values["options"]),
                invocation_id=cast("UUID", values["invocation_id"]),
                run_id=cast("UUID", values["run_id"]),
            )
        )
