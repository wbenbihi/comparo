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


def test_policy_schema_is_structured_in_both_modes() -> None:
    services = DefaultCliServices()
    human = services.policy_schema(machine=False)
    machine = services.policy_schema(machine=True)
    assert human.exit_code is CliExitCode.INTERNAL_ERROR
    assert human.messages[0].text == "The external policy schema is not available yet."
    assert _json(machine.payload) == {
        "apiVersion": "comparo.cli/v1alpha1",
        "code": "cli.schema.policy.unsupported",
        "status": "unsupported",
    }


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
