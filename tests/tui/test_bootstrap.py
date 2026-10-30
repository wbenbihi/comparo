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


class _FakeApp:
    """Minimal runnable terminal app for launch-boundary tests."""

    def __init__(self, result: object = None, failure: BaseException | None = None) -> None:
        self.result = result
        self.failure = failure

    def run(self) -> object:
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
    assert first.report_archive.maximum_entries == 500
    assert first.report_archive.artifact == tui_artifact_read_limits_defaults_v1()
    assert first.maximum_outstanding_preparations == 2
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), (0, 0), (7, 7), (-1, 4), (256, 4), (True, 4), ("0", 4)],
)
def test_run_tui_returns_only_valid_process_codes(value: object, expected: int) -> None:
    app = _FakeApp(result=value)
    code = run_tui(
        TuiLaunchRequest(),
        app_factory=lambda _request: app,
    )
    assert code == expected


@pytest.mark.parametrize(
    ("failure", "expected"),
    [(KeyboardInterrupt(), 130), (RuntimeError("secret-canary"), 4)],
)
def test_run_tui_contains_bootstrap_failures(failure: BaseException, expected: int) -> None:
    code = run_tui(
        TuiLaunchRequest(),
        app_factory=lambda _request: _FakeApp(failure=failure),
    )
    assert code == expected


def test_console_and_module_entry_points_use_the_main_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from io import StringIO

    from comparo.cli import main
    from comparo.tui.__main__ import main as module_main

    launched: list[ComparoApp] = []
    monkeypatch.setattr(ComparoApp, "run", lambda self: launched.append(self))
    arguments = ["--config", str(tmp_path / "comparo.yaml"), "--no-persist"]
    assert main(["tui", *arguments], stdout=StringIO(), stderr=StringIO()) == 0
    assert module_main(arguments) == 0
    assert len(launched) == 2
    for app in launched:
        assert type(app) is ComparoApp
        assert app.backend.launch.config == tmp_path / "comparo.yaml"
        assert app.backend.execution_settings.artifact.persist.value == "never"
    assert not (tmp_path / ".comparo").exists()


def test_launch_options_reach_real_workspace_and_policy_admission(tmp_path: Path) -> None:
    import asyncio

    from tests.application.test_preflight import _write_policy
    from tests.application.test_preflight import _write_project

    from comparo.application import CompareIntent
    from comparo.application import PlanningOptions
    from comparo.core.artifacts.policy import ArtifactPersistMode
    from comparo.tui.backend import TuiBackend

    async def scenario() -> None:
        config = _write_project(tmp_path)
        extra = tmp_path / "additional.yaml"
        extra.write_text(
            "apiVersion: comparo/v1alpha1\nkind: Request\n"
            "metadata: {id: extra, name: Extra}\n"
            "spec: {request: {method: GET, endpoint: /extra}}\n"
        )
        defaults = tui_launch_limits_defaults_v1()
        settings = msgspec.structs.replace(
            defaults.execution_settings,
            artifact=msgspec.structs.replace(
                defaults.execution_settings.artifact, persist=ArtifactPersistMode.NEVER
            ),
        )
        limits = msgspec.structs.replace(defaults, execution_settings=settings)
        backend = TuiBackend(
            TuiLaunchRequest(
                config=config,
                additional_sources=(extra,),
                policy=_write_policy(tmp_path),
                artifact_root=Path("custom-archive"),
                limits=limits,
            )
        )
        try:
            workspace = backend.load_workspace()
            assert not workspace.diagnostics
            result = await backend.prepare_launch(
                CompareIntent.ASSERTION,
                PlanningOptions(subjects=("extra",), environments=("staging",)),
                approve_mutations=False,
            )
            assert not result.diagnostics
            assert result.review is not None
            assert result.review.plan.policy.resource_id == "ci"
            assert result.review.plan.cardinality.environment_executions == 1
            assert backend.artifact_root is None
            assert not (tmp_path / "custom-archive").exists()
            assert not (tmp_path / ".comparo/artifacts").exists()
            assert backend.runs.count == 0
        finally:
            await backend.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("absolute", [False, True])
def test_custom_archive_is_shared_by_launch_and_reports(tmp_path: Path, absolute: bool) -> None:
    import asyncio

    from tests.tui.test_launch_workspace import _project
    from tests.tui.test_reports import _seed

    from comparo.tui.backend import TuiBackend

    project = _project(tmp_path)
    asyncio.run(_seed(project, count=2))
    custom = project / "custom-archive"
    (project / ".comparo/artifacts").rename(custom)
    limits = tui_launch_limits_defaults_v1()
    limits = msgspec.structs.replace(
        limits,
        report_archive=msgspec.structs.replace(limits.report_archive, maximum_entries=1),
    )
    backend = TuiBackend(
        TuiLaunchRequest(
            config=project,
            artifact_root=custom if absolute else Path("custom-archive"),
            limits=limits,
        )
    )
    assert backend.ensure_artifact_root() == custom.resolve()
    # The caller-owned bound is enforced by the archive service, not silently replaced.
    from comparo.application import ReportArchiveError

    with pytest.raises(ReportArchiveError, match="budget-exceeded"):
        backend.list_reports()
    projection = backend.read_report("report-00")
    assert projection is not None
    assert not (project / ".comparo/artifacts").exists()


def test_missing_absolute_archive_is_not_created(tmp_path: Path) -> None:
    from comparo.application import ArtifactOperationError
    from comparo.tui.backend import TuiBackend

    root = tmp_path / "operator-archive"
    backend = TuiBackend(TuiLaunchRequest(config=tmp_path, artifact_root=root))
    with pytest.raises(ArtifactOperationError):
        backend.ensure_artifact_root()
    assert not root.exists()
