"""Canonical Environment source lowering and run-start snapshot tests."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import cast

import pytest

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import PinnedProjectFiles
from comparo.application.environment_binding import EnvironmentBindingResult
from comparo.application.environment_binding import build_environment_snapshots
from comparo.application.environment_binding import capture_process_environment
from comparo.application.planning import PlanningOptions
from comparo.application.planning import environment_value_override
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.preflight import prepare_project_plan
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonString
from comparo.core.resolution.models import EnvironmentSnapshot
from tests.application.test_preflight import _request
from tests.application.test_preflight import _write_project


def _project(
    root: Path,
    env: str,
    *,
    options: PlanningOptions | None = None,
) -> PreparedProjectPlan:
    config = _write_project(root)
    resources = root / ".comparo" / "resources.yaml"
    text = resources.read_text(encoding="utf-8")
    text = text.replace(
        "spec: {baseUrl: https://staging.example.test}",
        f"spec: {{baseUrl: https://staging.example.test, env: {env}}}",
    ).replace(
        "spec: {baseUrl: https://prod.example.test}",
        f"spec: {{baseUrl: https://prod.example.test, env: {env}}}",
    )
    resources.write_text(text, encoding="utf-8")
    outcome = prepare_project_plan(_request(config, options=options))
    assert outcome.project is not None
    return outcome.project


def _build(
    project: PreparedProjectPlan,
    files: PinnedProjectFiles,
    *,
    ambient: dict[str, str] | None = None,
) -> EnvironmentBindingResult:
    return asyncio.run(
        build_environment_snapshots(
            project,
            files,
            ambient=None if ambient is None else tuple(sorted(ambient.items())),
            limits=EnvironmentSourceLimits(),
        )
    )


def test_selected_environments_receive_isolated_explicit_precedence(tmp_path: Path) -> None:
    (tmp_path / "shared.env").write_text("A=file\nB=file\n", encoding="utf-8")
    project = _project(
        tmp_path,
        "{inheritProcess: true, files: [{path: shared.env, onConflict: override}], "
        "values: {B: inline}}",
    )
    with PinnedProjectFiles(tmp_path.resolve()) as files:
        result = _build(project, files, ambient={"A": "ambient", "ONLY_AMBIENT": "yes"})
    assert result.diagnostics == ()
    assert len(result.snapshots) == 2
    access = SensitiveValueAccess(purpose="test.environment-binding")
    for snapshot in result.snapshots:
        values = {
            item.symbol.segments[0]: cast(
                "JsonString",
                item.value.reveal(access=access) if is_sensitive_value(item.value) else item.value,
            ).value
            for item in snapshot.entries
        }
        assert values == {"A": "file", "B": "inline", "ONLY_AMBIENT": "yes"}
    assert result.snapshots[0].environment != result.snapshots[1].environment


def test_invocation_environment_value_is_sealed_and_wins_declared_sources(tmp_path: Path) -> None:
    secret = "invocation-environment-secret-canary"
    project = _project(
        tmp_path,
        "{inheritProcess: true, values: {TOKEN: configured}}",
        options=PlanningOptions(
            environment_values=(environment_value_override("staging", "TOKEN", secret),)
        ),
    )
    with PinnedProjectFiles(tmp_path.resolve()) as files:
        result = _build(project, files, ambient={"TOKEN": "ambient"})

    assert result.diagnostics == ()
    access = SensitiveValueAccess(purpose="test.invocation-environment-binding")
    staging, prod = result.snapshots
    staging_token = next(
        item.value for item in staging.entries if item.symbol.segments == ("TOKEN",)
    )
    prod_token = next(item.value for item in prod.entries if item.symbol.segments == ("TOKEN",))
    assert is_sensitive_value(staging_token)
    assert cast("JsonString", staging_token.reveal(access=access)).value == secret
    assert is_sensitive_value(prod_token)
    assert cast("JsonString", prod_token.reveal(access=access)).value == "configured"
    assert secret not in repr(project)


@pytest.mark.parametrize(
    ("env", "code"),
    [
        ("plain", "environment.source.shape-invalid"),
        ("{unknown: true}", "environment.source.shape-invalid"),
        ("{inheritProcess: yes}", "environment.source.shape-invalid"),
        ("{files: plain}", "environment.source.shape-invalid"),
        ("{files: [plain]}", "environment.source.shape-invalid"),
        ("{files: [{unknown: x}]}", "environment.source.shape-invalid"),
        ("{files: [{path: 2}]}", "environment.source.shape-invalid"),
        ("{files: [{path: x, onConflict: 2}]}", "environment.source.shape-invalid"),
        ("{files: [{path: x, onConflict: invalid}]}", "environment.source.shape-invalid"),
        ("{values: plain}", "environment.source.shape-invalid"),
        ("{values: {BAD-NAME: x}}", "environment.source.shape-invalid"),
        ("{values: {GOOD: 2}}", "environment.source.shape-invalid"),
        ("{files: [{path: missing.env}]}", "environment.snapshot.not-found"),
    ],
)
def test_invalid_or_unavailable_sources_fail_with_value_free_diagnostics(
    tmp_path: Path,
    env: str,
    code: str,
) -> None:
    project = _project(tmp_path, env)
    with PinnedProjectFiles(tmp_path.resolve()) as files:
        result = _build(project, files)
    assert result.snapshots == ()
    assert {item.code.value for item in result.diagnostics} == {code}


def test_missing_env_section_produces_empty_snapshots(tmp_path: Path) -> None:
    outcome = prepare_project_plan(_request(_write_project(tmp_path)))
    assert outcome.project is not None
    with PinnedProjectFiles(tmp_path.resolve()) as files:
        result = _build(outcome.project, files)
    assert len(result.snapshots) == 2
    assert all(not item.entries for item in result.snapshots)


def test_process_environment_is_frozen_only_when_selected_sources_inherit_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "environ", {"TOKEN": "captured"})
    (tmp_path / "without").mkdir()
    (tmp_path / "with").mkdir()
    without_inheritance = _project(tmp_path / "without", "{}")
    with_inheritance = _project(tmp_path / "with", "{inheritProcess: true}")

    assert capture_process_environment(without_inheritance, EnvironmentSourceLimits()) == ()
    assert capture_process_environment(with_inheritance, EnvironmentSourceLimits()) == (
        ("TOKEN", "captured"),
    )


def test_safety_profiles_are_bound_before_environment_adapter_admission(tmp_path: Path) -> None:
    config = _write_project(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "    candidates: [{$use: prod}]\n",
            "    candidates: [{$use: prod}]\n    safety: {$use: SafetyProfile/strict}\n",
        ),
        encoding="utf-8",
    )
    resources = tmp_path / ".comparo" / "resources.yaml"
    resources.write_text(
        resources.read_text(encoding="utf-8")
        + "---\napiVersion: comparo/v1alpha1\nkind: SafetyProfile\n"
        "metadata: {id: strict, name: Strict}\nspec: {}\n",
        encoding="utf-8",
    )
    outcome = prepare_project_plan(_request(config))
    assert outcome.project is not None
    with PinnedProjectFiles(tmp_path.resolve()) as files:
        result = _build(outcome.project, files)

    assert result.diagnostics == ()
    assert len(result.snapshots) == 2


def test_environment_binding_boundaries_are_typed(tmp_path: Path) -> None:
    project = _project(tmp_path, "{}")
    with PinnedProjectFiles(tmp_path.resolve()) as files:
        with pytest.raises(TypeError, match="PreparedProjectPlan"):
            _build(cast("PreparedProjectPlan", object()), files)
        with pytest.raises(TypeError, match="PinnedProjectFiles"):
            asyncio.run(
                build_environment_snapshots(
                    project,
                    cast("PinnedProjectFiles", object()),
                    ambient=None,
                    limits=EnvironmentSourceLimits(),
                )
            )
        with pytest.raises(TypeError, match="EnvironmentSourceLimits"):
            asyncio.run(
                build_environment_snapshots(
                    project,
                    files,
                    ambient=None,
                    limits=cast("EnvironmentSourceLimits", object()),
                )
            )
    snapshot = cast("EnvironmentSnapshot", object())
    diagnostic = cast("Diagnostic", object())
    with pytest.raises(TypeError, match="snapshots"):
        EnvironmentBindingResult(snapshots=(snapshot,))
    with pytest.raises(TypeError, match="diagnostics"):
        EnvironmentBindingResult(diagnostics=(diagnostic,))
    with pytest.raises(ValueError, match="exactly"):
        EnvironmentBindingResult()
