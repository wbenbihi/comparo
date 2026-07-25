"""Tests for shared execution service translation and safe rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.planning import PlanningOptions
from comparo.cli import services as service_module
from comparo.cli.contracts import DEFAULT_CLI_ARTIFACT_ROOT_V1
from comparo.cli.contracts import DEFAULT_CLI_EXECUTION_LIMITS_V1
from comparo.cli.contracts import CliExecutionCommandKind
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import ExecutionArtifactSummary
from comparo.cli.contracts import ExecutionRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.execution import CliExecutionOutcome
from comparo.cli.services import DefaultCliServices

_SECRET = "must-never-render-this-secret"


def _request(
    *,
    output: CliOutputMode = CliOutputMode.HUMAN,
    matrices: tuple[str, ...] = (),
) -> ExecutionRequest:
    return ExecutionRequest(
        command=CliExecutionCommandKind.DIFF,
        plan=PlanRequest(
            config=Path("comparo.yaml"),
            additional_sources=(),
            policy=None,
            execution_profile=None,
            subjects=(),
            environments=(),
            baseline="staging",
            candidates=("prod",),
            matrices=matrices,
            trials=2,
            maximum_attempts=3,
            fail_fast=True,
            budgets=("requests=4",),
            output=output,
        ),
        approve_mutations=False,
        persist=False,
        artifact_root=DEFAULT_CLI_ARTIFACT_ROOT_V1,
        limits=DEFAULT_CLI_EXECUTION_LIMITS_V1,
        tool_version="test",
    )


def _summary(*, bundle_id: str | None = None) -> ExecutionArtifactSummary:
    return ExecutionArtifactSummary(
        api_version="comparo.artifact/v1alpha1",
        artifact_id="artifact-0",
        run_id="run-0",
        invocation_id="invocation-0",
        completion="complete",
        gate="not-requested",
        executions=2,
        comparisons=1,
        assertions=1,
        diffs=1,
        coverage_issues=0,
        persistence="not-persisted" if bundle_id is None else "persisted",
        bundle_id=bundle_id,
    )


def test_service_translates_planning_options_once_and_renders_safe_human_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[PlanningOptions] = []

    async def execute(
        request: ExecutionRequest,
        options: PlanningOptions,
        **identifiers: object,
    ) -> CliExecutionOutcome:
        del request
        assert set(identifiers) == {"invocation_id", "run_id"}
        captured.append(options)
        return CliExecutionOutcome(
            exit_code=CliExitCode.SUCCESS,
            summary=_summary(bundle_id="bundle-0"),
        )

    monkeypatch.setattr(service_module, "execute_cli_request", execute)
    result = DefaultCliServices().execute(_request())
    assert result.exit_code is CliExitCode.SUCCESS
    assert result.messages[0].text == "Execution completed."
    assert "artifact-0" in result.messages[1].text
    assert "2 execution(s), 1 comparison(s)" in result.messages[3].text
    assert result.messages[4].text == "Persistence: persisted; bundle bundle-0."
    options = captured[0]
    assert options.baseline == "staging"
    assert options.candidates == ("prod",)
    assert options.trials == 2
    assert options.maximum_attempts == 3
    assert options.fail_fast
    assert options.budgets[0].limit == 4


@pytest.mark.parametrize(
    ("exit_code", "status", "human"),
    [
        (CliExitCode.SUCCESS, "complete", "Execution completed."),
        (CliExitCode.GATE_FAILED, "gate-failed", "Execution completed; gate failed."),
        (CliExitCode.INVALID_INVOCATION, "invalid", "Execution was rejected."),
        (CliExitCode.EXECUTION_INCOMPLETE, "incomplete", "Execution did not complete."),
    ],
)
def test_safe_artifact_controls_human_and_json_status(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: CliExitCode,
    status: str,
    human: str,
) -> None:
    async def execute(*args: object, **kwargs: object) -> CliExecutionOutcome:
        del args, kwargs
        return CliExecutionOutcome(exit_code=exit_code, summary=_summary())

    monkeypatch.setattr(service_module, "execute_cli_request", execute)
    human_result = DefaultCliServices().execute(_request())
    machine_result = DefaultCliServices().execute(_request(output=CliOutputMode.JSON))
    assert human_result.exit_code is exit_code
    assert human_result.messages[0].text == human
    assert human_result.messages[-1].text == "Persistence: not-persisted."
    document = cast("dict[str, object]", json.loads(machine_result.payload))
    assert document["status"] == status
    assert document["command"] == "diff"
    artifact = cast("dict[str, object]", document["artifact"])
    assert artifact["artifact_id"] == "artifact-0"
    assert _SECRET not in machine_result.payload.decode()


def test_value_free_diagnostics_render_in_human_and_machine_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = ApplicationDiagnostic(
        code="runtime.request.failed",
        severity="error",
        stage="runtime",
    )

    async def execute(*args: object, **kwargs: object) -> CliExecutionOutcome:
        del args, kwargs
        return CliExecutionOutcome(
            exit_code=CliExitCode.INVALID_INVOCATION,
            diagnostics=(diagnostic,),
        )

    monkeypatch.setattr(service_module, "execute_cli_request", execute)
    human = DefaultCliServices().execute(_request())
    machine = DefaultCliServices().execute(_request(output=CliOutputMode.JSON))
    assert human.messages[0].text == "Execution is invalid (1 issue(s))."
    assert human.messages[1].text == "runtime.request.failed (runtime)"
    document = cast("dict[str, object]", json.loads(machine.payload))
    assert document["status"] == "invalid"
    diagnostics = cast("list[dict[str, object]]", document["diagnostics"])
    assert diagnostics[0]["code"] == "runtime.request.failed"


def test_invalid_planning_syntax_never_reaches_execution_or_echoes_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def execute(*args: object, **kwargs: object) -> CliExecutionOutcome:
        nonlocal called
        del args, kwargs
        called = True
        return CliExecutionOutcome(exit_code=CliExitCode.SUCCESS, summary=_summary())

    monkeypatch.setattr(service_module, "execute_cli_request", execute)
    result = DefaultCliServices().execute(_request(matrices=(_SECRET,)))
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    assert result.messages[1].text == "cli.plan.matrix.invalid (invocation)"
    assert _SECRET not in "\n".join(item.text for item in result.messages)
    assert not called


@pytest.mark.parametrize(
    "changes",
    [
        {"exit_code": object()},
        {"summary": object()},
        {"diagnostics": []},
        {"diagnostics": (object(),)},
        {"summary": None, "diagnostics": ()},
        {
            "summary": _summary(),
            "diagnostics": (ApplicationDiagnostic(code="invalid", severity="error", stage="test"),),
        },
    ],
)
def test_execution_outcome_enforces_the_safe_boundary(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "exit_code": CliExitCode.SUCCESS,
        "summary": _summary(),
        "diagnostics": (),
    }
    values.update(changes)
    with pytest.raises((TypeError, ValueError)):
        CliExecutionOutcome(**values)  # type: ignore[arg-type]
