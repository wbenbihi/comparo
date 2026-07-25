"""Tests for framework-neutral CLI contracts."""

from pathlib import Path
from typing import cast

import pytest

from comparo.cli.contracts import DEFAULT_CLI_ARTIFACT_LIMITS_V1
from comparo.cli.contracts import DEFAULT_CLI_ARTIFACT_ROOT_V1
from comparo.cli.contracts import DEFAULT_CLI_BLOB_OUTPUT_BYTES_V1
from comparo.cli.contracts import DEFAULT_CLI_EXECUTION_LIMITS_V1
from comparo.cli.contracts import DEFAULT_CLI_RENDER_LIMITS_V1
from comparo.cli.contracts import ArtifactBlobRequest
from comparo.cli.contracts import ArtifactReadRequest
from comparo.cli.contracts import CliArtifactLimits
from comparo.cli.contracts import CliColorMode
from comparo.cli.contracts import CliCommandResult
from comparo.cli.contracts import CliExecutionCommandKind
from comparo.cli.contracts import CliExecutionLimits
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliInvocation
from comparo.cli.contracts import CliMessage
from comparo.cli.contracts import CliMessageLevel
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import CliRenderLimits
from comparo.cli.contracts import CliReportFormat
from comparo.cli.contracts import DoctorRequest
from comparo.cli.contracts import ExecutionArtifactSummary
from comparo.cli.contracts import ExecutionRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import RenderRequest
from comparo.cli.contracts import ReportRequest
from comparo.cli.contracts import ValidateRequest


def _message() -> CliMessage:
    return CliMessage(level=CliMessageLevel.INFO, text="safe")


def _human_result() -> CliCommandResult:
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.HUMAN,
        messages=(_message(),),
    )


def _plan_request(**changes: object) -> PlanRequest:
    values: dict[str, object] = {
        "config": Path("comparo.yaml"),
        "additional_sources": (),
        "policy": None,
        "execution_profile": None,
        "subjects": (),
        "environments": (),
        "baseline": None,
        "candidates": (),
        "matrices": (),
        "trials": None,
        "maximum_attempts": None,
        "fail_fast": None,
        "budgets": (),
        "output": CliOutputMode.HUMAN,
    }
    values.update(changes)
    return PlanRequest(**values)  # type: ignore[arg-type]


def test_exit_codes_and_valid_contracts_are_stable() -> None:
    assert tuple(int(item) for item in CliExitCode) == (0, 1, 2, 3, 4, 130)
    human = _human_result()
    document = CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.JSON,
        payload=b"{}\n",
    )
    request = ValidateRequest(
        config=Path("comparo.yaml"),
        additional_sources=(Path("extra.yaml"),),
        output=CliOutputMode.JSON,
    )
    doctor = DoctorRequest(output=CliOutputMode.HUMAN)
    invocation = CliInvocation(result=human, color=CliColorMode.AUTO)
    assert document.payload == b"{}\n"
    assert request.additional_sources == (Path("extra.yaml"),)
    assert doctor.output is CliOutputMode.HUMAN
    assert invocation.result is human
    assert _plan_request(policy=Path("policy.yaml")).policy == Path("policy.yaml")
    assert Path(".comparo/artifacts") == DEFAULT_CLI_ARTIFACT_ROOT_V1
    assert DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_blobs == 4096
    assert DEFAULT_CLI_BLOB_OUTPUT_BYTES_V1 == 64 * 1024 * 1024
    assert DEFAULT_CLI_EXECUTION_LIMITS_V1.artifact is DEFAULT_CLI_ARTIFACT_LIMITS_V1
    assert DEFAULT_CLI_RENDER_LIMITS_V1.maximum_output_bytes == 64 * 1024 * 1024
    assert tuple(item.value for item in CliExecutionCommandKind) == ("run", "diff", "exec")
    assert tuple(item.value for item in CliReportFormat) == (
        "json",
        "junit",
        "sarif",
        "markdown",
    )


def test_binary_result_permits_an_empty_exact_payload() -> None:
    result = CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.BINARY,
    )
    assert result.payload == b""


@pytest.mark.parametrize("text", ["", "x" * 4097, cast("str", 1)])
def test_message_rejects_invalid_text(text: str) -> None:
    with pytest.raises(ValueError, match="bounded"):
        CliMessage(level=CliMessageLevel.INFO, text=text)


def test_message_rejects_invalid_level() -> None:
    with pytest.raises(TypeError, match="level"):
        CliMessage(level=cast("CliMessageLevel", object()), text="safe")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"exit_code": object()}, TypeError),
        ({"payload_kind": object()}, TypeError),
        ({"messages": []}, TypeError),
        ({"messages": (object(),)}, TypeError),
        ({"payload": "text"}, TypeError),
        ({"messages": (), "payload": b""}, ValueError),
        ({"payload": b"unexpected"}, ValueError),
        ({"payload_kind": CliPayloadKind.JSON, "payload": b""}, ValueError),
        (
            {
                "payload_kind": CliPayloadKind.JSON,
                "messages": (_message(),),
                "payload": b"{}",
            },
            ValueError,
        ),
    ],
)
def test_result_rejects_contradictory_representations(
    changes: dict[str, object],
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "exit_code": CliExitCode.SUCCESS,
        "payload_kind": CliPayloadKind.HUMAN,
        "messages": (_message(),),
        "payload": b"",
    }
    values.update(changes)
    with pytest.raises(error):
        CliCommandResult(**values)  # type: ignore[arg-type]


def test_validate_request_rejects_untyped_fields() -> None:
    with pytest.raises(TypeError, match="config"):
        ValidateRequest(
            config=cast("Path", object()),
            additional_sources=(),
            output=CliOutputMode.HUMAN,
        )
    with pytest.raises(TypeError, match="sources"):
        ValidateRequest(
            config=Path("config.yaml"),
            additional_sources=cast("tuple[Path, ...]", []),
            output=CliOutputMode.HUMAN,
        )
    with pytest.raises(TypeError, match="sources"):
        ValidateRequest(
            config=Path("config.yaml"),
            additional_sources=(cast("Path", object()),),
            output=CliOutputMode.HUMAN,
        )
    with pytest.raises(TypeError, match="output"):
        ValidateRequest(
            config=Path("config.yaml"),
            additional_sources=(),
            output=cast("CliOutputMode", object()),
        )


def test_doctor_and_invocation_reject_untyped_fields() -> None:
    with pytest.raises(TypeError, match="output"):
        DoctorRequest(output=cast("CliOutputMode", object()))
    with pytest.raises(TypeError, match="result"):
        CliInvocation(
            result=cast("CliCommandResult", object()),
            color=CliColorMode.AUTO,
        )
    with pytest.raises(TypeError, match="color"):
        CliInvocation(result=_human_result(), color=cast("CliColorMode", object()))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"config": object()}, "config"),
        ({"additional_sources": []}, "sources"),
        ({"additional_sources": (object(),)}, "sources"),
        ({"policy": object()}, "policy"),
        ({"execution_profile": object()}, "profile"),
        ({"subjects": []}, "repeated"),
        ({"subjects": (object(),)}, "repeated"),
        ({"baseline": object()}, "baseline"),
        ({"trials": True}, "numeric"),
        ({"maximum_attempts": "2"}, "numeric"),
        ({"fail_fast": 1}, "fail-fast"),
        ({"output": object()}, "output"),
    ],
)
def test_plan_request_rejects_untyped_fields(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        _plan_request(**changes)


def _artifact_read_request(**changes: object) -> ArtifactReadRequest:
    values: dict[str, object] = {
        "root": Path("artifacts"),
        "bundle_id": "artifact-0",
        "limits": DEFAULT_CLI_ARTIFACT_LIMITS_V1,
        "output": CliOutputMode.HUMAN,
    }
    values.update(changes)
    return ArtifactReadRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"root": object()}, "root"),
        ({"bundle_id": object()}, "bundle id"),
        ({"limits": object()}, "limits"),
        ({"output": object()}, "output"),
    ],
)
def test_artifact_read_request_rejects_untyped_fields(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        _artifact_read_request(**changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"root": object()}, "root"),
        ({"bundle_id": object()}, "coordinates"),
        ({"sha256": object()}, "coordinates"),
        ({"limits": object()}, "limits"),
        ({"maximum_output_bytes": True}, "output limit"),
    ],
)
def test_artifact_blob_request_rejects_untyped_fields(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "root": Path("artifacts"),
        "bundle_id": "artifact-0",
        "sha256": "0" * 64,
        "limits": DEFAULT_CLI_ARTIFACT_LIMITS_V1,
        "maximum_output_bytes": 1024,
    }
    values.update(changes)
    with pytest.raises(TypeError, match=message):
        ArtifactBlobRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"root": object()}, "root"),
        ({"bundle_id": object()}, "bundle id"),
        ({"limits": object()}, "limits"),
        ({"format": object()}, "format"),
    ],
)
def test_report_request_rejects_untyped_fields(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "root": Path("artifacts"),
        "bundle_id": "artifact-0",
        "limits": DEFAULT_CLI_ARTIFACT_LIMITS_V1,
        "format": CliReportFormat.JSON,
    }
    values.update(changes)
    with pytest.raises(TypeError, match=message):
        ReportRequest(**values)  # type: ignore[arg-type]


def test_cli_artifact_limits_require_exact_integers() -> None:
    values = {
        field: getattr(DEFAULT_CLI_ARTIFACT_LIMITS_V1, field)
        for field in DEFAULT_CLI_ARTIFACT_LIMITS_V1.__struct_fields__
    }
    values["maximum_blobs"] = True
    with pytest.raises(TypeError, match="exact integers"):
        CliArtifactLimits(**values)


def _execution_request(**changes: object) -> ExecutionRequest:
    values: dict[str, object] = {
        "command": CliExecutionCommandKind.RUN,
        "plan": _plan_request(),
        "approve_mutations": False,
        "persist": False,
        "artifact_root": Path(".comparo/artifacts"),
        "limits": DEFAULT_CLI_EXECUTION_LIMITS_V1,
        "tool_version": "test",
    }
    values.update(changes)
    return ExecutionRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"command": object()}, TypeError, "command kind"),
        ({"plan": object()}, TypeError, "planning request"),
        ({"approve_mutations": 1}, TypeError, "mutation approval"),
        ({"persist": 1}, TypeError, "persistence selection"),
        ({"artifact_root": object()}, TypeError, "artifact root"),
        ({"limits": object()}, TypeError, "execution limits"),
        ({"tool_version": object()}, TypeError, "tool version"),
        ({"tool_version": ""}, ValueError, "bounded"),
        ({"tool_version": "x" * 65}, ValueError, "bounded"),
    ],
)
def test_execution_request_rejects_untyped_or_unbounded_fields(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        _execution_request(**changes)


def test_cli_execution_limits_require_exact_types() -> None:
    values = {
        field: getattr(DEFAULT_CLI_EXECUTION_LIMITS_V1, field)
        for field in DEFAULT_CLI_EXECUTION_LIMITS_V1.__struct_fields__
    }
    values["maximum_json_nodes"] = True
    with pytest.raises(TypeError, match="exact integers"):
        CliExecutionLimits(**values)
    values["maximum_json_nodes"] = 1
    values["artifact"] = object()
    with pytest.raises(TypeError, match="artifact limits"):
        CliExecutionLimits(**values)


def _execution_summary(**changes: object) -> ExecutionArtifactSummary:
    values: dict[str, object] = {
        "api_version": "comparo.artifact/v1alpha1",
        "artifact_id": "artifact-0",
        "run_id": "run-0",
        "invocation_id": "invocation-0",
        "completion": "complete",
        "gate": "not-requested",
        "executions": 1,
        "comparisons": 0,
        "assertions": 1,
        "diffs": 0,
        "coverage_issues": 0,
        "persistence": "not-persisted",
        "bundle_id": None,
    }
    values.update(changes)
    return ExecutionArtifactSummary(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_id": ""},
        {"completion": object()},
        {"executions": -1},
        {"comparisons": True},
        {"bundle_id": ""},
        {"bundle_id": object()},
    ],
)
def test_execution_summary_rejects_unsafe_primitive_shapes(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="execution artifact"):
        _execution_summary(**changes)


def test_render_limits_and_request_require_exact_contracts() -> None:
    values = {
        field: getattr(DEFAULT_CLI_RENDER_LIMITS_V1, field)
        for field in DEFAULT_CLI_RENDER_LIMITS_V1.__struct_fields__
    }
    values["maximum_yaml_nodes"] = True
    with pytest.raises(TypeError, match="exact integers"):
        CliRenderLimits(**values)
    with pytest.raises(TypeError, match="planning request"):
        RenderRequest(
            plan=cast("PlanRequest", object()),
            limits=DEFAULT_CLI_RENDER_LIMITS_V1,
        )
    with pytest.raises(TypeError, match="render limits"):
        RenderRequest(
            plan=_plan_request(),
            limits=cast("CliRenderLimits", object()),
        )
