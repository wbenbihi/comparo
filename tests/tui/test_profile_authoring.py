"""Persisted profile equivalence and explicit Explore-to-Launch handoff."""

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from inspect import unwrap
from pathlib import Path
from threading import Event
from typing import cast

import pytest
from tests.tui.test_launch_workspace import _project
from tests.tui.test_launch_workspace import _wait
from textual.widget import Widget
from textual.widgets import Button

from comparo.application import CompareIntent
from comparo.application import MatrixOverride
from comparo.application import PlanningOptions
from comparo.application import ProjectWorkspaceResult
from comparo.application.profile_authoring import ProfileAuthoringError
from comparo.tui.app import ComparoApp
from comparo.tui.backend import TuiBackend
from comparo.tui.components.launchconfig import LaunchConfiguration
from comparo.tui.components.profilesave import ProfileSave
from comparo.tui.components.typography import Caption
from comparo.tui.components.valueinput import ValueInput
from comparo.tui.launch_handoff import UseInLaunch
from comparo.tui.types import ComparoScreen
from comparo.tui.workspaces.explorer import ExplorerWorkspace
from comparo.tui.workspaces.launch import LaunchWorkspace


def _button(owner: Widget, name: str) -> Button:
    return next(button for button in owner.query(Button) if button.name == name)


@pytest.mark.parametrize("comparison", [False, True])
def test_saved_manual_profile_compiles_and_reproduces_cardinality(
    tmp_path: Path, comparison: bool
) -> None:
    async def scenario() -> None:
        project = _project(tmp_path, "quickstart")
        backend = TuiBackend(project)
        options = (
            PlanningOptions(
                subjects=("health",), baseline="baseline", candidates=("candidate",), trials=2
            )
            if comparison
            else PlanningOptions(subjects=("health",), environments=("candidate",), trials=2)
        )
        try:
            document = backend.prepare_profile(options, "saved-check", "Saved check")
            assert not document.destination.exists()
            backend.save_profile(document)
            assert document.destination.read_text() == document.content
            with pytest.raises(FileExistsError):
                backend.save_profile(document)
            with pytest.raises(ProfileAuthoringError, match="already exists"):
                backend.prepare_profile(options, "saved-check", "Duplicate")
            result = await backend.prepare_launch(
                CompareIntent.EXECUTION_PROFILE,
                PlanningOptions(execution_profile="saved-check"),
                approve_mutations=False,
            )
            assert not result.diagnostics
            assert result.review is not None
            assert result.review.plan.cardinality.environment_executions == (4 if comparison else 2)
            assert backend.runs.count == 0
        finally:
            await backend.shutdown()

    asyncio.run(scenario())


def test_handoff_populates_target_without_starting_and_protects_active_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = ComparoApp(_project(tmp_path), preference_path=tmp_path / "preferences.json")
        async with app.run_test(size=(120, 40)) as pilot:
            launch = app.query_one(LaunchWorkspace)
            explore = app.query_one(ExplorerWorkspace)
            await _wait(pilot, lambda: launch.catalog is not None and explore.catalog is not None)
            assert explore.catalog is not None
            request = next(
                item.summary.coordinate
                for item in explore.catalog.entries.values()
                if item.summary.coordinate.kind == "Request"
            )
            environment = next(
                item.summary.coordinate
                for item in explore.catalog.entries.values()
                if item.summary.coordinate.kind == "Environment"
            )
            app.post_message(UseInLaunch(request, environment))
            await pilot.pause()
            assert app.state.active_screen is ComparoScreen.LAUNCH
            assert launch.draft.subjects == (request.resource_id,)
            assert launch.draft.environments == (environment.resource_id,)
            assert launch.draft.execution_intent is CompareIntent.ASSERTION
            assert app.backend.runs.count == 0
            before = launch.query_one(LaunchConfiguration).draft()
            launch.phase = "running"
            app.action_workspace(ComparoScreen.EXPLORE)
            app.post_message(UseInLaunch(request, None))
            await pilot.pause()
            assert str(app.state.active_screen) == "EXPLORE"
            assert launch.query_one(LaunchConfiguration).draft() == before
            launch.phase = "draft"

    asyncio.run(scenario())


def test_profile_dialog_requires_review_and_refreshes_both_catalogs(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = _project(tmp_path, "quickstart")
        app = ComparoApp(project, preference_path=tmp_path / "preferences.json")
        async with app.run_test(size=(120, 40)) as pilot:
            launch = app.query_one(LaunchWorkspace)
            await _wait(pilot, lambda: launch.phase == "draft")
            app.action_workspace(ComparoScreen.LAUNCH)
            await _wait(pilot, lambda: not _button(launch, "save-profile").disabled)
            button = _button(launch, "save-profile")
            assert not button.disabled
            button.press()
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, ProfileSave)
            dialog.query_one("#profile-id", ValueInput).value = "saved-ui"
            dialog.query_one("#profile-name", ValueInput).value = "Saved from Launch"
            await pilot.pause()
            save = _button(dialog, "save")
            assert save.disabled
            _button(dialog, "preview").press()
            await _wait(pilot, lambda: dialog.document is not None and not dialog.busy)
            assert dialog.document is not None
            destination = dialog.document.destination
            assert not destination.exists()
            assert "execution-profile-saved-ui.yaml" in str(
                dialog.query_one("#profile-status", Caption).render()
            )
            _button(dialog, "save").press()
            await _wait(pilot, lambda: app.screen is not dialog)
            explore = app.query_one(ExplorerWorkspace)
            await _wait(
                pilot,
                lambda: (
                    explore.catalog is not None
                    and any(
                        item.summary.coordinate.resource_id == "saved-ui"
                        for item in explore.catalog.entries.values()
                    )
                ),
            )
            assert destination.exists()
            assert app.backend.runs.count == 0

    asyncio.run(scenario())


def test_save_rejects_unrepresentable_overrides_and_excluded_destination(tmp_path: Path) -> None:
    project = _project(tmp_path, "quickstart")
    backend = TuiBackend(project)
    with pytest.raises(ProfileAuthoringError, match="Matrix case filters"):
        backend.prepare_profile(
            PlanningOptions(
                matrix_filters=(MatrixOverride(binding="input", matrix="inputs", cases=("one",)),)
            ),
            "filtered",
            "Filtered",
        )
    config = project / "comparo.yaml"
    config.write_text(
        config.read_text().replace(
            "data: .comparo",
            "data:\n    root: .comparo\n    exclude: [execution-profile-excluded.yaml]",
        )
    )
    document = backend.prepare_profile(
        PlanningOptions(subjects=("health",), baseline="baseline", candidates=("candidate",)),
        "excluded",
        "Excluded",
    )
    with pytest.raises(ProfileAuthoringError, match="filters exclude"):
        backend.save_profile(document)
    assert not document.destination.exists()
    assert not tuple(document.destination.parent.glob("*.tmp"))


@pytest.mark.parametrize("failed", [False, True])
def test_catalog_reload_completion_is_ignored_during_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    async def scenario() -> None:
        app = ComparoApp(_project(tmp_path, "quickstart"), preference_path=tmp_path / "prefs.json")
        started, release = Event(), Event()
        async with app.run_test() as pilot:
            launch = app.query_one(LaunchWorkspace)
            await _wait(pilot, lambda: launch.phase == "draft")
            snapshot = app.backend.load_workspace()
            retained = launch.catalog

            def delayed_load() -> ProjectWorkspaceResult:
                started.set()
                assert release.wait(5), "Shutdown did not release the catalog read"
                if failed:
                    raise OSError("private-path-canary")
                return snapshot

            monkeypatch.setattr(app.backend, "load_workspace", delayed_load)
            load = cast(
                Callable[[LaunchWorkspace], Awaitable[None]], unwrap(LaunchWorkspace._load_catalog)
            )
            pending = asyncio.ensure_future(load(launch))
            await _wait(pilot, started.is_set)
            close_all = app._close_all

            async def close_with_pending_catalog() -> None:
                # Reproduce completion between child pruning and workspace-worker cancellation.
                assert not app.is_running
                await launch.query_one(LaunchConfiguration).remove()
                release.set()
                await pending
                assert launch.catalog is retained
                assert launch.phase == "loading"
                await close_all()

            monkeypatch.setattr(app, "_close_all", close_with_pending_catalog)
            app.exit()

    asyncio.run(scenario())
