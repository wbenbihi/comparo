"""Resource-aware Explorer presentation and interaction tests."""

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from functools import wraps
from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import Button

from comparo.application import ProjectWorkspaceRequest
from comparo.application import inspect_project_workspace
from comparo.core.domain.identity import ResourceKind
from comparo.tui4.app import ComparoApp
from comparo.tui4.components.explorernavigator import ExplorerNavigator
from comparo.tui4.components.explorernavigator import ExplorerResourceTree
from comparo.tui4.components.resourcedetail import ResourceDetailOverview
from comparo.tui4.components.resources import RESOURCE_WIDGETS
from comparo.tui4.components.resources import resource_widget
from comparo.tui4.components.resources.matrix import MatrixResourceDetails
from comparo.tui4.components.resources.standard import RequestResourceDetails
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResourceActionId
from comparo.tui4.models.resource import ResourceInspection
from comparo.tui4.models.resource import ResourceRepresentation
from comparo.tui4.models.resource import initial_detail
from comparo.tui4.models.resource_presenters import matrix_dimensions
from comparo.tui4.types import ComparoScreen
from comparo.tui4.workspaces.explorer import ExplorerWorkspace

PROJECT = Path(__file__).parents[2] / "examples" / "tui-showcase"


def _run_async[**P, R](
    function: Callable[P, Coroutine[object, object, R]],
) -> Callable[P, R]:
    """Adapt one async Textual test to the repository's synchronous runner."""

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        return asyncio.run(function(*args, **kwargs))

    return wrapper


async def _wait_until(pilot: Pilot[None], predicate: Callable[[], bool]) -> None:
    """Wait for worker-backed Explorer state with a short deterministic ceiling."""
    for _attempt in range(200):
        if predicate():
            return
        await pilot.pause(0.01)
    pytest.fail("Explorer condition was not reached")


def _catalog() -> ExplorerCatalog:
    """Compile the committed showcase through the public application facade."""
    result = inspect_project_workspace(ProjectWorkspaceRequest(config=PROJECT))
    catalog = build_catalog(result)
    assert catalog is not None
    return catalog


def test_catalog_retains_revision_pinned_sources_and_all_kinds_have_widgets() -> None:
    catalog = _catalog()

    assert all(entry.source.sha256 is not None for entry in catalog.entries.values())
    assert {kind.value for kind in ResourceKind} <= set(RESOURCE_WIDGETS)
    for entry in catalog.entries.values():
        widget = resource_widget(initial_detail(entry))
        assert entry.summary.coordinate.kind in widget.KINDS


def test_kind_projections_keep_matrix_planning_and_request_composition_context_bound() -> None:
    catalog = _catalog()
    matrix = next(
        entry
        for entry in catalog.entries.values()
        if entry.summary.coordinate.kind == ResourceKind.MATRIX.value
        and entry.summary.coordinate.resource_id == "audiences"
    )
    request = next(
        entry
        for entry in catalog.entries.values()
        if entry.summary.coordinate.kind == ResourceKind.REQUEST.value
    )

    dimensions = matrix_dimensions(matrix)
    assert {(item.id, item.role) for item in dimensions} == {
        ("region", "axis"),
        ("plan", "axis"),
        ("include", "cases"),
        ("exclude", "cases"),
    }
    matrix_detail = initial_detail(matrix)
    request_detail = initial_detail(request)
    assert type(resource_widget(matrix_detail)) is MatrixResourceDetails
    assert type(resource_widget(request_detail)) is RequestResourceDetails
    matrix_cells = next(
        action
        for action in matrix_detail.actions
        if action.id is ResourceActionId.PLAN_MATRIX_CELLS
    )
    composition = next(
        action
        for action in request_detail.actions
        if action.id is ResourceActionId.INSPECT_COMPOSITION
    )
    assert not matrix_cells.enabled
    assert matrix_cells.disabled_reason
    assert not composition.enabled
    assert composition.disabled_reason


def test_resource_inspection_rejects_contradictory_lifecycle_state() -> None:
    with pytest.raises(ValueError, match="ready inspection"):
        ResourceInspection[str](phase=InspectionPhase.READY)
    with pytest.raises(ValueError, match="only a ready"):
        ResourceInspection(phase=InspectionPhase.LOADING, value="unexpected")
    with pytest.raises(ValueError, match="require a message"):
        ResourceInspection[str](phase=InspectionPhase.FAILED)
    with pytest.raises(ValueError, match="only failed"):
        ResourceInspection[str](message="unexpected")


@_run_async
async def test_navigator_owns_filter_tree_and_legend_mechanics() -> None:
    app = ComparoApp(PROJECT)

    async with app.run_test(size=(180, 55)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        tree = app.query_one(ExplorerResourceTree)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        navigator.focus_filter()
        await pilot.press(*"audiences")
        assert tree.filter_text == "audiences"
        assert len(tree.nodes) == 2
        navigator.focus_tree()
        await pilot.press("down", "down", "down")
        await pilot.pause()
        detail = app.query_one(ResourceDetailOverview).detail
        assert detail is not None
        assert detail.entry.summary.coordinate.resource_id == "audiences"


@_run_async
async def test_resource_host_loads_views_and_forwards_compare_subject() -> None:
    app = ComparoApp(PROJECT)

    async with app.run_test(size=(180, 55)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        workspace = app.query_one(ExplorerWorkspace)
        request = next(
            entry
            for entry in catalog.entries.values()
            if entry.summary.coordinate.kind == ResourceKind.REQUEST.value
        )
        workspace.selected = request.key
        await _wait_until(
            pilot,
            lambda: (
                (detail := app.query_one(ResourceDetailOverview).detail) is not None
                and detail.key == request.key
            ),
        )
        await _wait_until(
            pilot,
            lambda: len(app.query("#resource-action-authored")) == 1,
        )

        app.query_one("#resource-action-authored", Button).focus()
        await pilot.press("2")
        await _wait_until(
            pilot,
            lambda: (
                (detail := app.query_one(ResourceDetailOverview).detail) is not None
                and detail.authored.phase is InspectionPhase.READY
            ),
        )
        authored = app.query_one(ResourceDetailOverview).detail
        assert authored is not None
        assert authored.authored.value is not None
        assert authored.representation is ResourceRepresentation.AUTHORED
        assert authored.authored.value.contains_unmasked_values is False
        assert authored.authored.value.coordinate == request.summary.coordinate

        await pilot.click("#resource-action-effective")
        await _wait_until(
            pilot,
            lambda: (
                (detail := app.query_one(ResourceDetailOverview).detail) is not None
                and detail.effective.phase is InspectionPhase.READY
            ),
        )
        effective = app.query_one(ResourceDetailOverview).detail
        assert effective is not None
        assert effective.effective.value is not None
        assert effective.representation is ResourceRepresentation.EFFECTIVE
        assert effective.effective.value.coordinate == request.summary.coordinate

        await pilot.click("#resource-action-resolved")
        resolved = app.query_one(ResourceDetailOverview).detail
        assert resolved is not None
        assert resolved.representation is ResourceRepresentation.RESOLVED
        assert resolved.resolved.phase is InspectionPhase.UNAVAILABLE

        await pilot.click("#resource-action-overview")
        await pilot.click("#resource-action-use-in-compare")
        assert app.state.active_screen is ComparoScreen.LAUNCH
        assert app.state.compare_subject == request.summary.coordinate
