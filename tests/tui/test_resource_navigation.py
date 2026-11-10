"""Relationship activation and reversible Explorer navigation journeys."""

import asyncio
from dataclasses import replace
from pathlib import Path

import msgspec
import pytest
from textual.widgets import TabbedContent

from comparo.application import ProjectWorkspaceRequest
from comparo.application import inspect_project_workspace
from comparo.application.project_outline import ResourceIdentityCoordinate
from comparo.tui.app import ComparoApp
from comparo.tui.components.explorernavigator import ExplorerNavigator
from comparo.tui.components.explorernavigator import ExplorerResourceTree
from comparo.tui.components.filter import FilterInput
from comparo.tui.components.resourcedetail import OverviewRepresentation
from comparo.tui.components.resourcelinks import OpenResource
from comparo.tui.components.resourcelinks import ResourceLinksTable
from comparo.tui.components.resources.request import RequestResourceDetails
from comparo.tui.components.resources.shared import ResourceDependencyTree
from comparo.tui.models.resource import ResourceRepresentation
from comparo.tui.models.resource_links import linked_entry
from comparo.tui.models.resource_links import logical_identity
from comparo.tui.models.resource_presenters import resource_dependencies
from comparo.tui.workspaces.explorer import ExplorerWorkspace

PROJECT = Path(__file__).parents[2] / "examples" / "tui-showcase"


def test_logical_links_require_namespace_kind_and_unique_catalog_revision() -> None:
    from comparo.tui.models.explorer import build_catalog

    catalog = build_catalog(inspect_project_workspace(ProjectWorkspaceRequest(config=PROJECT)))
    assert catalog is not None
    entry = next(e for e in catalog.entries.values() if e.summary.coordinate.kind == "Matrix")
    target = logical_identity(entry.summary.coordinate)
    assert linked_entry(catalog, target) is entry
    for field in ("namespace", "kind", "resource_id"):
        assert (
            linked_entry(catalog, msgspec.structs.replace(target, **{field: "different"})) is None
        )
    assert linked_entry(None, target) is None
    duplicate = replace(entry, key="another-revision")
    assert (
        linked_entry(replace(catalog, entries={entry.key: entry, duplicate.key: duplicate}), target)
        is None
    )
    edges = resource_dependencies(entry)
    assert edges
    assert all(edge.target.namespace == entry.summary.coordinate.namespace for edge in edges)
    assert all(linked_entry(catalog, edge.target) is not None for edge in edges)


@pytest.mark.parametrize("size", [(160, 52), (120, 40), (80, 30)])
def test_graph_links_restore_filter_cursor_focus_and_history(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async def journey() -> None:
        app = ComparoApp(PROJECT, preference_path=tmp_path / "settings.json")
        async with app.run_test(size=size) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            for _ in range(200):
                if workspace.catalog is not None:
                    break
                await pilot.pause(0.01)
            assert workspace.catalog is not None
            catalog = workspace.catalog
            request = next(
                e
                for e in catalog.entries.values()
                if e.summary.coordinate.kind == "Request"
                and e.summary.coordinate.resource_id == "catalog"
            )
            matrix = next(
                e
                for e in catalog.entries.values()
                if e.summary.coordinate.kind == "Matrix"
                and e.summary.coordinate.resource_id == "audiences"
            )
            navigator = app.query_one(ExplorerNavigator)
            field = navigator.query_one(FilterInput)
            field.value = "Matrix catalog"
            await pilot.pause()
            navigator.query_one(ExplorerResourceTree).reveal(request.key)
            workspace.selected = request.key
            workspace.action_resource_view("dependencies")
            await pilot.pause()
            graph = app.query_one(ResourceDependencyTree)
            edge = next(
                e
                for e in resource_dependencies(request)
                if e.target == logical_identity(matrix.summary.coordinate)
            )
            graph.reveal(edge.key)
            graph.focus()
            await pilot.pause()
            before = navigator.capture_position()
            environment = workspace.execution_environment
            await pilot.press("enter")
            await pilot.pause()
            assert workspace.selected == matrix.key
            assert workspace.detail is not None
            assert str(workspace.detail.representation) == ResourceRepresentation.DEPENDENCIES
            assert field.value == ""
            assert navigator.query_one(ExplorerResourceTree).cursor_id == matrix.key
            assert workspace.execution_environment == environment
            assert app.focused is app.query_one(ResourceDependencyTree)
            assert app.screen.active_bindings["escape"].enabled
            app.save_screenshot(f"graph-linked-{size[0]}.svg", path=str(tmp_path))
            await pilot.press("escape")
            await pilot.pause()
            assert workspace.selected == request.key
            assert field.value == before.filter_text
            assert navigator.query_one(ExplorerResourceTree).cursor_id == request.key
            graph = app.query_one(ResourceDependencyTree)
            assert graph.cursor_id == edge.key
            assert navigator.capture_position().tree.expanded == before.tree.expanded
            assert graph.has_focus
            assert not bool(workspace._navigation.can_go_back)
            # Merely highlighting a target is not another explicit visit.
            graph.action_cursor_down()
            await pilot.pause()
            assert workspace.selected == request.key
            assert not bool(workspace._navigation.can_go_back)

    asyncio.run(journey())


def test_consumer_activation_restores_overview_scroll_and_request_tab(tmp_path: Path) -> None:
    async def journey() -> None:
        app = ComparoApp(PROJECT, preference_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            for _ in range(200):
                if workspace.catalog is not None:
                    break
                await pilot.pause(0.01)
            assert workspace.catalog is not None
            request = next(
                e
                for e in workspace.catalog.entries.values()
                if e.summary.coordinate.kind == "Request"
                and e.summary.coordinate.resource_id == "catalog"
            )
            matrix = next(
                e
                for e in workspace.catalog.entries.values()
                if e.summary.coordinate.kind == "Matrix"
                and e.summary.coordinate.resource_id == "audiences"
            )
            workspace.selected = request.key
            await pilot.pause()
            tabs = app.query_one("#request-tabs", TabbedContent)
            tabs.active = "request-body"
            await pilot.pause()
            workspace.action_resource_view("dependencies")
            await pilot.pause()
            graph = app.query_one(ResourceDependencyTree)
            edge = next(
                e
                for e in resource_dependencies(request)
                if e.target == logical_identity(matrix.summary.coordinate)
            )
            graph.reveal(edge.key)
            graph.focus()
            await pilot.press("enter")
            workspace.action_resource_view("overview")
            await pilot.pause()
            table = app.query_one(ResourceLinksTable)
            table.focus()
            overview = app.query_one(OverviewRepresentation)
            overview.scroll_end(animate=False)
            await pilot.pause()
            scroll = overview.scroll_y
            row = table.cursor_row_id
            assert await pilot.click(table, offset=(3, 1))
            await pilot.pause()
            assert workspace.selected == request.key
            assert workspace.detail is not None
            assert str(workspace.detail.representation) == ResourceRepresentation.OVERVIEW
            assert app.query_one("#request-tabs", TabbedContent).active == "request-body"
            await pilot.press("escape")
            await pilot.pause()
            assert workspace.selected == matrix.key
            assert app.query_one(ResourceLinksTable).has_focus
            assert app.query_one(ResourceLinksTable).cursor_row_id == row
            assert app.query_one(OverviewRepresentation).scroll_y == scroll
            app.save_screenshot("consumer-back.svg", path=str(tmp_path))
            await pilot.press("escape")
            await pilot.pause()
            assert workspace.selected == request.key
            assert workspace.detail is not None
            assert str(workspace.detail.representation) == ResourceRepresentation.DEPENDENCIES
            workspace.action_resource_view("overview")
            await pilot.pause()
            assert app.query_one("#request-tabs", TabbedContent).active == "request-body"

    asyncio.run(journey())


def test_unavailable_links_and_removed_history_do_not_restore_stale_resources(
    tmp_path: Path,
) -> None:
    async def journey() -> None:
        app = ComparoApp(PROJECT, preference_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            for _ in range(200):
                if workspace.catalog is not None:
                    break
                await pilot.pause(0.01)
            assert workspace.catalog is not None
            request = next(
                e
                for e in workspace.catalog.entries.values()
                if e.summary.coordinate.kind == "Request"
                and e.summary.coordinate.resource_id == "catalog"
            )
            workspace.selected = request.key
            workspace.action_resource_view("dependencies")
            await pilot.pause()
            graph = app.query_one(ResourceDependencyTree)
            unavailable = ResourceIdentityCoordinate(
                namespace="missing", kind="Request", resource_id="catalog"
            )
            workspace.on_open_resource(OpenResource(graph, unavailable))
            assert workspace.selected == request.key
            assert not bool(workspace._navigation.can_go_back)
            workspace.on_open_resource(
                OpenResource(graph, logical_identity(request.summary.coordinate))
            )
            assert not bool(workspace._navigation.can_go_back)
            edge = next(e for e in resource_dependencies(request) if e.target.kind == "Matrix")
            workspace.on_open_resource(OpenResource(graph, edge.target))
            target = workspace.selected
            # Reject a second event from the old graph even before it unmounts.
            workspace.on_open_resource(
                OpenResource(graph, logical_identity(request.summary.coordinate))
            )
            assert workspace.selected == target
            await pilot.pause()
            target = workspace.selected
            assert target != request.key
            assert bool(workspace._navigation.can_go_back)
            # A late event from an unmounted previous inspector cannot navigate.
            workspace.on_open_resource(
                OpenResource(graph, logical_identity(request.summary.coordinate))
            )
            assert workspace.selected == target
            workspace.catalog = replace(
                workspace.catalog,
                entries={
                    key: value
                    for key, value in workspace.catalog.entries.items()
                    if key != request.key
                },
            )
            await pilot.pause()
            workspace.action_resource_back()
            await pilot.pause()
            assert workspace.selected != request.key
            assert not bool(workspace._navigation.can_go_back)

    asyncio.run(journey())


def test_back_rebinds_changed_resource_revision_after_reload(tmp_path: Path) -> None:
    async def journey() -> None:
        app = ComparoApp(PROJECT, preference_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            for _ in range(200):
                if workspace.catalog is not None:
                    break
                await pilot.pause(0.01)
            assert workspace.catalog is not None
            request = next(
                entry
                for entry in workspace.catalog.entries.values()
                if entry.summary.coordinate.kind == "Request"
                and entry.summary.coordinate.resource_id == "catalog"
            )
            workspace.selected = request.key
            workspace.action_resource_view("dependencies")
            await pilot.pause()
            graph = app.query_one(ResourceDependencyTree)
            edge = next(
                item for item in resource_dependencies(request) if item.target.kind == "Matrix"
            )
            graph.reveal(edge.key)
            graph.focus()
            await pilot.press("enter")
            await pilot.pause()
            revision = msgspec.structs.replace(request.summary.coordinate, fingerprint="f" * 64)
            updated = replace(
                request,
                key=request.key.partition("#")[0] + "#" + revision.fingerprint,
                summary=msgspec.structs.replace(request.summary, coordinate=revision),
            )
            catalog = workspace.catalog
            workspace.catalog = replace(
                catalog,
                entries={
                    **{key: entry for key, entry in catalog.entries.items() if key != request.key},
                    updated.key: updated,
                },
                sections=tuple(
                    replace(
                        section,
                        entries=tuple(
                            updated if item.key == request.key else item for item in section.entries
                        ),
                    )
                    for section in catalog.sections
                ),
            )
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert workspace.detail is not None
            assert workspace.detail.entry.summary.coordinate == revision
            assert workspace.selected == updated.key
            assert app.query_one(ExplorerResourceTree).cursor_id == updated.key
            assert app.query_one(ResourceDependencyTree).cursor_id == edge.key
            assert app.query_one(ResourceDependencyTree).has_focus

    asyncio.run(journey())


def test_resolved_mode_survives_graph_navigation_and_back(tmp_path: Path) -> None:
    from comparo.tui.models.resource import InspectionPhase
    from comparo.tui.models.resource import ResourceOverviewMode

    async def journey() -> None:
        app = ComparoApp(PROJECT, preference_path=tmp_path / "settings.json")
        async with app.run_test(size=(120, 40)) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            for _ in range(200):
                if workspace.catalog is not None:
                    break
                await pilot.pause(0.01)
            assert workspace.catalog is not None
            request = next(
                entry
                for entry in workspace.catalog.entries.values()
                if entry.summary.coordinate.kind == "Request"
                and entry.summary.coordinate.resource_id == "catalog"
            )
            workspace.selected = request.key
            workspace.action_toggle_resource_resolution()
            for _ in range(200):
                if (
                    workspace.detail is not None
                    and workspace.detail.resolved.phase is InspectionPhase.READY
                ):
                    break
                await pilot.pause(0.01)
            assert workspace.detail is not None
            assert str(workspace.detail.resolved.phase) == InspectionPhase.READY
            # The worker's model can be ready while the Request tabs are still mounting.
            await pilot.pause()
            assert app.query_one(RequestResourceDetails).resource is workspace.detail
            workspace.action_resource_view("dependencies")
            await pilot.pause()
            graph = app.query_one(ResourceDependencyTree)
            edge = next(
                item for item in resource_dependencies(request) if item.target.kind == "Matrix"
            )
            graph.reveal(edge.key)
            graph.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert workspace.detail.overview_mode is ResourceOverviewMode.RESOLVED
            await pilot.press("escape")
            await pilot.pause()
            assert workspace.selected == request.key
            assert workspace.detail.overview_mode is ResourceOverviewMode.RESOLVED
            assert str(workspace.detail.resolved.phase) == InspectionPhase.UNAVAILABLE
            workspace.action_resource_view("overview")
            for _ in range(200):
                if workspace.detail.resolved.phase is InspectionPhase.READY:
                    break
                await pilot.pause(0.01)
            assert str(workspace.detail.resolved.phase) == InspectionPhase.READY
            assert workspace.detail.overview_mode is ResourceOverviewMode.RESOLVED
            # Finish the visible journey before run_test tears down the widget tree.
            await pilot.pause()
            assert app.query_one(RequestResourceDetails).resource is workspace.detail
            tabs = app.query_one("#request-tabs", TabbedContent)
            assert tabs.active == "request-summary"
            assert tabs.get_tab("request-summary").is_mounted

    asyncio.run(journey())
