"""Canonical Environment source lowering and run-start snapshot tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import PinnedProjectFiles
from comparo.application import environment_binding as binding_module
from comparo.application.environment_binding import EnvironmentBindingResult
from comparo.application.environment_binding import build_environment_snapshots
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.preflight import prepare_project_plan
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.values import JsonString
from comparo.core.resolution.models import EnvironmentSnapshot
from tests.application.test_preflight import _request
from tests.application.test_preflight import _write_project


def _project(root: Path, env: str) -> PreparedProjectPlan:
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
    outcome = prepare_project_plan(_request(config))
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
            ambient=ambient,
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
    for snapshot in result.snapshots:
        values = {
            item.symbol.segments[0]: cast("JsonString", item.value).value
            for item in snapshot.entries
        }
        assert values == {"A": "file", "B": "inline", "ONLY_AMBIENT": "yes"}
    assert result.snapshots[0].environment != result.snapshots[1].environment


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


def test_safety_handles_are_deduplicated_before_adapter_admission(tmp_path: Path) -> None:
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
    subject = outcome.project.prepared.effective.subjects[0]
    doubled_effective = msgspec.structs.replace(
        outcome.project.prepared.effective,
        subjects=(subject, subject),
    )
    doubled = msgspec.structs.replace(
        outcome.project,
        prepared=msgspec.structs.replace(
            outcome.project.prepared,
            effective=doubled_effective,
        ),
    )
    assert binding_module._safety(doubled) == subject.profiles.safety


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
