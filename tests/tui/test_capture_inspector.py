"""Real stored captures through the shared keyboard-driven evidence inspector."""

import asyncio
from dataclasses import replace
from pathlib import Path

import msgspec
import pytest
from tests.adapters.artifacts.test_local_store import _stored_artifact
from tests.application.test_artifacts import _limits as read_limits
from tests.application.test_structured_bodies import _replace_response_body
from tests.core.artifacts.test_capture_compiler import _limits as storage_limits
from tests.tui.test_launch_workspace import _project
from tests.tui.test_launch_workspace import _wait
from tests.tui.test_reports import _seed
from textual.widget import Widget
from textual.widgets import Button

from comparo.adapters.artifacts import InMemorySanitizedBlobStore
from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.application import ArtifactBodyMessage
from comparo.application import ArtifactBodyReference
from comparo.application.structured_bodies import StructuredJsonPage
from comparo.application.structured_bodies import StructuredSsePage
from comparo.core.artifacts import fingerprint_run_artifact
from comparo.core.domain.primitives import ByteCount
from comparo.tui.app import ComparoApp
from comparo.tui.components.captureinspector import CaptureInspector
from comparo.tui.components.diffview import DiffView
from comparo.tui.components.results import ResultsView
from comparo.tui.components.table import Table
from comparo.tui.components.typography import Caption
from comparo.tui.evidence_access import BODY_PAGE_BYTES
from comparo.tui.evidence_access import EvidenceAccess
from comparo.tui.workspaces.launch import LaunchWorkspace
from comparo.tui.workspaces.reports import ReportsWorkspace


def _button(owner: Widget, name: str) -> Button:
    return next(button for button in owner.query(Button) if button.name == name)


async def _access(
    tmp_path: Path, payload: bytes, media: str
) -> tuple[EvidenceAccess, ArtifactBodyReference]:
    artifact, original = await _stored_artifact()
    limits = msgspec.structs.replace(
        storage_limits(),
        maximum_blob_bytes=ByteCount(value=1_000_000),
        maximum_total_blob_bytes=ByteCount(value=2_000_000),
    )
    store = InMemorySanitizedBlobStore(limits)
    for blob in artifact.blobs:
        writer = await store.begin()
        async for chunk in original.read(blob):
            await writer.write(chunk)
        await writer.commit(media_type=blob.media_type)
    original.close()
    artifact = await _replace_response_body(artifact, store, payload, media)
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    sink = LocalArtifactBundleSink(root)
    try:
        outcome = await sink.persist(
            artifact,
            store,
            msgspec.structs.replace(
                storage_limits(),
                maximum_blob_bytes=ByteCount(value=1_000_000),
                maximum_total_blob_bytes=ByteCount(value=2_000_000),
            ),
        )
        assert outcome.bundle_id == "artifact-0"
    finally:
        sink.close()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    return EvidenceAccess(
        root, read_limits(maximum_blob_bytes=1_000_000, maximum_total_blob_bytes=2_000_000)
    ), ArtifactBodyReference(
        artifact_api_version=artifact.api_version,
        artifact_id=artifact.metadata.artifact_id,
        artifact_results_sha256=fingerprint_run_artifact(artifact),
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        exchange_id=exchange.exchange_id,
        message=ArtifactBodyMessage.RESPONSE,
    )


def test_raw_paging_json_subtrees_and_curl_remain_on_exact_capture(tmp_path: Path) -> None:
    async def scenario() -> None:
        payload = b'{"user":{"id":7,"name":"sample"},"padding":"' + b"x" * 70000 + b'"}'
        access, reference = await _access(tmp_path, payload, "application/json")
        app = ComparoApp(Path("examples/quickstart"), preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=(120, 40)) as pilot:
            inspector = CaptureInspector(
                access, (("Baseline · health · attempt 1 / exchange 1 · response", reference),)
            )
            await app.push_screen(inspector)
            await _wait(pilot, lambda: not inspector.busy)
            assert (inspector.next_start,) == (BODY_PAGE_BYTES,)
            await pilot.press("n")
            await _wait(pilot, lambda: not inspector.busy)
            assert int(inspector.start) == BODY_PAGE_BYTES
            assert inspector.next_start is None
            assert str(len(payload)) in str(
                inspector.query_one("#capture-status", Caption).render()
            )
            await pilot.press("p")
            await _wait(pilot, lambda: not inspector.busy)
            assert inspector.start == 0
            _button(inspector, "json").press()
            await _wait(pilot, lambda: isinstance(inspector.page, StructuredJsonPage))
            table = inspector.query_one(Table)
            table.focus()
            table.move_cursor(row=1)
            await pilot.press("enter")
            await _wait(pilot, lambda: inspector.pointer == "/user" and not inspector.busy)
            assert [str(row.cells[0]) for row in table.records] == [
                "/user",
                "/user/id",
                "/user/name",
            ]
            await pilot.press("b")
            await _wait(pilot, lambda: not inspector.busy)
            assert str(inspector.pointer) == ""
            _button(inspector, "curl").press()
            await _wait(
                pilot,
                lambda: any(
                    button.name == "copy" and not button.disabled
                    for button in inspector.query(Button)
                ),
            )
            exported = inspector.curl_export
            assert exported is not None
            command = exported.command
            assert "curl --disable" in command
            assert "https://example.test/items" in command
            _button(inspector, "copy").press()
            await pilot.pause()
            assert app.clipboard == command
            inspector._expire(inspector.generation)
            assert inspector.curl_export is None
            assert inspector.query_one(DiffView).document is None
            await pilot.press("escape")
            assert app.screen is not inspector

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(80, 30), (160, 52)])
def test_sse_json_event_drilldown_and_invalid_json_are_explicit(
    tmp_path: Path, size: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)

    async def scenario() -> None:
        access, reference = await _access(
            tmp_path,
            b'id: 42\nevent: token\ndata: {"delta":"hello"}\n\nevent: done\ndata: ok\n\n',
            "text/event-stream",
        )
        app = ComparoApp(Path("examples/quickstart"), preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=size) as pilot:
            inspector = CaptureInspector(
                access, (("Candidate · stream · attempt 1 / exchange 1 · response", reference),)
            )
            await app.push_screen(inspector)
            await _wait(pilot, lambda: not inspector.busy)
            _button(inspector, "sse").press()
            await _wait(pilot, lambda: isinstance(inspector.page, StructuredSsePage))
            assert len(inspector.query_one(Table).records) == 2
            app.save_screenshot(f"capture-{size[0]}.svg", path=str(tmp_path))
            inspector.query_one(Table).focus()
            await pilot.press("enter")
            await _wait(pilot, lambda: isinstance(inspector.page, StructuredJsonPage))
            assert (inspector.event_index,) == (0,)
            assert inspector.query_one(Table).records[1].cells[0] == "/delta"
            await pilot.press("b")
            await _wait(pilot, lambda: isinstance(inspector.page, StructuredSsePage))
            assert inspector.event_index is None
            _button(inspector, "json").press()
            await _wait(pilot, lambda: inspector.mode == "json" and not inspector.busy)
            assert "unavailable" in str(inspector.query_one("#capture-status", Caption).render())
            assert inspector.query_one(Table).records == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("host", ["LAUNCH", "REPORTS"])
def test_shared_analyze_opens_inspector_and_restores_its_selection(
    tmp_path: Path, host: str
) -> None:
    async def scenario() -> None:
        project = _project(tmp_path)
        await _seed(project, count=1)
        app = ComparoApp(project, preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=(120, 40)) as pilot:
            launch = app.query_one(LaunchWorkspace)
            reports = app.query_one(ReportsWorkspace)
            await pilot.press("3")
            await _wait(pilot, lambda: launch.phase == "draft" and reports.projection is not None)
            projection = reports.projection
            assert projection is not None
            if host == "LAUNCH":
                launch.phase = "finished"
                launch.analysis_open = True
                results = launch.query_one(ResultsView)
                results.load(projection)
                launch._update_controls()
            else:
                reports.action_open()
                results = reports.query_one(ResultsView)
            app.action_workspace(host)
            comparison = projection.comparisons[0]
            results.open_reference(comparison.reference)
            results.action_evidence()
            await pilot.pause()
            selected = results.selected
            await pilot.press("i")
            inspector = app.screen
            assert isinstance(inspector, CaptureInspector)
            await _wait(pilot, lambda: not inspector.busy)
            assert inspector.captures[0][0].startswith("Baseline")
            assert any(label.startswith("Candidate") for label, _ in inspector.captures)
            assert all(
                reference.artifact_id == projection.summary.artifact_id
                for _, reference in inspector.captures
            )
            await pilot.press("escape")
            assert results.selected == selected
            assert str(app.state.active_screen) == host
            assert await app.persist_preferences(
                replace(app.preferences, keymap={"analyze.fields": "f8"})
            )
            await pilot.press("f8")
            assert results.lens == "fields"
            assert "Fields f8" in str(results.query_one("#result-view-keys", Caption).render())
            assert app.backend.runs.count == 0

    asyncio.run(scenario())
