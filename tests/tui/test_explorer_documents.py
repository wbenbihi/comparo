"""Real nested document journeys and reference expansion for Explorer."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import TabbedContent

from comparo.tui.app import ComparoApp
from comparo.tui.backend import TuiBackend
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.resources.request_body import RequestBody
from comparo.tui.components.table import Table
from comparo.tui.models.explorer import build_catalog
from comparo.tui.models.resource import InspectionPhase
from comparo.tui.models.schema_document import schema_nodes
from comparo.tui.workspaces.explorer import ExplorerWorkspace

PROJECT = Path(__file__).parents[2] / "examples" / "explorer-inspection"


def test_schema_preserves_items_defs_required_refs_and_stops_cycles() -> None:
    catalog = build_catalog(TuiBackend(PROJECT).load_workspace())
    assert catalog is not None
    entries = tuple(e for e in catalog.entries.values() if e.summary.coordinate.kind == "Schema")
    entry = next(e for e in entries if e.summary.coordinate.resource_id == "deep-order")
    nodes = schema_nodes(entry, entries)
    by_source = {node.source_path: node for node in nodes if node.source == "Schema/deep-order"}
    assert by_source[("properties", "items", "items")].path == ("0..n",)
    assert by_source[("properties", "items", "items", "properties", "sku")].required
    assert by_source[("$defs",)].role == "group"
    assert all(
        node.schema_path[:1] == ("$defs",) or "$ref" in node.schema_path
        for node in nodes
        if node.source_path[:1] == ("$defs",)
    )
    assert any(node.source == "Schema/customer" and node.path == ("street",) for node in nodes)
    assert any(node.reference_state == "cycle" for node in nodes)
    assert len(nodes) < 100


@pytest.mark.parametrize("size", [(160, 52), (120, 40), (80, 30)])
def test_body_json_path_inspector_masks_secrets_and_preserves_siblings(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async def journey() -> None:
        app = ComparoApp(PROJECT, preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=size) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            await ready(pilot, lambda: workspace.catalog is not None)
            assert workspace.catalog is not None
            workspace.selected = next(
                e.key
                for e in workspace.catalog.entries.values()
                if e.summary.coordinate.kind == "Request"
            )
            await pilot.pause()
            workspace.action_toggle_resource_resolution()
            await ready(
                pilot,
                lambda: (
                    workspace.detail is not None
                    and workspace.detail.resolved.phase
                    in {InspectionPhase.READY, InspectionPhase.FAILED}
                ),
            )
            assert workspace.detail is not None
            assert workspace.detail.resolved.phase is InspectionPhase.READY, (
                workspace.detail.resolved.message
            )
            await pilot.pause()
            app.query_one(TabbedContent).active = "request-body"
            await pilot.pause()
            body = app.query_one(RequestBody)
            document = "\n".join(line.text for line in body.lines)
            payload = json.loads(document)
            assert payload["orderId"] == 42
            assert payload["customer"]["address"]["city"] == "Paris"
            assert payload["items"][0]["options"]["enabled"] is True
            assert payload["payment"]["token"] == "<masked>"
            assert "example-secret-never-display" not in document
            index = next(
                i
                for i, line in enumerate(body.lines)
                if line.path == ("request", "body", "payment", "token")
            )
            body.query_one(Table).focus()
            body.query_one(Table).move_cursor(row=index)
            await pilot.pause()
            fields = body.query_one("#request-body-path", KeyValueList).entries
            assert str(fields[0].value) == "$.payment.token"
            assert "secrets.token" in str(fields[2].value)
            assert "resources.yaml:" in str(fields[3].value)

    asyncio.run(journey())


async def ready(pilot: Pilot[None], predicate: Callable[[], bool]) -> None:
    for _ in range(300):
        if predicate():
            return
        await pilot.pause(0.01)
    pytest.fail("Inspection did not become ready")


def test_value_keyboard_resolution_without_environment_or_consumer(tmp_path: Path) -> None:
    data = tmp_path / ".comparo"
    data.mkdir()
    project = {
        "apiVersion": "comparo/v1alpha1",
        "kind": "Project",
        "metadata": {"id": "isolated", "name": "Isolated Value"},
        "spec": {
            "data": ".comparo",
            "secrets": {"token": "VALUE-UI-SECRET-CANARY"},
            "defaults": {"parameters": {"limit": 5}},
        },
    }
    value = {
        "apiVersion": "comparo/v1alpha1",
        "kind": "Value",
        "metadata": {"id": "only", "name": "Only value"},
        "spec": {
            "value": {
                "public": "readable",
                "limit": {"$param": "limit"},
                "secret": {"$secret": "token"},
            }
        },
    }
    (tmp_path / "comparo.yaml").write_text(json.dumps(project))
    (data / "values.yaml").write_text(json.dumps(value))

    async def journey() -> None:
        app = ComparoApp(tmp_path, preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            await ready(pilot, lambda: workspace.catalog is not None)
            assert workspace.catalog is not None
            workspace.selected = next(
                e.key
                for e in workspace.catalog.entries.values()
                if e.summary.coordinate.kind == "Value"
            )
            await pilot.pause()
            assert workspace.check_action("toggle_resource_resolution", ())
            await pilot.press("r")
            assert workspace.detail is not None
            assert workspace.detail.overview_mode.value == "resolved", repr(app.focused)
            await ready(
                pilot,
                lambda: (
                    workspace.detail is not None
                    and workspace.detail.resolved.phase is not InspectionPhase.LOADING
                ),
            )
            assert workspace.detail is not None
            assert workspace.detail.resolved.phase is InspectionPhase.READY, (
                workspace.detail.resolved
            )
            assert workspace.detail.resolved.value is not None
            fields = {f.path: f.value for f in workspace.detail.resolved.value.fields}
            assert fields[("value", "public")] == "readable"
            assert fields[("value", "limit")] == "5"
            assert fields[("value", "secret")] == "<masked>"
            assert "VALUE-UI-SECRET-CANARY" not in app.export_screenshot()

    asyncio.run(journey())
