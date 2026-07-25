"""Tests for framework-free read-only CLI services."""

import json
from pathlib import Path
from typing import cast

import pytest

from comparo.cli import services as service_module
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import DoctorRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import ValidateRequest
from comparo.cli.services import DefaultCliServices


def _write_project(root: Path) -> Path:
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec: {data: .comparo}\n",
        encoding="utf-8",
    )
    data = root / ".comparo"
    data.mkdir()
    (data / "environment.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: dev, name: Development}\n"
        "spec: {baseUrl: https://example.test}\n",
        encoding="utf-8",
    )
    return config


def _json(payload: bytes) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(payload))


def _write_plannable_project(root: Path) -> Path:
    config = root / "plan.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec:\n"
        "  data: plan-data\n"
        "  defaults:\n"
        "    subjects: [{$use: Request/ping}]\n"
        "    baseline: {$use: staging}\n"
        "    candidates: [{$use: prod}]\n",
        encoding="utf-8",
    )
    data = root / "plan-data"
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


def _plan_request(
    config: Path,
    *,
    output: CliOutputMode = CliOutputMode.HUMAN,
    matrices: tuple[str, ...] = (),
    budgets: tuple[str, ...] = (),
    subjects: tuple[str, ...] = (),
) -> PlanRequest:
    return PlanRequest(
        config=config,
        additional_sources=(),
        policy=None,
        execution_profile=None,
        subjects=subjects,
        environments=(),
        baseline=None,
        candidates=(),
        matrices=matrices,
        trials=None,
        maximum_attempts=None,
        fail_fast=None,
        budgets=budgets,
        output=output,
    )


def test_schema_services_match_checked_in_contracts() -> None:
    services = DefaultCliServices()
    root = Path(__file__).parents[2] / "src" / "comparo" / "schemas"
    expected = {
        "configuration_schema": root / "comparo-v1alpha1.schema.json",
        "artifact_schema": root / "comparo-artifact-v1alpha1.schema.json",
        "report_schema": root / "comparo-report-v1alpha1.schema.json",
    }
    for method_name, path in expected.items():
        result = getattr(services, method_name)()
        assert result.exit_code is CliExitCode.SUCCESS
        assert result.payload_kind is CliPayloadKind.JSON
        assert result.payload.decode() == path.read_text(encoding="utf-8")


def test_policy_schema_matches_checked_in_contract() -> None:
    services = DefaultCliServices()
    result = services.policy_schema()
    expected = (
        Path(__file__).parents[2]
        / "src"
        / "comparo"
        / "schemas"
        / "comparo-policy-v1alpha1.schema.json"
    )
    assert result.exit_code is CliExitCode.SUCCESS
    assert result.payload_kind is CliPayloadKind.JSON
    assert result.payload.decode() == expected.read_text(encoding="utf-8")


def test_validate_reports_valid_projects_in_human_and_machine_modes(tmp_path: Path) -> None:
    config = _write_project(tmp_path)
    services = DefaultCliServices()
    human = services.validate(
        ValidateRequest(config=config, additional_sources=(), output=CliOutputMode.HUMAN)
    )
    machine = services.validate(
        ValidateRequest(config=config, additional_sources=(), output=CliOutputMode.JSON)
    )
    assert human.exit_code is CliExitCode.SUCCESS
    assert tuple(message.text for message in human.messages) == (
        "Configuration is valid.",
        "Compiled 2 resource(s).",
    )
    assert _json(machine.payload) == {
        "apiVersion": "comparo.cli/v1alpha1",
        "command": "validate",
        "resourceCount": 2,
        "status": "valid",
    }


def test_validate_projects_invalid_diagnostics_without_source_values(tmp_path: Path) -> None:
    config = tmp_path / "broken.yaml"
    config.write_text("broken: [", encoding="utf-8")
    services = DefaultCliServices()
    human = services.validate(
        ValidateRequest(config=config, additional_sources=(), output=CliOutputMode.HUMAN)
    )
    machine = services.validate(
        ValidateRequest(config=config, additional_sources=(), output=CliOutputMode.JSON)
    )
    assert human.exit_code is CliExitCode.INVALID_INVOCATION
    assert human.messages[0].text.startswith("Configuration is invalid")
    document = _json(machine.payload)
    assert document["status"] == "invalid"
    diagnostics = document["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics
    assert set(diagnostics[0]) == {"code", "column", "document", "line", "severity", "source"}
    assert "broken: [" not in machine.payload.decode()


def test_doctor_succeeds_and_never_publishes_its_canary() -> None:
    services = DefaultCliServices()
    human = services.doctor(DoctorRequest(output=CliOutputMode.HUMAN))
    machine = services.doctor(DoctorRequest(output=CliOutputMode.JSON))
    assert human.exit_code is CliExitCode.SUCCESS
    assert all(message.text.endswith("passed") for message in human.messages)
    assert _json(machine.payload)["status"] == "passed"
    assert "comparo-doctor-sensitive-canary" not in machine.payload.decode()


def test_doctor_failure_is_static_in_human_and_machine_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_module,
        "_doctor_checks",
        lambda: (("sensitive-rendering", False),),
    )
    services = DefaultCliServices()
    human = services.doctor(DoctorRequest(output=CliOutputMode.HUMAN))
    machine = services.doctor(DoctorRequest(output=CliOutputMode.JSON))
    assert human.exit_code is CliExitCode.INTERNAL_ERROR
    assert human.messages[0].text == "sensitive-rendering: failed"
    assert _json(machine.payload)["status"] == "failed"


def test_plan_succeeds_in_human_and_machine_modes_with_trusted_local_default(
    tmp_path: Path,
) -> None:
    config = _write_plannable_project(tmp_path)
    services = DefaultCliServices()
    human = services.plan(_plan_request(config))
    machine = services.plan(_plan_request(config, output=CliOutputMode.JSON))
    assert human.exit_code is CliExitCode.SUCCESS
    assert human.messages[0].text == "Plan is valid."
    assert "trusted-local" in "\n".join(item.text for item in human.messages)
    document = _json(machine.payload)
    assert document["api_version"] == "comparo.plan/v1alpha1"
    assert cast("dict[str, object]", document["policy"])["resource_id"] == "trusted-local"
    assert cast("dict[str, int]", document["cardinality"])["comparisons"] == 1


def test_plan_reports_preflight_diagnostics_in_human_and_machine_modes(tmp_path: Path) -> None:
    config = tmp_path / "missing.yaml"
    services = DefaultCliServices()
    human = services.plan(_plan_request(config))
    machine = services.plan(_plan_request(config, output=CliOutputMode.JSON))
    assert human.exit_code is CliExitCode.INVALID_INVOCATION
    assert human.messages[0].text.startswith("Plan is invalid")
    assert all("missing.yaml" not in item.text for item in human.messages)
    document = _json(machine.payload)
    assert document["status"] == "invalid"
    diagnostic = cast("list[dict[str, object]]", document["diagnostics"])[0]
    assert diagnostic["stage"] == "configuration"
    assert diagnostic["source"] is not None
    assert "missing.yaml" not in machine.payload.decode()


@pytest.mark.parametrize(
    "matrix",
    ["missing-separator", "=matrix", "binding=", "binding=matrix=extra"],
)
def test_plan_rejects_malformed_matrix_syntax_without_echoing_values(
    tmp_path: Path,
    matrix: str,
) -> None:
    result = DefaultCliServices().plan(
        _plan_request(
            tmp_path / "unused.yaml",
            output=CliOutputMode.JSON,
            matrices=(matrix,),
        )
    )
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    assert cast("list[dict[str, str]]", _json(result.payload)["diagnostics"])[0]["code"] == (
        "cli.plan.matrix.invalid"
    )
    assert matrix not in result.payload.decode()


@pytest.mark.parametrize(
    ("budget", "code"),
    [
        ("requests", "cli.plan.budget.invalid"),
        ("requests=é", "cli.plan.budget.invalid"),
        ("unknown=2", "cli.plan.budget.invalid"),
        ("requests=0", "invocation.invalid-limit"),
    ],
)
def test_plan_rejects_malformed_budget_syntax_without_echoing_values(
    tmp_path: Path,
    budget: str,
    code: str,
) -> None:
    result = DefaultCliServices().plan(_plan_request(tmp_path / "unused.yaml", budgets=(budget,)))
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    assert result.messages[1].text == f"{code} (invocation)"
    assert budget not in "\n".join(item.text for item in result.messages)


def test_planning_option_translation_accepts_valid_assignments_and_closes_domain_errors(
    tmp_path: Path,
) -> None:
    request = _plan_request(
        tmp_path / "unused.yaml",
        matrices=("region=regions",),
        budgets=("requests=2",),
    )
    options = service_module._planning_options(request)
    assert options.matrices[0].binding == "region"
    assert options.budgets[0].limit == 2

    invalid = DefaultCliServices().plan(
        _plan_request(
            tmp_path / "unused.yaml",
            output=CliOutputMode.JSON,
            subjects=("ping", "ping"),
        )
    )
    diagnostic = cast("list[dict[str, str]]", _json(invalid.payload)["diagnostics"])[0]
    assert diagnostic["code"] == "invocation.duplicate-selection"


def test_assignment_rejects_untyped_values_without_retaining_them() -> None:
    with pytest.raises(ValueError, match=r"safe\.code") as caught:
        service_module._assignment(cast("str", object()), code="safe.code")
    assert str(caught.value) == "safe.code"
