"""Bounded Matrix previews, exact occurrences, masked values and UI selection."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import msgspec
import pytest
from textual.pilot import Pilot

from comparo.application import ResolvedValueInspectionTarget
from comparo.application import ResourceCoordinate
from comparo.application.matrix_inspection import MatrixBrowseSelection
from comparo.application.matrix_inspection import inspect_matrix_cases
from comparo.application.production_execution import ProductionExecutionSettings
from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.application.resolved_inspection import MatrixCaseSelection
from comparo.application.resolved_inspection import ResolvedValueProjectionLimits
from comparo.application.value_inspection import ValueInspection
from comparo.application.value_inspection import inspect_value
from comparo.policy import PolicyLoadLimits
from comparo.tui.app import ComparoApp
from comparo.tui.backend import RESOLVED_PROJECTION_LIMITS
from comparo.tui.backend import TuiBackend
from comparo.tui.components.inspector import InspectorColumns
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.resources.matrix_browser import MatrixBrowser
from comparo.tui.components.table import Table
from comparo.tui.matrix_backend import inspect_matrix
from comparo.tui.models.explorer import build_catalog
from comparo.tui.workspaces.explorer import ExplorerWorkspace

PROJECT = Path(__file__).parents[2] / "examples" / "tui-showcase"


def _selection(
    backend: TuiBackend, matrix: str = "audiences", request: str = "catalog"
) -> MatrixBrowseSelection:
    catalog = build_catalog(backend.load_workspace())
    assert catalog is not None

    def coord(kind: str, resource_id: str) -> ResourceCoordinate:
        return next(
            entry.summary.coordinate
            for entry in catalog.entries.values()
            if entry.summary.coordinate.kind == kind
            and entry.summary.coordinate.resource_id == resource_id
        )

    return MatrixBrowseSelection(
        coord("Matrix", matrix), coord("Request", request), coord("Environment", "baseline")
    )


def _large_project(root: Path) -> Path:
    data = root / ".comparo"
    data.mkdir()

    def document(kind: str, identity: str, spec: dict[str, object]) -> str:
        return json.dumps(
            {
                "apiVersion": "comparo/v1alpha1",
                "kind": kind,
                "metadata": {"id": identity, "name": identity},
                "spec": spec,
            }
        )

    (root / "comparo.yaml").write_text(
        document(
            "Project",
            "browser",
            {
                "data": ".comparo",
                "secrets": {"token": "MATRIX-SECRET-CANARY"},
                "defaults": {"trials": 3},
            },
        )
    )
    cases = [
        {
            "id": f"case-{i}",
            "values": {"number": i, **({"secret": {"$secret": "token"}} if i == 51 else {})},
        }
        for i in range(52)
    ]
    (data / "resources.yaml").write_text(
        "\n---\n".join(
            (
                document("Environment", "baseline", {"baseUrl": "http://127.0.0.1:9"}),
                document("Matrix", "many", {"cases": cases}),
                document(
                    "Matrix",
                    "companion",
                    {
                        "cases": [
                            {"id": "one", "values": {"other": 1}},
                            {"id": "two", "values": {"other": 2}},
                        ]
                    },
                ),
                document(
                    "Request",
                    "catalog",
                    {
                        "request": {"method": "GET", "endpoint": "/"},
                        "matrices": [{"$use": "many"}, {"$use": "companion"}],
                    },
                ),
                document(
                    "Request",
                    "second",
                    {
                        "request": {"method": "GET", "endpoint": "/second"},
                        "matrices": [{"$use": "many"}],
                    },
                ),
            )
        )
    )
    return root


def test_case_pages_and_companion_ordinals_match_the_application_iterator(tmp_path: Path) -> None:
    async def inspect() -> None:
        backend = TuiBackend(_large_project(tmp_path))
        selection = _selection(backend, "many")
        prepared, controls = backend._prepare_resolved_context(
            "baseline", "Matrix", "many", "catalog"
        )
        assert prepared.handle is not None
        assert prepared.review is not None
        try:
            plan = prepared.review.plan
            first = inspect_matrix_cases(plan, selection)
            assert len(first.cases) == 50
            assert first.total_cases == 52
            assert first.matching_occurrences == 6
            for start, case in ((0, "case-0"), (0, "case-49"), (50, "case-51")):
                for match in range(6):
                    window = inspect_matrix_cases(
                        plan, replace(selection, start=start, case_id=case, occurrence=match)
                    )
                    assert window.execution_ordinal is not None
                    actual = backend.compare.list_occurrences(
                        prepared.handle,
                        controls,
                        start_ordinal=window.execution_ordinal,
                        maximum_occurrences=1,
                    ).occurrences[0]
                    assert actual.matrices[0].case_id == case
                    assert actual.matrices[1].case_id == ("one" if match < 3 else "two")
                    assert actual.trial_ordinal == match % 3
            for invalid in (
                replace(selection, start=52),
                replace(selection, occurrence=6),
                replace(selection, case_id="case-51"),
                replace(selection, binding="absent"),
            ):
                with pytest.raises(ValueError, match="Matrix"):
                    inspect_matrix_cases(plan, invalid)
        finally:
            backend.compare.discard(prepared.handle)
            await backend.shutdown()

    asyncio.run(inspect())


def test_preview_masks_secrets_and_rejects_changed_revisions(tmp_path: Path) -> None:
    async def inspect() -> None:
        backend = TuiBackend(_large_project(tmp_path))
        selection = _selection(backend, "many")
        try:
            last = await inspect_matrix(backend, replace(selection, start=50, case_id="case-51"))
            assert last.values is not None
            assert "MATRIX-SECRET-CANARY" not in repr(last)
            assert any(
                item.inspection is not None and item.inspection.masked
                for item in last.values.results
            )
            for field in ("matrix", "subject", "environment"):
                coordinate = msgspec.structs.replace(
                    getattr(selection, field), fingerprint="0" * 64
                )
                result = await inspect_matrix(backend, replace(selection, **{field: coordinate}))
                assert result.values is None
                assert result.message is not None
            # Handles were released after both successful and rejected inspections.
            assert (await inspect_matrix(backend, selection)).values is not None
        finally:
            await backend.shutdown()

    asyncio.run(inspect())


def test_compiled_case_target_cannot_resolve_another_case_or_revision() -> None:
    async def inspect() -> None:
        backend = TuiBackend(PROJECT)
        selection = _selection(backend)
        prepared, controls = backend._prepare_resolved_context(
            "baseline", "Matrix", "audiences", "catalog"
        )
        assert prepared.handle is not None
        try:
            for case in (
                MatrixCaseSelection(binding="audiences", case_id="us-pro"),
                MatrixCaseSelection(binding="locales", case_id="eu-free"),
            ):
                target = ResolvedValueInspectionTarget(
                    resource=selection.matrix,
                    environment=selection.environment,
                    field_path=(),
                    execution_ordinal=0,
                    matrix_case=case,
                    limits=RESOLVED_PROJECTION_LIMITS,
                    start_node=0,
                    maximum_nodes=64,
                )
                result = await backend.compare.inspect_resolved_value(
                    prepared.handle, controls, target
                )
                assert result.inspection is None
                assert result.diagnostics[0].code == "resolution.inspect.matrix-case-mismatch"
        finally:
            backend.compare.discard(prepared.handle)
            await backend.shutdown()

    asyncio.run(inspect())


@pytest.mark.parametrize("size", [(160, 52), (120, 40), (80, 30)])
def test_browser_inspects_cases_without_execution_context_and_restores_source(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async def journey() -> None:
        app = ComparoApp(PROJECT, preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=size) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            await _wait_until(pilot, lambda: workspace.catalog is not None)
            assert workspace.catalog is not None
            workspace.selected = next(
                entry.key
                for entry in workspace.catalog.entries.values()
                if entry.summary.coordinate.kind == "Matrix"
                and entry.summary.coordinate.resource_id == "audiences"
            )
            await pilot.pause()
            workspace.action_toggle_resource_resolution()
            await _wait_until(
                pilot,
                lambda: (
                    bool(app.query(MatrixBrowser))
                    and app.query_one(MatrixBrowser).result is not None
                ),
            )
            browser = app.query_one(MatrixBrowser)
            assert browser.result is not None
            assert browser.result.matrix is not None
            assert browser.result.matrix.selected == "eu-free"
            assert not browser.query("Select")
            assert not browser.query("#matrix-prev-match, #matrix-next-match")
            for columns in browser.query(InspectorColumns):
                assert columns.has_class("stacked") is (size[0] < 140)
            browser.query_one(Table).move_cursor(row=2)
            await _wait_until(
                pilot,
                lambda: (
                    browser.result is not None
                    and browser.result.matrix is not None
                    and browser.result.matrix.selected == "us-pro"
                ),
            )
            workspace.action_resource_view("source")
            await pilot.pause()
            workspace.action_resource_view("overview")
            await _wait_until(
                pilot,
                lambda: (
                    bool(app.query(MatrixBrowser))
                    and app.query_one(MatrixBrowser).result is not None
                ),
            )
            assert app.query_one(MatrixBrowser).cursor.case_id == "us-pro"

    asyncio.run(journey())


def test_multiple_consumers_do_not_block_cases_and_paging_masks_secrets(tmp_path: Path) -> None:
    from comparo.tui.components.actionbar import ActionBar

    async def journey() -> None:
        app = ComparoApp(_large_project(tmp_path), preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=(160, 52)) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            await _wait_until(pilot, lambda: workspace.catalog is not None)
            assert workspace.catalog is not None
            workspace.selected = next(
                entry.key
                for entry in workspace.catalog.entries.values()
                if entry.summary.coordinate.kind == "Matrix"
                and entry.summary.coordinate.resource_id == "many"
            )
            await pilot.pause()
            workspace.action_toggle_resource_resolution()
            await _wait_until(
                pilot,
                lambda: (
                    bool(app.query(MatrixBrowser))
                    and app.query_one(MatrixBrowser).result is not None
                ),
            )
            browser = app.query_one(MatrixBrowser)
            assert browser.result is not None
            assert browser.result.matrix is not None
            assert browser.result.matrix.total == 52
            bar = browser.query_one(ActionBar)
            browser.on_action_bar_requested(ActionBar.Requested(bar, "next-page"))
            await _wait_until(
                pilot,
                lambda: (
                    browser.result is not None
                    and browser.result.matrix is not None
                    and browser.result.matrix.start == 50
                ),
            )
            browser.query_one(Table).move_cursor(row=1)
            await _wait_until(
                pilot,
                lambda: (
                    browser.result is not None
                    and browser.result.matrix is not None
                    and browser.result.matrix.selected == "case-51"
                ),
            )
            assert browser.result is not None
            assert any(field.node and field.node.masked for field in browser.result.fields)
            assert "MATRIX-SECRET-CANARY" not in msgspec.json.encode(browser.result).decode()

    asyncio.run(journey())


def test_stale_preview_cannot_overwrite_new_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import comparo.tui.matrix_navigation as navigation

    async def journey() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        original = inspect_value

        async def delayed(
            workspace: ProjectWorkspaceRequest,
            resource: ResourceCoordinate,
            environment: ResourceCoordinate | None,
            *,
            policy_path: Path | None,
            policy_limits: PolicyLoadLimits,
            settings: ProductionExecutionSettings,
            limits: ResolvedValueProjectionLimits,
            case_id: str | None = None,
            start_case: int = 0,
        ) -> ValueInspection:
            result = await original(
                workspace,
                resource,
                environment,
                policy_path=policy_path,
                policy_limits=policy_limits,
                settings=settings,
                limits=limits,
                case_id=case_id,
                start_case=start_case,
            )
            if case_id == "eu-pro":
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
            return result

        monkeypatch.setattr(navigation, "inspect_value", delayed)
        app = ComparoApp(PROJECT, preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=(160, 52)) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            await _wait_until(pilot, lambda: workspace.catalog is not None)
            assert workspace.catalog is not None
            workspace.selected = next(
                entry.key
                for entry in workspace.catalog.entries.values()
                if entry.summary.coordinate.kind == "Matrix"
                and entry.summary.coordinate.resource_id == "audiences"
            )
            await pilot.pause()
            workspace.action_toggle_resource_resolution()
            await _wait_until(
                pilot,
                lambda: (
                    bool(app.query(MatrixBrowser))
                    and app.query_one(MatrixBrowser).result is not None
                ),
            )
            browser = app.query_one(MatrixBrowser)
            assert browser.result is not None
            assert browser.result.matrix is not None
            table = browser.query_one(Table)
            table.move_cursor(row=1)
            await asyncio.wait_for(started.wait(), 3)
            assert not browser.query_one(KeyValueList).entries
            table.move_cursor(row=2)
            await _wait_until(
                pilot,
                lambda: (
                    browser.result is not None
                    and browser.result.matrix is not None
                    and browser.result.matrix.selected == "us-pro"
                ),
            )
            release.set()
            await pilot.pause()
            assert browser.cursor.case_id == "us-pro"

    asyncio.run(journey())


async def _wait_until(pilot: Pilot[None], predicate: Callable[[], bool]) -> None:
    for _ in range(300):
        if predicate():
            return
        await pilot.pause(0.01)
    pytest.fail("Inspection did not finish")
