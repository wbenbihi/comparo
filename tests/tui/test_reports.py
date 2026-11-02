"""Authenticated report archive, shared Analyze, keyboard navigation and export journeys."""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import msgspec
import pytest
from tests.adapters.artifacts.test_local_store import _stored_artifact
from tests.core.artifacts.test_capture_compiler import _limits
from tests.tui.test_launch_workspace import _project
from tests.tui.test_launch_workspace import _wait
from textual.widgets import Button

from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.application import ReportArchiveListing
from comparo.application import ResultProjection
from comparo.core.artifacts import ArtifactDefinition
from comparo.core.artifacts import ArtifactFactOutcome
from comparo.core.artifacts import ArtifactGate
from comparo.core.artifacts import ArtifactGateVerdict
from comparo.core.artifacts import ArtifactMetric
from comparo.core.artifacts import ArtifactRunAnnotations
from comparo.core.artifacts import ArtifactVcsProvenance
from comparo.tui.app import ComparoApp
from comparo.tui.backend import TuiBackend
from comparo.tui.components.diffview import DiffView
from comparo.tui.components.filter import FilterInput
from comparo.tui.components.reportarchive import ReportArchive
from comparo.tui.components.reportexport import ReportExport
from comparo.tui.components.results import ResultsView
from comparo.tui.components.selectfield import ChoicePicker
from comparo.tui.components.selectfield import SelectField
from comparo.tui.components.typography import Caption
from comparo.tui.components.valueinput import ValueInput
from comparo.tui.models.reports import ArchiveFilters
from comparo.tui.types import ComparoScreen
from comparo.tui.workspaces.launch import LaunchWorkspace
from comparo.tui.workspaces.reports import ReportsWorkspace


async def _seed(project: Path, count: int = 12) -> None:
    root = project / ".comparo/artifacts"
    root.mkdir(mode=0o700, exist_ok=True)
    artifact, blobs = await _stored_artifact()
    sink = LocalArtifactBundleSink(root)
    now = datetime.now().astimezone()
    gate = ArtifactDefinition(
        namespace="default", kind="GateProfile", resource_id="release-gate", fingerprint="b" * 64
    )
    try:
        for index in range(count):
            created = int(
                (now - timedelta(days=index // 4, minutes=index * 20)).timestamp() * 1_000_000_000
            )
            profile = ArtifactDefinition(
                namespace="default",
                kind="ExecutionProfile",
                resource_id="release-check" if index % 2 == 0 else "smoke-run",
                fingerprint="c" * 64,
            )
            failed = index % 3 == 0
            diff = msgspec.structs.replace(
                artifact.comparisons[0].diffs[0],
                outcome=ArtifactFactOutcome.DRIFTED if failed else ArtifactFactOutcome.EQUIVALENT,
            )
            stored = msgspec.structs.replace(
                artifact,
                metadata=msgspec.structs.replace(
                    artifact.metadata,
                    artifact_id=f"report-{index:02d}",
                    run_id=f"run-{index:02d}",
                    created_unix_nanoseconds=created,
                ),
                completion=msgspec.structs.replace(
                    artifact.completion,
                    instant_unix_nanoseconds=created + (20 + index) * 1_000_000_000,
                ),
                provenance=msgspec.structs.replace(artifact.provenance, execution_profile=profile),
                gate=ArtifactGate(
                    verdict=ArtifactGateVerdict.FAILED if failed else ArtifactGateVerdict.PASSED,
                    profile=gate,
                    reasons=("blocking-drift",) if failed else (),
                    coverage=(),
                    assessments=(),
                ),
                comparisons=(msgspec.structs.replace(artifact.comparisons[0], diffs=(diff,)),),
                summary=msgspec.structs.replace(
                    artifact.summary,
                    diffs=(ArtifactMetric(name="drifted" if failed else "equivalent", value=1),),
                ),
                resources=tuple(
                    sorted(
                        (*artifact.resources, profile, gate),
                        key=lambda row: (row.namespace, row.kind, row.resource_id),
                    )
                ),
                annotations=ArtifactRunAnnotations(
                    title="Release comparison" if index % 2 == 0 else "Smoke check",
                    tags=("release",) if index % 2 == 0 else ("smoke",),
                    vcs=ArtifactVcsProvenance(
                        provider="git", repository="comparo", revision="9b7e3a1", branch="main"
                    ),
                ),
            )
            await sink.persist(stored, blobs, _limits())
    finally:
        sink.close()


def _capture(app: ComparoApp, name: str) -> None:
    destination = os.getenv("COMPARO_SCREENSHOTS")
    if destination:
        Path(destination).mkdir(parents=True, exist_ok=True)
        app.save_screenshot(f"{name}.svg", path=destination)


@pytest.mark.parametrize("size", [(80, 30), (120, 40), (160, 52)])
def test_saved_reports_reuse_analyze_and_preserve_archive_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: tuple[int, int]
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)

    async def scenario() -> None:
        project = _project(tmp_path)
        await _seed(project)
        app = ComparoApp(project)
        async with app.run_test(size=size) as pilot:
            await pilot.press("3")
            reports = app.query_one(ReportsWorkspace)
            await _wait(pilot, lambda: reports.projection is not None)
            assert len(reports.listing.entries) == 12
            assert app.backend.runs.count == 0
            archive = reports.query_one(ReportArchive)
            assert archive.highlighted_id == "report-00"
            assert len(archive.entries) == 12
            assert any(item.disabled and item.label.startswith("Today") for item in archive.items)
            await pilot.pause()
            _capture(app, f"reports-{size[0]}")
            if size[0] == 80:
                await pilot.press("p")
                assert reports.has_class("previewing")
                await pilot.pause()
                _capture(app, "reports-preview-80")
                await pilot.press("escape")
                assert not reports.has_class("previewing")
            archive.focus()
            await pilot.press("down")
            await _wait(
                pilot,
                lambda: (
                    reports.projection is not None
                    and reports.projection.summary.artifact_id == "report-01"
                ),
            )
            assert archive.highlighted_id == "report-01"
            await pilot.press("c")
            assert app.clipboard == "report-01"
            await pilot.press("enter")
            assert bool(reports.analysis_open)
            assert app.state.active_screen is ComparoScreen.REPORTS
            results = reports.query_one(ResultsView)
            assert type(results) is ResultsView
            assert results.projection is reports.projection
            assert app.query_one(LaunchWorkspace).run_handle is None
            await pilot.press("alt+2")
            assert results.lens == "requests"
            await pilot.press("right_square_bracket")
            assert results.lens == "rules"
            await pilot.press("/", "left_square_bracket", "right_square_bracket")
            assert results.query_one(FilterInput).value == "[]"
            assert results.lens == "rules"
            await pilot.press("escape", "escape")
            projection = reports.projection
            assert projection is not None
            comparison = projection.comparisons[0]
            results.open_reference(comparison.reference)
            results._compare_bodies()
            await _wait(pilot, lambda: results.query_one(DiffView).document is not None)
            document = results.query_one(DiffView).document
            assert document is not None
            assert "right" in document.new_text
            assert "right" in document.old_text
            _capture(app, f"reports-analysis-{size[0]}")
            await pilot.press("alt+left")
            assert not reports.analysis_open
            assert archive.highlighted_id == "report-01"
            assert app.focused is archive
            assert app.backend.runs.count == 0
            assert reports.region.right <= size[0]
            assert reports.region.bottom <= size[1]

    asyncio.run(scenario())


def test_report_filters_pagination_export_and_invalid_archive_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from comparo.tui.workspaces import reports as module

    monkeypatch.setattr(module, "PAGE_SIZE", 5)

    async def scenario() -> None:
        project = _project(tmp_path)
        await _seed(project)
        broken = project / ".comparo/artifacts/broken"
        broken.mkdir(mode=0o700)
        app = ComparoApp(project)
        async with app.run_test(size=(160, 52)) as pilot:
            await pilot.press("3")
            reports = app.query_one(ReportsWorkspace)
            await _wait(pilot, lambda: reports.projection is not None)
            assert len(reports.listing.failures) == 1
            assert "broken" in str(reports.query_one("#reports-failures", Caption).render())
            assert len(reports.query_one(ReportArchive).entries) == 5
            next_page = next(
                button
                for button in reports.query("#reports-page-actions Button")
                if isinstance(button, Button) and button.name == "next"
            )
            await pilot.click(next_page)
            await _wait(
                pilot,
                lambda: (
                    reports.projection is not None
                    and reports.projection.summary.artifact_id == "report-05"
                ),
            )
            assert reports.page == 1
            status = reports.query_one("#reports-notice", Caption)
            field = reports.query_one("#reports-status", SelectField)
            field.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoicePicker)
            await pilot.press("/", *"fail", "enter", "enter")
            await pilot.pause()
            await _wait(pilot, lambda: reports.projection is not None)
            assert field.value == "failed"
            assert reports.page == 0
            assert len(reports.query_one(ReportArchive).entries) == 4
            await pilot.press("/", *"no-such-report")
            await pilot.pause()
            assert not reports.query_one(ReportArchive).entries
            assert reports.has_class("empty-archive")
            clear = next(
                button
                for button in reports.query("#reports-page-actions Button")
                if isinstance(button, Button) and button.name == "clear"
            )
            await pilot.click(clear)
            await _wait(pilot, lambda: reports.projection is not None)
            assert field.value == "all"
            await pilot.press("e")
            await pilot.pause()
            assert isinstance(app.screen, ReportExport)
            output = tmp_path / "saved-report.json"
            app.screen.query_one(ValueInput).value = str(output)
            save = next(button for button in app.screen.query(Button) if button.name == "save")
            await pilot.click(save)
            await _wait(pilot, output.exists)
            assert b"report-00" in output.read_bytes()
            original = output.read_bytes()
            await pilot.press("e")
            await pilot.pause()
            app.screen.query_one(ValueInput).value = str(output)
            save = next(button for button in app.screen.query(Button) if button.name == "save")
            await pilot.click(save)
            await _wait(pilot, lambda: "already exists" in str(status.render()))
            assert output.read_bytes() == original
            await pilot.press("e")
            await pilot.pause()
            dialog = app.screen
            assert isinstance(dialog, ReportExport)
            dialog.query_one(ValueInput).value = str(tmp_path / "formatted.json")
            dialog.query_one(SelectField).focus()
            await pilot.press("enter", "/", *"markdown", "enter", "enter")
            await pilot.pause()
            assert app.screen is dialog
            markdown = tmp_path / "formatted.md"
            assert dialog.query_one(ValueInput).value == str(markdown)
            save = next(button for button in dialog.query(Button) if button.name == "save")
            await pilot.click(save)
            await _wait(pilot, lambda: "Exported" in str(status.render()))
            assert r"report\-00" in markdown.read_text()

    asyncio.run(scenario())


def test_empty_archive_and_backend_reads_do_not_create_or_execute_runs(tmp_path: Path) -> None:
    project = _project(tmp_path)
    backend = TuiBackend(project)
    assert backend.list_reports() == ReportArchiveListing(entries=(), failures=())
    assert not (project / ".comparo/artifacts").exists()
    assert backend.runs.count == 0

    async def scenario() -> None:
        app = ComparoApp(project)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.press("3")
            reports = app.query_one(ReportsWorkspace)
            await _wait(pilot, lambda: not reports.archive_loading)
            assert reports.projection is None
            assert reports.has_class("empty-archive")
            assert not reports.check_action("open", ())
            assert "No saved reports" in str(reports.query_one("#reports-empty", Caption).render())
            _capture(app, "reports-empty-80")

    asyncio.run(scenario())


def test_archive_filters_use_metadata_and_invalid_reports_are_explicit(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = _project(tmp_path)
        await _seed(project, 4)
        backend = TuiBackend(project)
        entries = backend.list_reports().entries
        now = datetime.now().astimezone()
        assert [
            row.bundle_id
            for row in entries
            if ArchiveFilters(
                profile="release-check", tag="release", environment="prod", date="7"
            ).matches(row, now)
        ] == ["report-00", "report-02"]
        assert not any(ArchiveFilters(environment="other").matches(row, now) for row in entries)
        assert (
            len([row for row in entries if ArchiveFilters(text="9b7e3a1").matches(row, now)]) == 4
        )
        for row in entries:
            (project / ".comparo/artifacts" / row.bundle_id / "results.json").write_text("{}")
        app = ComparoApp(project)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("3")
            reports = app.query_one(ReportsWorkspace)
            await _wait(pilot, lambda: not reports.archive_loading)
            assert len(reports.listing.failures) == 4
            assert not reports.listing.entries
            assert reports.projection is None
            assert "No readable reports" in str(
                reports.query_one("#reports-empty", Caption).render()
            )
            assert not reports.check_action("open", ())
            _capture(app, "reports-invalid-120")

    asyncio.run(scenario())


def test_slow_report_preview_cannot_replace_a_newer_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        project = _project(tmp_path)
        await _seed(project, 3)
        app = ComparoApp(project)
        started = threading.Event()
        release = threading.Event()
        original = app.backend.read_report

        def delayed(bundle_id: str) -> ResultProjection:
            if bundle_id == "report-01":
                started.set()
                release.wait(5)
            return original(bundle_id)

        monkeypatch.setattr(app.backend, "read_report", delayed)
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press("3")
                reports = app.query_one(ReportsWorkspace)
                await _wait(pilot, lambda: reports.projection is not None)
                reports.query_one(ReportArchive).focus()
                await pilot.press("down")
                await _wait(pilot, started.is_set)
                await pilot.press("down")
                await _wait(pilot, lambda: reports.projection is not None)
                current = reports.projection
                assert current is not None
                assert current.summary.artifact_id == "report-02"
                await pilot.press("1")
                release.set()
                await pilot.pause(0.1)
                assert reports.projection is current
                assert app.state.active_screen is ComparoScreen.EXPLORE
                await pilot.press("3")
                await _wait(
                    pilot, lambda: not reports.archive_loading and reports.projection is not None
                )
                assert reports.query_one(ReportArchive).highlighted_id == "report-02"
        finally:
            release.set()

    asyncio.run(scenario())


def test_archive_error_is_retryable_without_exception_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        project = _project(tmp_path)
        await _seed(project, 1)
        app = ComparoApp(project)
        original = app.backend.list_reports

        def unavailable() -> ReportArchiveListing:
            raise RuntimeError("PRIVATE ERROR PAYLOAD")

        monkeypatch.setattr(app.backend, "list_reports", unavailable)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.press("3")
            reports = app.query_one(ReportsWorkspace)
            await _wait(pilot, lambda: not reports.archive_loading)
            notice = reports.query_one("#reports-notice", Caption)
            assert "Archive unavailable" in str(notice.render())
            assert "PRIVATE ERROR PAYLOAD" not in str(notice.render())
            monkeypatch.setattr(app.backend, "list_reports", original)
            retry = reports.query_one("#reports-page-actions Button", Button)
            await pilot.click(retry)
            await _wait(pilot, lambda: reports.projection is not None)
            assert len(reports.listing.entries) == 1

    asyncio.run(scenario())
