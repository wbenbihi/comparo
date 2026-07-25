"""Offline project-path preflight and explicit trust-mode tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.planning import PlanningOptions
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import ProjectPlanResult
from comparo.application.preflight import ProjectPreparationResult
from comparo.application.preflight import inspect_project_plan
from comparo.application.preflight import prepare_project_plan
from comparo.core.planning.models import PreparedRun
from comparo.core.policy import CompiledExecutionPolicy

_INVOCATION_ID = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("20000000-0000-0000-0000-000000000002")


def _write_project(root: Path, *, defaults: bool = True) -> Path:
    default_block = (
        "  defaults:\n"
        "    subjects: [{$use: Request/ping}]\n"
        "    baseline: {$use: staging}\n"
        "    candidates: [{$use: prod}]\n"
        if defaults
        else ""
    )
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec:\n"
        "  data: .comparo\n"
        f"{default_block}",
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


def _write_policy(root: Path) -> Path:
    path = root / "operator-policy.yaml"
    path.write_text(
        "apiVersion: comparo.policy/v1alpha1\n"
        "kind: ExecutionPolicy\n"
        "metadata: {id: ci, name: CI}\n"
        "spec:\n"
        "  planning:\n"
        "    subjects: []\n"
        "    environments: []\n"
        "    allowAnySubjects: true\n"
        "    allowAnyEnvironments: true\n"
        "  http:\n"
        "    allowedSchemes: [https]\n"
        "    allowedMethods: [GET]\n"
        "    allowedHosts: [api.example.test]\n"
        "    allowedPorts: [443]\n"
        "    allowedAddressClasses: [public]\n"
        "    allowAnyHost: false\n"
        "    allowAnyPort: false\n"
        "    requireMutationApproval: false\n"
        "    allowProxyDns: false\n"
        "  values:\n"
        "    ambientEnvironment: {names: [], allowAny: false}\n"
        "    allowProjectFiles: false\n"
        "    remoteSchemas: {sources: [], allowAny: false}\n"
        "    secretEgress: {rules: [], allowAny: false}\n"
        "  artifacts:\n"
        "    allowSanitizedPersistence: false\n"
        "    roots: []\n",
        encoding="utf-8",
    )
    return path


def _request(
    config: Path,
    *,
    policy: Path | None = None,
    options: PlanningOptions | None = None,
) -> ProjectPlanRequest:
    return ProjectPlanRequest(
        config=config,
        additional_sources=(),
        policy=policy,
        options=PlanningOptions() if options is None else options,
        invocation_id=_INVOCATION_ID,
        run_id=_RUN_ID,
    )


def test_preflight_uses_trusted_local_by_default_and_external_policy_only_when_explicit(
    tmp_path: Path,
) -> None:
    config = _write_project(tmp_path)
    local = inspect_project_plan(_request(config))
    assert local.inspection is not None
    assert local.inspection.policy.resource_id == "trusted-local"
    assert local.inspection.run_id == str(_RUN_ID)

    external = inspect_project_plan(_request(config, policy=_write_policy(tmp_path)))
    assert external.inspection is not None
    assert external.inspection.policy.resource_id == "ci"
    assert external.inspection.policy.namespace == "operator"

    retained = prepare_project_plan(_request(config))
    assert retained.project is not None
    assert retained.project.project_root == tmp_path.resolve()
    assert retained.project.prepared.plan.run_id.value == _RUN_ID
    assert retained.project.policy.definition == retained.project.prepared.authority.policy


def test_configuration_failure_precedes_policy_selection(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing.yaml"
    outcome = inspect_project_plan(
        _request(missing_config, policy=tmp_path / "also-missing-policy.yaml")
    )
    assert outcome.inspection is None
    assert outcome.diagnostics
    assert {item.stage for item in outcome.diagnostics} == {"configuration"}


def test_external_policy_and_planner_failures_are_normalized(tmp_path: Path) -> None:
    config = _write_project(tmp_path)
    invalid_policy = tmp_path / "invalid-policy.yaml"
    invalid_policy.write_text("secret-policy-canary: [", encoding="utf-8")
    rejected_policy = inspect_project_plan(_request(config, policy=invalid_policy))
    assert rejected_policy.inspection is None
    assert {item.stage for item in rejected_policy.diagnostics} == {"policy"}
    assert "secret-policy-canary" not in msgspec.json.encode(rejected_policy).decode()

    no_defaults_root = tmp_path / "no-defaults"
    no_defaults_root.mkdir()
    rejected_plan = inspect_project_plan(_request(_write_project(no_defaults_root, defaults=False)))
    assert rejected_plan.inspection is None
    assert {item.stage for item in rejected_plan.diagnostics} == {"planning"}


def test_invalid_identifier_is_a_value_free_invocation_diagnostic(tmp_path: Path) -> None:
    outcome = inspect_project_plan(
        _request(
            _write_project(tmp_path),
            options=PlanningOptions(subjects=("INVALID SECRET TOKEN",)),
        )
    )
    assert outcome.inspection is None
    assert outcome.diagnostics[0].code == "invocation.invalid-identifier"
    assert outcome.diagnostics[0].stage == "invocation"
    assert "INVALID SECRET TOKEN" not in msgspec.json.encode(outcome).decode()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"config": object()}, "config"),
        ({"additional_sources": []}, "sources"),
        ({"additional_sources": (object(),)}, "sources"),
        ({"policy": object()}, "policy"),
        ({"options": object()}, "options"),
        ({"invocation_id": object()}, "identifiers"),
        ({"run_id": object()}, "identifiers"),
    ],
)
def test_preflight_request_rejects_untyped_components(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "config": Path("comparo.yaml"),
        "additional_sources": (),
        "policy": None,
        "options": PlanningOptions(),
        "invocation_id": _INVOCATION_ID,
        "run_id": _RUN_ID,
    }
    values.update(changes)
    with pytest.raises(TypeError, match=message):
        ProjectPlanRequest(**values)  # type: ignore[arg-type]


def test_preflight_result_rejects_untyped_or_contradictory_products(tmp_path: Path) -> None:
    inspection = inspect_project_plan(_request(_write_project(tmp_path))).inspection
    assert inspection is not None
    diagnostic = ApplicationDiagnostic(code="test", severity="error", stage="planning")
    with pytest.raises(TypeError, match="inspection"):
        ProjectPlanResult(inspection=cast("PlanInspection", object()))
    with pytest.raises(TypeError, match="diagnostics"):
        ProjectPlanResult(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", []))
    with pytest.raises(TypeError, match="diagnostics"):
        ProjectPlanResult(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", (object(),)))
    with pytest.raises(ValueError, match="exactly"):
        ProjectPlanResult()
    with pytest.raises(ValueError, match="exactly"):
        ProjectPlanResult(inspection=inspection, diagnostics=(diagnostic,))
    with pytest.raises(TypeError, match="ProjectPlanRequest"):
        inspect_project_plan(cast("ProjectPlanRequest", object()))


def test_execution_preflight_products_reject_untyped_or_contradictory_state(
    tmp_path: Path,
) -> None:
    result = prepare_project_plan(_request(_write_project(tmp_path)))
    project = result.project
    assert project is not None
    diagnostic = ApplicationDiagnostic(code="test", severity="error", stage="planning")
    with pytest.raises(TypeError, match="PreparedRun"):
        PreparedProjectPlan(
            prepared=cast("PreparedRun", object()),
            policy=project.policy,
            project_root=project.project_root,
        )
    with pytest.raises(TypeError, match="CompiledExecutionPolicy"):
        PreparedProjectPlan(
            prepared=project.prepared,
            policy=cast("CompiledExecutionPolicy", object()),
            project_root=project.project_root,
        )
    with pytest.raises(TypeError, match="absolute"):
        PreparedProjectPlan(
            prepared=project.prepared,
            policy=project.policy,
            project_root=Path("relative"),
        )
    altered = msgspec.structs.replace(
        project.policy,
        definition=msgspec.structs.replace(
            project.policy.definition,
            fingerprint=msgspec.structs.replace(
                project.policy.definition.fingerprint,
                digest=msgspec.structs.replace(
                    project.policy.definition.fingerprint.digest,
                    hex_value="0" * 64,
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="differs"):
        PreparedProjectPlan(
            prepared=project.prepared,
            policy=altered,
            project_root=project.project_root,
        )
    with pytest.raises(TypeError, match="prepared project"):
        ProjectPreparationResult(project=cast("PreparedProjectPlan", object()))
    with pytest.raises(TypeError, match="diagnostics"):
        ProjectPreparationResult(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", (object(),)))
    with pytest.raises(ValueError, match="exactly"):
        ProjectPreparationResult()
    with pytest.raises(ValueError, match="exactly"):
        ProjectPreparationResult(project=project, diagnostics=(diagnostic,))
