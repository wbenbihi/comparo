"""Tests for framework-free read-only CLI services."""

import asyncio
import json
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.cli import services as service_module
from comparo.cli.contracts import DEFAULT_CLI_ARTIFACT_LIMITS_V1
from comparo.cli.contracts import DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1
from comparo.cli.contracts import ArtifactBlobRequest
from comparo.cli.contracts import ArtifactReadRequest
from comparo.cli.contracts import CliArtifactLimits
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import CliReportFormat
from comparo.cli.contracts import DoctorRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import ReportRequest
from comparo.cli.contracts import ValidateRequest
from comparo.cli.services import DefaultCliServices
from comparo.core.artifacts.model import RunArtifact
from tests.adapters.artifacts.test_local_store import _stored_artifact
from tests.core.artifacts.test_capture_compiler import _limits as domain_artifact_limits


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
        "spec: {request: {method: GET, endpoint: /ping}}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: dev, name: Dev}\n"
        "spec: {baseUrl: https://dev.example.test}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Value\n"
        "metadata: {id: shared, name: Shared}\n"
        "spec: {value: {token: planning-secret-canary}}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Value\n"
        "metadata: {id: scalar, name: Scalar}\n"
        "spec: {value: nope}\n",
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
    parameters_from: tuple[str, ...] = (),
    variables_from: tuple[str, ...] = (),
    secrets_from: tuple[str, ...] = (),
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
        parameters_from=parameters_from,
        variables_from=variables_from,
        secrets_from=secrets_from,
    )


def _artifact_bundle(root: Path) -> tuple[Path, RunArtifact]:
    store = root / "artifacts"
    store.mkdir(mode=0o700)
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(store)
    outcome = asyncio.run(sink.persist(artifact, blobs, domain_artifact_limits()))
    sink.close()
    assert outcome.bundle_id == "artifact-0"
    return store, artifact


def _read_request(
    root: Path,
    *,
    output: CliOutputMode = CliOutputMode.HUMAN,
    limits: CliArtifactLimits = DEFAULT_CLI_ARTIFACT_LIMITS_V1,
) -> ArtifactReadRequest:
    return ArtifactReadRequest(
        root=root,
        bundle_id="artifact-0",
        limits=limits,
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


@pytest.mark.parametrize("output", [CliOutputMode.HUMAN, CliOutputMode.JSON])
def test_invalid_validation_load_limits_fail_before_project_sources_are_opened(
    monkeypatch: pytest.MonkeyPatch,
    output: CliOutputMode,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("configuration source was opened")

    monkeypatch.setattr(service_module, "compile_project_path", unexpected)
    limits = msgspec.structs.replace(DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1, maximum_sources=0)
    result = DefaultCliServices().validate(
        ValidateRequest(
            config=Path("must-not-open.yaml"),
            additional_sources=(),
            output=output,
            load_limits=limits,
        )
    )
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    rendered = (
        result.payload.decode()
        if result.payload
        else "\n".join(item.text for item in result.messages)
    )
    assert "cli.configuration-load.limits.invalid" in rendered


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


def test_plan_binds_value_resources_without_serializing_secret_literals(tmp_path: Path) -> None:
    config = _write_plannable_project(tmp_path)
    result = DefaultCliServices().plan(
        _plan_request(
            config,
            output=CliOutputMode.JSON,
            parameters_from=("Value/shared",),
            variables_from=("prod=shared",),
            secrets_from=("staging=Value/shared",),
        )
    )
    assert result.exit_code is CliExitCode.SUCCESS
    assert b"planning-secret-canary" not in result.payload


@pytest.mark.parametrize(
    ("variables_from", "code"),
    [
        (("does-not-exist",), "invocation.value-resource-missing"),
        (("scalar",), "invocation.value-resource-object-required"),
        (("dev=shared",), "planning.values.environment-not-selected"),
    ],
)
def test_plan_value_resource_failures_are_closed_and_value_free(
    tmp_path: Path,
    variables_from: tuple[str, ...],
    code: str,
) -> None:
    result = DefaultCliServices().plan(
        _plan_request(
            _write_plannable_project(tmp_path),
            output=CliOutputMode.JSON,
            variables_from=variables_from,
        )
    )
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    assert code.encode() in result.payload
    assert "planning-secret-canary" not in result.payload.decode()
    assert all(value not in result.payload.decode() for value in variables_from)


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


@pytest.mark.parametrize("output", [CliOutputMode.HUMAN, CliOutputMode.JSON])
def test_invalid_plan_load_limits_fail_before_preflight_opens_project_sources(
    monkeypatch: pytest.MonkeyPatch,
    output: CliOutputMode,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("project preflight was reached")

    monkeypatch.setattr(service_module, "inspect_project_plan", unexpected)
    request = msgspec.structs.replace(
        _plan_request(Path("must-not-open.yaml"), output=output),
        load_limits=msgspec.structs.replace(
            DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1,
            maximum_policy_yaml_depth=0,
        ),
    )
    result = DefaultCliServices().plan(request)
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    rendered = (
        result.payload.decode()
        if result.payload
        else "\n".join(item.text for item in result.messages)
    )
    assert "cli.configuration-load.limits.invalid" in rendered


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


def test_artifact_inspect_and_verify_succeed_in_human_and_machine_modes(
    tmp_path: Path,
) -> None:
    root, _artifact = _artifact_bundle(tmp_path)
    services = DefaultCliServices()
    inspected_human = services.artifact_inspect(_read_request(root))
    inspected_json = services.artifact_inspect(_read_request(root, output=CliOutputMode.JSON))
    verified_human = services.artifact_verify(_read_request(root))
    verified_json = services.artifact_verify(_read_request(root, output=CliOutputMode.JSON))

    assert inspected_human.exit_code is CliExitCode.SUCCESS
    assert inspected_human.messages[0].text == "Artifact bundle is authentic."
    assert "2 blob(s), 9 byte(s)" in inspected_human.messages[-1].text
    inspection = _json(inspected_json.payload)
    assert inspection["apiVersion"] == "comparo.cli/v1alpha1"
    assert inspection["command"] == "artifact inspect"
    assert inspection["status"] == "authenticated"
    assert cast("dict[str, object]", inspection["artifact"])["api_version"] == (
        "comparo.artifact/v1alpha1"
    )
    assert cast("dict[str, object]", inspection["artifact"])["artifact_id"] == "artifact-0"
    assert verified_human.messages[0].text == "Artifact bundle is authentic."
    assert _json(verified_json.payload) == {
        "apiVersion": "comparo.cli/v1alpha1",
        "command": "artifact verify",
        "status": "verified",
    }


def test_artifact_failures_are_closed_in_human_and_machine_modes(tmp_path: Path) -> None:
    sensitive_root = tmp_path / "secret-artifact-root"
    services = DefaultCliServices()
    human = services.artifact_verify(_read_request(sensitive_root))
    machine = services.artifact_inspect(_read_request(sensitive_root, output=CliOutputMode.JSON))
    assert human.exit_code is CliExitCode.INVALID_INVOCATION
    assert human.messages[0].text == "Artifact operation failed (artifact-unavailable)."
    assert _json(machine.payload) == {
        "apiVersion": "comparo.cli/v1alpha1",
        "code": "artifact-unavailable",
        "command": "artifact inspect",
        "status": "invalid",
    }
    assert "secret-artifact-root" not in human.messages[0].text + machine.payload.decode()

    values = {
        name: getattr(DEFAULT_CLI_ARTIFACT_LIMITS_V1, name)
        for name in DEFAULT_CLI_ARTIFACT_LIMITS_V1.__struct_fields__
    }
    values["maximum_blobs"] = 0
    invalid_limits = CliArtifactLimits(**values)
    invalid = services.artifact_verify(_read_request(sensitive_root, limits=invalid_limits))
    assert invalid.messages[0].text == "Artifact operation failed (invalid-limit)."


def test_artifact_blob_returns_exact_bytes_and_applies_independent_output_limit(
    tmp_path: Path,
) -> None:
    root, artifact = _artifact_bundle(tmp_path)
    blob = artifact.blobs[0]
    services = DefaultCliServices()
    request = ArtifactBlobRequest(
        root=root,
        bundle_id="artifact-0",
        sha256=blob.sha256,
        limits=DEFAULT_CLI_ARTIFACT_LIMITS_V1,
        maximum_output_bytes=blob.byte_count,
    )
    result = services.artifact_blob(request)
    assert result.exit_code is CliExitCode.SUCCESS
    assert result.payload_kind is CliPayloadKind.BINARY
    assert result.payload in {b"left", b"right"}

    exceeded = services.artifact_blob(
        ArtifactBlobRequest(
            root=root,
            bundle_id="artifact-0",
            sha256=blob.sha256,
            limits=DEFAULT_CLI_ARTIFACT_LIMITS_V1,
            maximum_output_bytes=blob.byte_count - 1,
        )
    )
    assert exceeded.messages[0].text == ("Artifact operation failed (output-limit-exceeded).")
    missing = services.artifact_blob(
        ArtifactBlobRequest(
            root=root,
            bundle_id="artifact-0",
            sha256="0" * 64,
            limits=DEFAULT_CLI_ARTIFACT_LIMITS_V1,
            maximum_output_bytes=1024,
        )
    )
    assert missing.messages[0].text == "Artifact operation failed (blob-not-found)."


@pytest.mark.parametrize(
    ("format", "kind", "prefix"),
    [
        (CliReportFormat.JSON, CliPayloadKind.JSON, b'{"api_version"'),
        (CliReportFormat.JUNIT, CliPayloadKind.TEXT, b"<?xml"),
        (CliReportFormat.SARIF, CliPayloadKind.JSON, b'{"$schema"'),
        (CliReportFormat.MARKDOWN, CliPayloadKind.MARKDOWN, b"# Comparo"),
    ],
)
def test_report_renders_every_built_in_format_to_complete_stdout_payload(
    tmp_path: Path,
    format: CliReportFormat,
    kind: CliPayloadKind,
    prefix: bytes,
) -> None:
    root, _artifact = _artifact_bundle(tmp_path)
    result = DefaultCliServices().report(
        ReportRequest(
            root=root,
            bundle_id="artifact-0",
            limits=DEFAULT_CLI_ARTIFACT_LIMITS_V1,
            format=format,
        )
    )
    assert result.exit_code is CliExitCode.SUCCESS
    assert result.payload_kind is kind
    assert result.payload.startswith(prefix)


def test_report_failure_is_value_free_and_does_not_publish_partial_output(tmp_path: Path) -> None:
    root, _artifact = _artifact_bundle(tmp_path)
    values = {
        name: getattr(DEFAULT_CLI_ARTIFACT_LIMITS_V1, name)
        for name in DEFAULT_CLI_ARTIFACT_LIMITS_V1.__struct_fields__
    }
    values["maximum_report_bytes"] = 0
    result = DefaultCliServices().report(
        ReportRequest(
            root=root,
            bundle_id="artifact-0",
            limits=CliArtifactLimits(**values),
            format=CliReportFormat.JSON,
        )
    )
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    assert result.payload == b""
    assert result.messages[0].text == "Artifact operation failed (report-failed)."
