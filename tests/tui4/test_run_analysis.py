"""Real live HTTP progress, reference-inspired analysis navigation and body evidence."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import override

import pytest
from tests.tui4.test_launch_workspace import _project
from tests.tui4.test_launch_workspace import _wait
from textual.app import App
from textual.app import ComposeResult
from textual.content import Content
from textual.widgets import Button

from comparo.tui4.app import ComparoApp
from comparo.tui4.components.choicemenu import ChoiceMenu
from comparo.tui4.components.diffview import DiffView
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.groupedtable import GroupedTable
from comparo.tui4.components.launchconfig import LaunchConfiguration
from comparo.tui4.components.results import ResultsView
from comparo.tui4.components.runview import LiveRunView
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Caption
from comparo.tui4.workspaces.launch import LaunchWorkspace


def _capture(app: ComparoApp, name: str) -> None:
    target = os.getenv("COMPARO_SCREENSHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        app.save_screenshot(f"{name}.svg", path=target)


@pytest.mark.parametrize("size", [(80, 30), (120, 40), (160, 52)])
def test_live_status_failed_rule_results_and_authenticated_bodies(
    tmp_path: Path, size: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    if size[0] > 80:
        monkeypatch.delenv("NO_COLOR", raising=False)

    async def scenario() -> None:
        release = asyncio.Event()
        arrived = asyncio.Event()

        async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                candidate = b"/candidate/" in request
                if candidate:
                    arrived.set()
                    await release.wait()
                body = b'{"status":"broken"}' if candidate else b'{"status":"ok"}'
                status = b"500 Internal Server Error" if candidate else b"200 OK"
                writer.write(
                    b"HTTP/1.1 "
                    + status
                    + b"\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        assert server.sockets
        project = _project(tmp_path, "quickstart")
        source = project / ".comparo/environments.yaml"
        source.write_text(
            source.read_text().replace("18080", str(server.sockets[0].getsockname()[1]))
        )
        app = ComparoApp(project)
        try:
            async with app.run_test(size=size) as pilot:
                launch = app.query_one(LaunchWorkspace)
                results = app.query_one(ResultsView)
                await _wait(pilot, lambda: launch.phase == "draft" and launch.catalog is not None)
                await pilot.press("2")
                app.query_one(LaunchConfiguration).choices("mode").choose("comparison")
                await pilot.pause()
                _capture(app, f"target-{size[0]}")
                await pilot.press("ctrl+r")
                await _wait(pilot, lambda: launch.phase == "ready")
                await pilot.pause()
                _capture(app, f"plan-{size[0]}")
                await pilot.press("ctrl+x")
                await _wait(pilot, arrived.is_set)
                assert launch.run_handle is not None
                handle = launch.run_handle
                await _wait(
                    pilot,
                    lambda: any(
                        item.http_status == 200
                        for item in app.backend.inspect_launch(handle).snapshot.occurrences
                    ),
                )
                launch._poll_run()
                live = app.query_one(LiveRunView)
                table = live.query_one(Table)
                assert table.row_count == 1
                table.focus()
                await pilot.press("down")
                selected = table.cursor_row_id
                launch._poll_run()
                await pilot.pause()
                assert table.cursor_row_id == selected
                assert app.focused is table
                assert any("200" in str(cell) for row in table.records for cell in row.cells)
                _capture(app, f"running-{size[0]}")
                release.set()
                await _wait(pilot, lambda: results.projection is not None)
                assert live.display
                assert app.focused is table
                assert table.cursor_row_id == selected
                assert not bool(results.display)
                assert launch.phase == "finished"
                await pilot.pause()
                _capture(app, f"completed-{size[0]}")
                analyze = next(
                    button
                    for button in launch.query("#launch-actions Button")
                    if isinstance(button, Button) and button.name == "analyze"
                )
                await pilot.click(analyze)
                await pilot.pause()
                assert results.display
                projection = results.projection
                assert projection is not None
                assert projection.summary.gate_verdict == "failed"
                assert results.entries
                if size[0] == 80:
                    await pilot.press("enter")
                    await pilot.pause()
                    assert results.has_class("inspecting")
                    await pilot.press("e")
                    await pilot.pause()
                    assert results.has_class("detail-focus")
                    assert results.query_one("#result-scroll").size.height >= 5
                    _capture(app, "analysis-rule-evidence-80")
                    await pilot.press("o")
                    await pilot.pause()
                    assert not results.has_class("detail-focus")
                assert any(fact.outcome == "violated" for fact in projection.assertions)
                assert any(fact.outcome == "drifted" for fact in projection.diffs)
                await pilot.pause()
                _capture(app, f"issues-{size[0]}")
                for number, lens in enumerate(("requests", "rules", "fields", "timeline"), 2):
                    await pilot.press(f"alt+{number}")
                    await pilot.pause()
                    assert results.lens == lens
                    assert results.entries
                    assert app.focused is results.query_one("#result-index", Table)
                    assert not results.has_class("empty-index")
                    if lens == "fields":
                        locations = {
                            evidence.location
                            for fact in (*projection.assertions, *projection.diffs)
                            for evidence in fact.evidence
                        }
                        assert {entry.label for entry in results.entries.values()} == locations
                        labels = " ".join(
                            str(record.cells[0])
                            for record in results.query_one("#result-index", Table).records
                        )
                        assert "response.status" in labels
                        assert "response.status/response.status" not in labels
                        assert "baseline" in labels
                        assert "candidate" in labels
                        assert all(
                            row.reference.result_id not in labels for row in projection.executions
                        )
                        await pilot.press("enter")
                        group_key = results.group_key
                        assert group_key is not None
                        selected_reference = results.selected
                        assert selected_reference in results.entries[group_key].references
                        assert results.links
                    _capture(app, f"{lens}-{size[0]}")
                await pilot.press("right_square_bracket")
                assert results.lens == "issues"
                await pilot.press("left_square_bracket")
                assert results.lens == "timeline"
                await pilot.press("/", *"no-such-result")
                await pilot.pause()
                assert not results.entries
                assert results.has_class("empty-index")
                clear_filter = results.query_one("#result-empty-actions Button", Button)
                await pilot.click(clear_filter)
                await pilot.pause()
                assert results.entries
                assert not results.has_class("empty-index")
                await pilot.press(*"no-such-result")
                await pilot.press("alt+4")
                assert results.lens == "fields"
                assert results.query_one(FilterInput).value == ""
                assert results.entries
                await pilot.press("/", *"status", "escape")
                assert results.query_one(FilterInput).value == ""
                await pilot.press("escape")
                assert app.focused is results.query_one("#result-index", Table)
                await pilot.press("alt+1")
                target_group = next(
                    entry for entry in results.entries.values() if entry.outcome == "drifted"
                )
                results._populate(target_group.label)
                await pilot.pause()
                selected_reference = results.selected
                assert selected_reference in target_group.references
                results._populate("no-such-result")
                await pilot.pause()
                empty_selection = results.selected
                assert empty_selection is None
                assert not results.links
                results._populate("")
                await pilot.pause()
                fact = next(fact for fact in projection.assertions if fact.outcome == "violated")
                stable_rows = results.query_one("#result-related", Table).records
                results.open_reference(fact.reference)
                assert results.selected == fact.reference
                results.open_reference(fact.owner)
                assert results.history[-1] == fact.reference
                results.action_back()
                assert results.selected == fact.reference
                assert results.query_one("#result-related", Table).records == stable_rows
                assert results.links
                launch.action_run_summary()
                assert live.display
                assert not bool(results.display)
                launch.action_analyze()
                assert results.selected == fact.reference
                results.open_reference(fact.owner)
                messages = results.query_one("#result-messages", ChoiceMenu)
                response = next(
                    key for key, ref in results.messages.items() if ref.message.value == "response"
                )
                messages.choose(response)
                await _wait(pilot, lambda: results.query_one(DiffView).document is not None)
                document = results.query_one(DiffView).document
                assert document is not None
                assert "broken" in document.new_text
                results.open_reference(projection.comparisons[0].reference)
                results._compare_bodies()
                await _wait(pilot, lambda: results.query_one(DiffView).document is not None)
                document = results.query_one(DiffView).document
                assert document is not None
                assert document.sided
                assert "ok" in document.old_text
                assert "broken" in document.new_text
                results.query_one(DiffView).focus()
                await pilot.pause()
                _capture(app, f"evidence-{size[0]}")
                # View shortcuts also leave the full-width response inspector cleanly.
                await pilot.press("right_square_bracket")
                assert results.lens == "requests"
                assert not results.has_class("body-focus")
                assert app.focused is results.query_one("#result-index", Table)
                snapshot = app.backend.inspect_launch(launch.run_handle).snapshot
                assert {
                    item.http_status for item in snapshot.occurrences if item.execution is not None
                } == {200, 500}
                assert all(
                    item.transport_failed is False
                    for item in snapshot.occurrences
                    if item.execution is not None
                )
                if size[0] == 80:
                    await pilot.press("escape")
                    await pilot.pause()
                    assert not results.has_class("body-focus")
                    results.history.clear()
                    await pilot.press("escape")
                    await pilot.pause()
                    assert not results.has_class("inspecting")
                    _capture(app, "analysis-groups-80")
                assert results.region.right <= size[0]
                assert results.region.bottom <= size[1]
        finally:
            release.set()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_table_preserves_identity_on_reorder_and_updates_without_clearing() -> None:
    class TableApp(App[None]):
        @override
        def compose(self) -> ComposeResult:
            yield Table[str](
                columns=(Column("value", "Value"),),
                records=(Record("a", ("A",)), Record("b", ("B",))),
            )

    async def scenario() -> None:
        app = TableApp()
        async with app.run_test() as pilot:
            table = app.query_one(Table)
            table.focus()
            await pilot.press("down")
            assert table.cursor_row_id == "b"
            table.records = (Record("b", (Content("Updated"),)), Record("a", ("A",)))
            await pilot.pause()
            assert table.cursor_row_id == "b"
            table.records = (Record("b", (Content("Completed"),)), Record("a", ("A",)))
            await pilot.pause()
            assert table.cursor_row_id == "b"
            assert table.row_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(120, 40), (160, 52)])
@pytest.mark.parametrize("profile", ["matrix-release", "launch-review"])
def test_matrix_profile_live_rows_results_and_new_run_reset(
    tmp_path: Path, size: tuple[int, int], monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    """Exercise matrix and multi-request runs through real folding and evidence navigation."""
    extra = 3 if profile == "launch-review" else 0
    import json
    from urllib.parse import parse_qs
    from urllib.parse import urlsplit

    from examples.support.demo_server import _response

    monkeypatch.delenv("NO_COLOR", raising=False)

    async def scenario() -> None:
        release = asyncio.Event()

        async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                target = urlsplit(request.split(b" ")[1].decode())
                environment, endpoint = target.path.strip("/").split("/", 1)
                if environment == "candidate":
                    await release.wait()
                status, payload = _response(environment, endpoint, parse_qs(target.query), {})
                body = json.dumps(payload).encode()
                writer.write(
                    (
                        f"HTTP/1.1 {status.value} {status.phrase}\r\n"
                        "Content-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                    ).encode()
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        assert server.sockets
        project = _project(tmp_path)
        source = project / ".comparo/environments.yaml"
        source.write_text(
            source.read_text().replace("18080", str(server.sockets[0].getsockname()[1]))
        )
        app = ComparoApp(project)
        try:
            async with app.run_test(size=size) as pilot:
                launch = app.query_one(LaunchWorkspace)
                results = app.query_one(ResultsView)
                config = app.query_one(LaunchConfiguration)
                await _wait(pilot, lambda: launch.phase == "draft" and launch.catalog is not None)
                await pilot.press("2")
                config.choices("mode").choose("execution-profile")
                config.choices("profile").choose(profile)
                await pilot.pause(0.4)
                _capture(app, f"matrix-target-{size[0]}")
                await pilot.press("ctrl+r")
                await _wait(pilot, lambda: launch.phase == "ready")
                assert launch.review is not None
                assert launch.review.plan.cardinality.environment_executions == 16 + 2 * extra
                await pilot.pause()
                _capture(app, f"matrix-plan-{size[0]}")
                await pilot.press("ctrl+x")
                assert launch.run_handle is not None
                run = launch.run_handle
                await _wait(
                    pilot,
                    lambda: app.backend.inspect_launch(run).snapshot.finished_executions >= 1,
                )
                launch._poll_run()
                live = app.query_one(LiveRunView)
                calls = live.query_one(GroupedTable)
                assert calls.row_count == 1 + extra
                calls.focus()
                await pilot.press("space")
                assert calls.row_count == 9 + extra
                await pilot.press("down", "down")
                case = calls.cursor_row_id
                launch._poll_run()
                await pilot.pause()
                assert calls.row_count == 9 + extra
                assert calls.cursor_row_id == case
                _capture(app, f"matrix-running-{size[0]}")
                await pilot.press("left")
                assert calls.row_count == 1 + extra
                _capture(app, f"matrix-running-folded-{size[0]}")
                await pilot.press("right")
                release.set()
                await _wait(pilot, lambda: results.projection is not None)
                assert live.display
                assert not bool(results.display)
                assert launch.phase == "finished"
                await pilot.pause()
                _capture(app, f"completed-{size[0]}")
                analyze = next(
                    button
                    for button in launch.query("#launch-actions Button")
                    if isinstance(button, Button) and button.name == "analyze"
                )
                await pilot.click(analyze)
                await pilot.pause()
                assert results.display
                projection = results.projection
                assert projection is not None
                assert len(projection.executions) == 16 + 2 * extra
                assert len(projection.comparisons) == 8 + extra
                assert any(fact.outcome == "drifted" for fact in projection.diffs)
                await pilot.pause()
                _capture(app, f"matrix-issues-{size[0]}")
                occurrences = results.query_one("#result-related", Table)
                group_rows = occurrences.records
                assert len(group_rows) == (3 if extra else 2)
                occurrences.focus()
                await pilot.press("down", "enter")
                await pilot.pause()
                assert occurrences.records == group_rows
                occurrence_key = occurrences.cursor_row_id
                assert occurrence_key is not None
                assert results.selected == results.links[occurrence_key]
                assert not results.history
                _capture(app, f"matrix-second-occurrence-{size[0]}")
                for number, lens in enumerate(("rules", "fields", "timeline"), 3):
                    await pilot.press(f"alt+{number}")
                    await pilot.pause()
                    assert results.lens == lens
                    if lens == "fields":
                        assert not results.entries
                        assert results.has_class("empty-index")
                        assert results.query_one("#result-empty").display
                        assert not results.query_one("#result-index", Table).display
                        assert not results.query_one("#result-detail-panel").display
                        assert "No field evidence retained" in str(
                            results.query_one("#result-empty-title", Caption).render()
                        )
                        omitted = sum(
                            fact.evidence_omitted
                            for fact in (*projection.assertions, *projection.diffs)
                        )
                        assert omitted > 0
                        assert f"{omitted} evidence records omitted" in str(
                            results.query_one("#result-coverage", Caption).render()
                        )
                    _capture(app, f"matrix-{lens}-{size[0]}")
                await pilot.press("alt+4")
                browse = next(
                    button
                    for button in results.query("#result-empty-actions Button")
                    if isinstance(button, Button) and button.name == "requests"
                )
                await pilot.click(browse)
                await pilot.pause()
                assert results.lens == "requests"
                assert not results.has_class("empty-index")
                request_index = results.query_one("#result-index", GroupedTable)
                request_index.focus()
                await pilot.press("space")
                await pilot.pause()
                assert request_index.row_count == 9 + extra
                _capture(app, f"matrix-requests-{size[0]}")
                await pilot.press("down", "down")
                await pilot.pause()
                case_key = request_index.cursor_row_id
                assert case_key is not None
                selected_case = results.index_children[case_key][1]
                assert results.selected == selected_case
                if size[0] == 120:
                    await pilot.press("enter")
                    await pilot.pause()
                    assert results.query_one("#result-detail-panel").display
                    assert results.query_one("#result-detail-panel").size.width > 100
                    _capture(app, "matrix-request-detail-120")
                assert results.query_one("#result-related", Table).row_count == 8
                related_key = results.query_one("#result-related", Table).cursor_row_id
                assert related_key is not None
                assert results.selected == results.links[related_key]
                assert any(
                    cell != "—"
                    for record in results.query_one("#result-related", Table).records
                    for cell in record.cells[-1:]
                )
                await pilot.press("ctrl+e")
                assert app.backend.runs.count == 0
                await pilot.press("ctrl+r")
                await _wait(pilot, lambda: launch.phase == "ready")
                launch.action_run()
                reset_projection = results.projection
                assert reset_projection is None
                assert not results.body_references
                await _wait(pilot, lambda: results.projection is not None)
                next_projection = results.projection
                assert next_projection is not None
                assert next_projection.summary.artifact_id != projection.summary.artifact_id
        finally:
            release.set()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
