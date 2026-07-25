"""Safe terminal bootstrap and stable process-exit tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.application import PolicyLoadLimits
from comparo.application import ProductionExecutionSettings
from comparo.application import ProjectLoadLimits
from comparo.application import ReportArchiveLimits
from comparo.tui.app import ComparoApp
from comparo.tui.contracts import TuiLaunchLimits
from comparo.tui.contracts import TuiLaunchRequest
from comparo.tui.contracts import tui_artifact_read_limits_defaults_v1
from comparo.tui.contracts import tui_launch_limits_defaults_v1
from comparo.tui.launch import build_tui_app
from comparo.tui.launch import run_tui
from comparo.tui.state import ThemePreference
from comparo.tui.state import TuiPreferences


class _FakeApp:
    """Minimal runnable terminal app for launch-boundary tests."""

    def __init__(self, result: object = None, failure: BaseException | None = None) -> None:
        self.result = result
        self.failure = failure
        self.mouse: bool | None = None

    def run(self, *, mouse: bool) -> object:
        self.mouse = mouse
        if self.failure is not None:
            raise self.failure
        return self.result


def test_launch_limit_defaults_are_fresh_complete_and_persist_results() -> None:
    first = tui_launch_limits_defaults_v1()
    second = tui_launch_limits_defaults_v1()
    assert first is not second
    assert type(first.project_load) is ProjectLoadLimits
    assert type(first.policy_load) is PolicyLoadLimits
    assert type(first.execution_settings) is ProductionExecutionSettings
    assert type(first.report_archive) is ReportArchiveLimits
    assert first.report_archive.maximum_entries == 4096
    assert first.report_archive.artifact == tui_artifact_read_limits_defaults_v1()
    assert first.maximum_outstanding_preparations == 8
    assert first.execution_settings.artifact.persist.value == "always"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("project_load", object(), TypeError),
        ("policy_load", object(), TypeError),
        ("execution_settings", object(), TypeError),
        ("report_archive", object(), TypeError),
        ("maximum_outstanding_preparations", 0, ValueError),
        ("maximum_outstanding_preparations", True, ValueError),
    ],
)
def test_launch_limits_reject_untyped_or_unbounded_values(
    field: str,
    value: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        msgspec.structs.replace(tui_launch_limits_defaults_v1(), **{field: value})


def test_launch_limits_require_exact_construction_type() -> None:
    defaults = tui_launch_limits_defaults_v1()
    values: dict[str, object] = {
        field: getattr(defaults, field) for field in defaults.__struct_fields__
    }
    assert type(TuiLaunchLimits(**values)) is TuiLaunchLimits  # type: ignore[arg-type]


def test_production_builder_composes_an_unstarted_app(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    app = build_tui_app(
        TuiLaunchRequest(
            config=tmp_path / "comparo.yaml",
            artifact_root=artifact_root,
        )
    )
    assert type(app) is ComparoApp


def test_production_builder_rejects_untyped_contracts() -> None:
    with pytest.raises(TypeError, match="typed request"):
        build_tui_app(cast("TuiLaunchRequest", object()))
    with pytest.raises(TypeError, match="typed request"):
        build_tui_app(TuiLaunchRequest(), cast("TuiPreferences", object()))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), (0, 0), (7, 7), (-1, 4), (256, 4), (True, 4), ("0", 4)],
)
def test_run_tui_returns_only_valid_process_codes(value: object, expected: int) -> None:
    app = _FakeApp(result=value)
    preferences = TuiPreferences(theme=ThemePreference.DARK, mouse=False)
    code = run_tui(
        TuiLaunchRequest(),
        preferences=preferences,
        app_factory=lambda _request, _preferences: app,
    )
    assert code == expected
    assert app.mouse is False


@pytest.mark.parametrize(
    ("failure", "expected"),
    [(KeyboardInterrupt(), 130), (RuntimeError("secret-canary"), 4)],
)
def test_run_tui_contains_bootstrap_failures(failure: BaseException, expected: int) -> None:
    code = run_tui(
        TuiLaunchRequest(),
        app_factory=lambda _request, _preferences: _FakeApp(failure=failure),
    )
    assert code == expected
