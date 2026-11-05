"""Resource-specific inspector selection and responsive layout journeys."""

import asyncio
import os
from pathlib import Path

import pytest
from textual.widgets import TabbedContent

from comparo.tui.app import ComparoApp
from comparo.tui.components.inspector import InspectorColumns
from comparo.tui.components.inspector import RecordInspector
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.resources.schema_tree import SchemaPropertyBrowser
from comparo.tui.components.resources.schema_tree import SchemaPropertyTree
from comparo.tui.components.resources.schema_tree import property_key
from comparo.tui.components.table import Table
from comparo.tui.models.resource_presenters import schema_overview
from comparo.tui.preferences import Preferences
from comparo.tui.preferences import save_preferences
from comparo.tui.workspaces.explorer import ExplorerWorkspace

PROJECT = Path(__file__).parents[2] / "examples" / "tui-showcase"


@pytest.mark.parametrize(
    ("size", "theme", "no_color"),
    [
        ((160, 52), "comparo-dark", False),
        ((120, 40), "comparo-light", False),
        ((80, 30), "comparo-dark", True),
    ],
)
def test_kind_inspectors_select_details_and_stack_without_losing_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: tuple[int, int],
    theme: str,
    no_color: bool,
) -> None:
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    preferences = tmp_path / "preferences.json"
    save_preferences(preferences, Preferences(theme=theme))
    output = Path(os.environ.get("COMPARO_SCREENSHOTS", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)

    async def journey() -> None:
        app = ComparoApp(PROJECT, preference_path=preferences)
        async with app.run_test(size=size) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            for _ in range(200):
                if workspace.catalog is not None:
                    break
                await pilot.pause(0.01)
            assert workspace.catalog is not None
            for kind in (
                "Request",
                "Schema",
                "Environment",
                "Value",
                "ExecutionProfile",
                "AssertionProfile",
                "DiffProfile",
                "GateProfile",
                "SafetyProfile",
                "RedactionProfile",
                "ArtifactProfile",
                "Scenario",
            ):
                entry = next(
                    item
                    for item in workspace.catalog.entries.values()
                    if item.summary.coordinate.kind == kind
                )
                workspace.selected = entry.key
                await pilot.pause()
                if kind == "Request":
                    assert app.query_one(TabbedContent).active == "request-summary"
                if kind == "Schema":
                    overview = schema_overview(entry)
                    tree = app.query_one(SchemaPropertyTree)
                    assert tree.size.height == 22
                    prop = overview.properties[-1]
                    tree.move_cursor(tree._by_id[property_key(prop)])
                    tree.focus()
                    await pilot.pause()
                    fields = app.query_one(SchemaPropertyBrowser).query_one(KeyValueList).entries
                    assert str(fields[0].value) == " / ".join(prop.path)
                    assert str(
                        next(field.value for field in fields if field.key == "Definition")
                    ).startswith("#/")
                    # Branch nodes are inspectable as well as expandable.
                    branch = next(p for p in overview.properties if p.type in {"array", "object"})
                    tree.move_cursor(tree._by_id[property_key(branch)])
                    await pilot.pause()
                    fields = app.query_one(SchemaPropertyBrowser).query_one(KeyValueList).entries
                    assert str(fields[0].value) == " / ".join(branch.path)
                inspectors = list(app.query(RecordInspector))
                if inspectors:
                    inspector = inspectors[0]
                    table = inspector.query_one(Table)
                    table.focus()
                    table.move_cursor(row=len(table.records) - 1)
                    await pilot.pause()
                    assert (
                        inspector.query_one(KeyValueList).entries
                        == inspector._details[table.records[-1].id]
                    )
                    assert all(field.key for field in inspector.query_one(KeyValueList).entries)
                    assert inspector.size.height < size[1]
                _assert_columns(app, size[0])
                app.save_screenshot(
                    f"{kind.lower()}-{size[0]}-{theme}-{'mono' if no_color else 'color'}.svg",
                    path=str(output),
                )

    asyncio.run(journey())


def _assert_columns(app: ComparoApp, width: int) -> None:
    for columns in app.query(InspectorColumns):
        assert columns.has_class("stacked") is (width < 140)
        visible = [child for child in columns.children if child.display]
        assert all(child.size.width > 10 for child in visible)
        assert all(child.region.right <= columns.region.right for child in visible)
        if len(visible) == 2:
            if width < 140:
                assert visible[1].region.y >= visible[0].region.bottom
            else:
                assert visible[1].region.x >= visible[0].region.right
