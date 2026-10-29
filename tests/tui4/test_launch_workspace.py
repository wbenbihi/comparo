"""Launch selection, admission, execution and component-library integration contracts."""

from __future__ import annotations

import ast
import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import Button

from comparo.application import CompareExecutionControls
from comparo.application import CompareIntent
from comparo.application import CompareServiceError
from comparo.application import PlanningOptions
from comparo.tui4.app import ComparoApp
from comparo.tui4.backend import TuiBackend
from comparo.tui4.components.actionbar import ActionBar
from comparo.tui4.components.choicemenu import ChoiceMenu
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.launchconfig import LaunchConfiguration
from comparo.tui4.components.valueinput import ValueInput
from comparo.tui4.models.launch import LaunchDraft
from comparo.tui4.workspaces.launch import LaunchWorkspace

ROOT = Path(__file__).parents[2]


def _assert_focused(app: ComparoApp, expected: Widget) -> None:
    """Check current focus without narrowing it across subsequent Pilot actions."""
    assert app.focused is expected


def _project(tmp_path: Path, name: str = "tui-showcase") -> Path:
    project = tmp_path / name
    shutil.copytree(ROOT / "examples" / name, project, ignore=shutil.ignore_patterns("artifacts"))
    return project


async def _wait(pilot: Pilot[None], predicate: Callable[[], bool]) -> None:
    for _ in range(250):
        if predicate():
            return
        await pilot.pause(0.01)
    raise AssertionError("Launch did not reach the expected state")


@pytest.mark.parametrize(
    ("intent", "options", "executions", "comparisons"),
    [
        (
            CompareIntent.EXECUTION_PROFILE,
            PlanningOptions(execution_profile="matrix-release"),
            16,
            8,
        ),
        (
            CompareIntent.ASSERTION,
            PlanningOptions(subjects=("health",), environments=("candidate",)),
            1,
            0,
        ),
        (
            CompareIntent.COMPARISON,
            PlanningOptions(subjects=("health",), baseline="baseline", candidates=("candidate",)),
            2,
            1,
        ),
    ],
)
def test_launch_prepares_all_modes_and_discard_revokes_start(
    tmp_path: Path,
    intent: CompareIntent,
    options: PlanningOptions,
    executions: int,
    comparisons: int,
) -> None:
    async def scenario() -> None:
        backend = TuiBackend(_project(tmp_path))
        try:
            result = await backend.prepare_launch(intent, options, approve_mutations=False)
            assert not result.diagnostics
            assert result.review is not None
            assert result.review.execution_allowed
            assert result.review.plan.cardinality.environment_executions == executions
            assert result.review.plan.cardinality.comparisons == comparisons
            assert backend.runs.count == 0
            backend.discard_handle()
            with pytest.raises(RuntimeError, match="requires preparation"):
                backend.start_launch()
        finally:
            await backend.shutdown()

    asyncio.run(scenario())


def test_mutation_choice_cannot_change_after_preparation(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = _project(tmp_path, "quickstart")
        source = project / ".comparo" / "requests.yaml"
        source.write_text(source.read_text().replace("method: GET", "method: POST"))
        backend = TuiBackend(project)
        options = PlanningOptions(subjects=("health",), environments=("candidate",))
        try:
            result = await backend.prepare_launch(
                CompareIntent.ASSERTION, options, approve_mutations=False
            )
            assert result.review is not None
            assert result.review.plan.write_capable_subjects == 1
            assert not result.review.mutation_approved
            assert result.handle is not None
            with pytest.raises(CompareServiceError):
                backend.compare.validate_start(
                    result.handle,
                    CompareExecutionControls(
                        settings=backend.execution_settings,
                        artifact_root=backend.artifact_root,
                        approve_mutations=True,
                        tool_version="tui4",
                    ),
                )
            assert backend.runs.count == 0
            approved = await backend.prepare_launch(
                CompareIntent.ASSERTION, options, approve_mutations=True
            )
            assert approved.review is not None
            assert approved.review.execution_allowed
            assert approved.handle != result.handle
        finally:
            await backend.shutdown()

    asyncio.run(scenario())


def test_component_selection_filter_modes_and_plan_invalidation(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = ComparoApp(_project(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            launch = app.query_one(LaunchWorkspace)
            config = app.query_one(LaunchConfiguration)
            await _wait(pilot, lambda: launch.catalog is not None and launch.phase == "draft")
            await pilot.press("2")
            config.choices("mode").choose("execution-profile")
            config.choices("profile").choose("matrix-release")
            await pilot.pause()
            await pilot.press("ctrl+r")
            await _wait(pilot, lambda: launch.phase == "ready")
            assert launch.review is not None
            assert launch.review.plan.cardinality.comparisons == 8
            retained = app.backend.handle
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert app.backend.handle is None
            assert retained is not None
            assert launch.draft.profile == "matrix-release"
            assert not launch.check_action("run", ())

            modes = config.choices("mode")
            modes.focus()
            await pilot.press("up")
            assert launch.draft.intent.value == "execution-profile"
            await pilot.press("space")
            await pilot.pause()
            assert launch.draft.intent is CompareIntent.COMPARISON
            assert launch.draft.options().execution_profile is None

            requests = config.choices("requests")
            assert "catalog" in requests.selected
            config.query_one(FilterInput).value = "health"
            await pilot.pause()
            requests.choose("health")
            await pilot.pause()
            config.query_one(FilterInput).value = "no matching request"
            await pilot.pause()
            assert set(launch.draft.subjects) == {"catalog", "health"}
            assert not requests.items
            config.query_one(FilterInput).value = ""
            await pilot.pause()
            assert set(requests.selected) == {"catalog", "health"}

            config.choices("candidates").choose("baseline")
            await pilot.pause()
            assert not launch.check_action("prepare", ())
            assert any("baseline" in message for message in launch.draft.problems)
            config.choices("mode").choose("assertion")
            await pilot.pause()
            assert launch.draft.options().baseline is None
            assert launch.draft.options().candidates == ()
            config.query_one(ValueInput).value = "0"
            await pilot.pause()
            assert not launch.check_action("prepare", ())
            config.query_one(ValueInput).value = "2"
            await pilot.pause()
            assert launch.draft.options().trials == 2
            await pilot.resize_terminal(90, 32)
            await pilot.pause()
            bar = app.query_one("#launch-actions", ActionBar)
            assert bar.region.bottom <= app.screen.size.height

    asyncio.run(scenario())


async def _respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.readuntil(b"\r\n\r\n")
    body = b'{"status":"ok"}'
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


def test_launch_runs_reviewed_selection_and_persists_via_mouse_action(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = await asyncio.start_server(_respond, "127.0.0.1", 0)
        assert server.sockets
        port = server.sockets[0].getsockname()[1]
        project = _project(tmp_path, "quickstart")
        source = project / ".comparo" / "environments.yaml"
        source.write_text(source.read_text().replace("18080", str(port)))
        app = ComparoApp(project)
        try:
            async with app.run_test(size=(140, 45)) as pilot:
                launch = app.query_one(LaunchWorkspace)
                await _wait(pilot, lambda: launch.catalog is not None and launch.phase == "draft")
                await pilot.press("2")
                app.query_one(LaunchConfiguration).choices("mode").choose("comparison")
                await pilot.pause()
                await pilot.press("ctrl+r")
                await _wait(pilot, lambda: launch.phase == "ready")
                assert launch.review is not None
                assert launch.review.plan.cardinality.environment_executions == 2
                await pilot.pause()
                run = next(
                    button
                    for button in app.query_one("#launch-actions", ActionBar).query(Button)
                    if button.name == "run"
                )
                assert run.content_region.height >= 1
                await pilot.click(run)
                await _wait(pilot, lambda: launch.phase == "finished")
                assert launch.run_handle is not None
                snapshot = app.backend.inspect_launch(launch.run_handle).snapshot
                assert snapshot.final_summary is not None
                assert snapshot.final_summary.gate == "passed"
                assert snapshot.finished_executions == 2
                assert app.backend.handle is None
                assert await app.backend.launch_outcome(launch.run_handle) == (
                    "Report: persisted.",
                )
                assert app.backend.artifact_root is not None
                assert any(app.backend.artifact_root.iterdir())
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_launch_workspace_uses_component_library_boundary() -> None:
    tree = ast.parse((ROOT / "src/comparo/tui4/workspaces/launch.py").read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(module and module.startswith("textual.widgets") for module in imports)
    assert "comparo.tui4.components.launchconfig" in imports
    assert "comparo.tui4.components.launchsummary" in imports


def test_profile_mode_does_not_leak_hidden_ad_hoc_selection() -> None:
    draft = LaunchDraft(
        intent=CompareIntent.EXECUTION_PROFILE,
        profile="matrix-release",
        subjects=("other",),
        environments=("development",),
        baseline="baseline",
        candidates=("candidate",),
    )
    assert draft.options() == PlanningOptions(execution_profile="matrix-release")


def test_run_continues_outside_launch_and_can_be_cancelled(tmp_path: Path) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        drained = asyncio.Event()

        async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                started.set()
                await release.wait()
            finally:
                writer.close()
                await writer.wait_closed()
                drained.set()

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        assert server.sockets
        project = _project(tmp_path, "quickstart")
        source = project / ".comparo" / "environments.yaml"
        source.write_text(
            source.read_text().replace("18080", str(server.sockets[0].getsockname()[1]))
        )
        app = ComparoApp(project)
        try:
            async with app.run_test(size=(140, 45)) as pilot:
                launch = app.query_one(LaunchWorkspace)
                await _wait(pilot, lambda: launch.catalog is not None and launch.phase == "draft")
                await pilot.press("2")
                config = app.query_one(LaunchConfiguration)
                config.choices("mode").choose("assertion")
                config.choices("environments").selected = ("candidate",)
                config.query_one(ValueInput).value = "1"
                await pilot.pause()
                await pilot.press("ctrl+r")
                await _wait(pilot, lambda: launch.phase == "ready")
                await pilot.press("ctrl+x")
                await _wait(pilot, started.is_set)
                assert launch.run_handle is not None
                await pilot.press("1")
                await pilot.pause()
                assert not app.backend.inspect_launch(launch.run_handle).terminal
                await pilot.press("2", "ctrl+k")
                await pilot.pause()
                assert launch.phase in {"cancelling", "finished"}, (
                    app.state.active_screen,
                    app.focused,
                    launch.phase,
                )
                await _wait(pilot, lambda: launch.phase == "finished")
                snapshot = app.backend.inspect_launch(launch.run_handle).snapshot
                assert snapshot.final_summary is not None
                assert snapshot.final_summary.completion == "cancelled"
        finally:
            release.set()
            if started.is_set():
                await asyncio.wait_for(drained.wait(), 2)
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_manual_profile_case_filters_and_validation_overrides_reach_review(tmp_path: Path) -> None:
    """A saved profile is optional; exact case/trial/profile choices reach admission."""
    from comparo.tui4.components.disclosure import Disclosure
    from comparo.tui4.components.variantchoices import VariantChoices

    async def scenario() -> None:
        app = ComparoApp(_project(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            launch = app.query_one(LaunchWorkspace)
            config = app.query_one(LaunchConfiguration)
            variants = config.query_one(VariantChoices)
            await _wait(pilot, lambda: launch.catalog is not None and bool(variants.axes))
            await pilot.press("2")
            assert launch.draft.intent is CompareIntent.COMPARISON
            config.choices("mode").choose("execution-profile")
            config.choices("profile-source").choose("manual")
            await _wait(pilot, lambda: launch.draft.manual_profile and bool(variants.axes))
            assert launch.draft.options().execution_profile is None
            assert app.backend.handle is None  # Auto-preview is never an executable checkpoint.
            config.query_one("#launch-variants-section", Disclosure).expanded = True
            await pilot.pause()
            first = variants.query_one("#variant-0", ChoiceMenu)
            for item in first.items[1:]:
                first.choose(item.id)
            config.query_one("#launch-trials", ValueInput).value = "3"
            config.query_one("#launch-maximum-attempts", ValueInput).value = "2"
            config.choices("assertions-mode").choose("none")
            config.choices("gate-mode").choose("none")
            await pilot.pause()
            assert launch.draft.matrices[0].cases == (first.items[0].id,)
            await pilot.press("ctrl+r")
            await _wait(pilot, lambda: launch.phase == "ready")
            assert launch.review is not None
            plan = launch.review.plan
            assert plan.cardinality.cells == 2
            assert plan.cardinality.environment_executions == 12
            assert plan.slices[0].maximum_attempts_per_execution == 2
            assert plan.validation is not None
            assert plan.validation.assertion_rules == 1  # Request response Schema remains active.
            assert not plan.slices[0].profiles.assertions
            assert plan.slices[0].response_schema
            assert plan.validation.gate is None
            assert app.backend.runs.count == 0
            await pilot.press("ctrl+e")
            config.reset_options()
            await pilot.pause()
            assert launch.draft.matrices == ()
            assert launch.draft.assertions is None
            assert launch.draft.gate is None
            assert not launch.draft.trials

    asyncio.run(scenario())


def test_target_keyboard_navigates_sections_without_tabbing_through_closed_editors(
    tmp_path: Path,
) -> None:
    """Section navigation and explicit editing are separate keyboard modes."""
    from comparo.tui4.components.disclosure import Disclosure
    from comparo.tui4.components.disclosure import DisclosureHeader

    async def scenario() -> None:
        app = ComparoApp(_project(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            launch = app.query_one(LaunchWorkspace)
            config = app.query_one(LaunchConfiguration)
            await _wait(pilot, lambda: launch.catalog is not None and launch.phase == "draft")
            await pilot.press("2")
            _assert_focused(app, config.choices("mode"))
            await pilot.press("tab")
            _assert_focused(app, config.sections[0].query_one(DisclosureHeader))
            await pilot.press("down", "down")
            _assert_focused(app, config.sections[2].query_one(DisclosureHeader))
            await pilot.press("enter")
            trials = config.query_one("#launch-trials", ValueInput)
            assert app.focused is not None
            assert trials in app.focused.ancestors
            assert bool(config.sections[2].expanded)
            assert not config.sections[0].expanded
            await pilot.press("2", "escape")
            assert launch.draft.trials == "2"
            _assert_focused(app, config.sections[2].query_one(DisclosureHeader))
            await pilot.press("down", "down", "tab")
            assert app.focused is not None
            assert app.focused.name == "prepare"
            await pilot.press("shift+tab")
            _assert_focused(app, config.sections[4].query_one(DisclosureHeader))
            await pilot.press("alt+2")
            assert bool(config.sections[1].expanded)
            assert not config.sections[2].expanded
            await pilot.press("escape", "left")
            assert not config.sections[1].expanded
            await pilot.press("escape")
            _assert_focused(app, config.choices("mode"))
            header = config.sections[0].query_one(DisclosureHeader)
            await pilot.click(header)
            await pilot.pause()
            assert config.query_one("#launch-target-section", Disclosure).expanded
            assert app.focused is not header
            assert app.focused is not None
            assert config.sections[0] in app.focused.ancestors

    asyncio.run(scenario())


def test_scope_summary_reflows_below_form_and_preserves_selection(tmp_path: Path) -> None:
    """Resizing changes the summary's composition and keeps actions reachable."""
    import os

    from comparo.tui4.components.launchsummary import LaunchSummary
    from comparo.tui4.components.launchsummary import ScopeContents

    async def scenario() -> None:
        app = ComparoApp(_project(tmp_path))
        async with app.run_test(size=(180, 52)) as pilot:
            launch = app.query_one(LaunchWorkspace)
            summary = app.query_one(LaunchSummary)
            config = app.query_one(LaunchConfiguration)
            await _wait(pilot, lambda: launch.catalog is not None and launch.phase == "draft")
            await pilot.press("2")
            await _wait(
                pilot,
                lambda: any(
                    label == "Planned calls"
                    for section in summary.sections
                    for label, _ in section.fields
                ),
            )
            draft = launch.draft
            for size in ((180, 52), (140, 45), (120, 40), (80, 30), (160, 52)):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                assert launch.draft == draft
                rail = app.query_one("#launch-target-rail")
                if size[0] >= 140:
                    assert not summary.compact
                    assert rail.region.width >= 32
                    assert rail.region.x >= config.region.right
                else:
                    assert summary.compact
                    assert summary.query_one(ScopeContents).compact
                    assert rail.region.width >= launch.region.width - 6
                    assert rail.region.y >= config.region.bottom
                assert rail.region.right <= size[0]
                assert rail.region.bottom < size[1]
                action_bar = app.query_one("#launch-target-actions", ActionBar)
                action_bar.focus_action()
                await pilot.pause()
                assert app.focused is not None
                assert app.focused.name == "prepare"
                assert app.focused.region.bottom <= size[1]
                assert app.focused.region.right <= size[0]
                target = os.getenv("COMPARO_SCREENSHOTS")
                if target:
                    Path(target).mkdir(parents=True, exist_ok=True)
                    app.save_screenshot(f"responsive-target-{size[0]}.svg", path=target)

    asyncio.run(scenario())


def test_conditional_profile_choices_are_nested_and_require_explicit_selection(
    tmp_path: Path,
) -> None:
    """Revealed options belong to a labeled child group and remain keyboard reachable."""
    import os

    from comparo.tui4.components.selectiongroup import SelectionGroup

    async def scenario() -> None:
        app = ComparoApp(_project(tmp_path))
        async with app.run_test(size=(140, 45)) as pilot:
            launch = app.query_one(LaunchWorkspace)
            config = app.query_one(LaunchConfiguration)
            await _wait(pilot, lambda: launch.catalog is not None and launch.phase == "draft")
            await pilot.press("2", "alt+4")
            mode = config.choices("assertions-mode")
            assert app.focused is mode
            mode.choose("select")
            await pilot.pause()
            choices = config.choices("assertions")
            group = config.query_one("#launch-assertions-selection", SelectionGroup)
            assert bool(group.display)
            assert group.region.x > mode.region.x
            target = os.getenv("COMPARO_SCREENSHOTS")
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
                app.save_screenshot("validation-hierarchy-140.svg", path=target)
            assert not launch.check_action("prepare", ())
            await pilot.press("tab")
            assert app.focused is choices
            await pilot.press("space")
            await pilot.pause()
            assert bool(launch.draft.assertions)
            assert launch.check_action("prepare", ())
            mode.focus()
            mode.choose("inherit")
            await pilot.pause()
            assert not group.display
            assert launch.draft.assertions is None
            await pilot.press("tab")
            assert app.focused is config.choices("diffs-mode")

    asyncio.run(scenario())
