"""Resource-aware Explorer presentation and interaction tests."""

import asyncio
import shutil
import threading
from collections.abc import Callable
from collections.abc import Coroutine
from dataclasses import replace
from functools import wraps
from pathlib import Path

import msgspec
import pytest
from rich.text import Text
from textual.pilot import Pilot
from textual.widgets import Button
from textual.widgets import Static

from comparo.application import AuthoredResourceWindow
from comparo.application import ProjectWorkspaceRequest
from comparo.application import ResourceCoordinate
from comparo.application import inspect_project_workspace
from comparo.application.project_outline import ProjectOutlineField
from comparo.application.project_outline import ProjectResourceOutline
from comparo.core.domain.identity import ResourceKind
from comparo.tui4.app import ComparoApp
from comparo.tui4.backend import HealthCheckBatch
from comparo.tui4.backend import TuiBackend
from comparo.tui4.components.badge import Badge
from comparo.tui4.components.badge import BadgeEmphasis
from comparo.tui4.components.badge import BadgeSpec
from comparo.tui4.components.badge import BadgeTone
from comparo.tui4.components.badge import badge_text
from comparo.tui4.components.explorernavigator import ExplorerNavigator
from comparo.tui4.components.explorernavigator import ExplorerResourceTree
from comparo.tui4.components.legend import Legend
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.resourceactions import ResourceActionBar
from comparo.tui4.components.resourcedetail import ResourceDetailOverview
from comparo.tui4.components.resourcedetail import ResourceSummary
from comparo.tui4.components.resources import RESOURCE_WIDGETS
from comparo.tui4.components.resources import resource_widget
from comparo.tui4.components.resources.environment import EnvironmentContextStrip
from comparo.tui4.components.resources.environment import EnvironmentHealthStatus
from comparo.tui4.components.resources.environment import EnvironmentResourceDetails
from comparo.tui4.components.resources.matrix import MatrixResourceDetails
from comparo.tui4.components.resources.project import ProjectDependencyGraph
from comparo.tui4.components.resources.project import ProjectDependencyTree
from comparo.tui4.components.resources.project import ProjectEnvironmentCard
from comparo.tui4.components.resources.project import ProjectEnvironmentRow
from comparo.tui4.components.resources.project import ProjectResourceDetails
from comparo.tui4.components.resources.standard import RequestResourceDetails
from comparo.tui4.components.typography import Caption
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.explorer import catalog_nodes
from comparo.tui4.models.explorer import select_execution_environment
from comparo.tui4.models.navigation import MenuNode
from comparo.tui4.models.resource import EnvironmentHealthCheckResult
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResourceActionId
from comparo.tui4.models.resource import ResourceInspection
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourceRepresentation
from comparo.tui4.models.resource import initial_detail
from comparo.tui4.models.resource_presenters import environment_health_check_ids
from comparo.tui4.models.resource_presenters import environment_health_check_targets
from comparo.tui4.models.resource_presenters import environment_overview
from comparo.tui4.models.resource_presenters import matrix_dimensions
from comparo.tui4.models.resource_presenters import project_compiled_exact_fields
from comparo.tui4.models.resource_presenters import project_overview
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


def _legend_is_visible(legend: Legend) -> bool:
    """Read reactive visibility without retaining a stale narrowed test value."""
    return legend.display


def test_badges_share_one_terminal_native_contract_for_inline_and_widget_use() -> None:
    solid = badge_text("live", tone=BadgeTone.DANGER, emphasis=BadgeEmphasis.SOLID)
    active = badge_text("exec", tone=BadgeTone.ACTIVE, emphasis=BadgeEmphasis.SOLID)
    quiet = badge_text("matrix", tone=BadgeTone.WARNING)
    status = badge_text("in progress", tone=BadgeTone.INFO, emphasis=BadgeEmphasis.STATUS)
    widget = Badge("local", tone=BadgeTone.SUCCESS)
    rendered_widget = widget.render()

    assert solid.plain == " LIVE "
    assert active.plain == " EXEC "
    assert active.style != solid.style
    assert quiet.plain == "[matrix]"
    assert status.plain == "● in progress"
    assert isinstance(rendered_widget, Text)
    assert rendered_widget.plain == "[local]"
    assert "◖" not in solid.plain
    assert "◗" not in solid.plain
    with pytest.raises(ValueError, match="printable"):
        BadgeSpec(" invalid ")
    with pytest.raises(ValueError, match="printable"):
        BadgeSpec("invalid\nlabel")


def test_explorer_tree_centrally_formats_sections_and_resource_rows() -> None:
    catalog = _catalog()
    tree = ExplorerResourceTree()
    tree.catalog = catalog
    nodes = catalog_nodes(catalog)
    sections = [node for node in nodes if node.id.startswith("section:")]
    request_section = next(node for node in sections if node.id == "section:Requests")
    request_node = request_section.children[0]

    assert request_node.prompt.plain == catalog.entries[request_node.id].summary.name
    assert tree.format_node(request_section).plain == f"Requests {len(request_section.children)}"
    formatted_resource = tree.format_node(request_node)
    entry = catalog.entries[request_node.id]
    assert entry.summary.name is not None
    assert entry.summary.name in formatted_resource.plain
    assert formatted_resource.plain != request_node.prompt.plain

    resource_nodes = [node for node in nodes if node.id in catalog.entries]
    for section in sections:
        resource_nodes.extend(section.children)
    for node in resource_nodes:
        entry = catalog.entries[node.id]
        formatted = tree.format_node(node).plain
        resource_id = entry.summary.coordinate.resource_id
        display_name = entry.summary.name or f"Unnamed {entry.summary.coordinate.kind}"
        assert formatted.count(resource_id) == display_name.count(resource_id)


def test_execution_environment_defaults_to_baseline_and_formats_one_context_pill() -> None:
    catalog = _catalog()
    selected = select_execution_environment(catalog, None)
    assert selected is not None
    assert selected.facts.role == "baseline"

    tree = ExplorerResourceTree()
    tree.catalog = catalog
    tree.execution_environment = selected.summary.coordinate
    formatted = {
        entry.summary.coordinate.resource_id: tree.format_node(
            MenuNode(
                entry.key,
                entry.summary.name or entry.summary.coordinate.resource_id,
            )
        ).plain
        for entry in catalog.entries.values()
        if entry.summary.coordinate.kind == "Environment"
    }
    assert " EXEC " in formatted["baseline"]
    assert " EXEC " not in formatted["candidate"]


def test_catalog_retains_revision_pinned_sources_and_all_kinds_have_widgets() -> None:
    catalog = _catalog()

    assert all(entry.source.sha256 is not None for entry in catalog.entries.values())
    assert {kind.value for kind in ResourceKind} <= set(RESOURCE_WIDGETS)
    for entry in catalog.entries.values():
        widget = resource_widget(initial_detail(entry))
        assert entry.summary.coordinate.kind in widget.KINDS


def test_project_overview_retains_topology_and_every_safe_project_setting() -> None:
    catalog = _catalog()
    overview = project_overview(catalog.project)

    assert overview.baseline is not None
    assert (overview.baseline.name, overview.baseline.locality) == (
        "Production baseline",
        "local",
    )
    assert [(candidate.name, candidate.locality) for candidate in overview.candidates] == [
        ("Staging candidate", "local")
    ]
    assert [setting.name for setting in overview.sources] == ["data"]
    assert [setting.name for setting in overview.variables] == ["client"]
    assert overview.secrets == ()
    assert [group.title for group in overview.default_groups] == [
        "Selection",
        "Execution",
        "Evaluation & output",
    ]

    settings = [*overview.sources, *overview.variables, *overview.secrets, *overview.additional]
    for group in overview.default_groups:
        settings.extend(group.settings)
    projected_paths = {
        path
        for setting in settings
        for path in (
            setting.path,
            *(setting.path + detail.path for detail in setting.details),
        )
    }
    outline = catalog.project.summary.outline
    assert outline is not None
    section_paths = {("variables",), ("secrets",), ("defaults",)}
    expected_paths = {
        field.path for field in outline.fields if field.path and field.path not in section_paths
    }
    assert projected_paths == expected_paths
    exact_fields = {field.path: field for field in project_compiled_exact_fields(catalog.project)}
    assert (exact_fields[("data",)].value, exact_fields[("data",)].tone) == (
        ".comparo",
        "value.text",
    )
    assert (
        exact_fields[("defaults", "maximumAttempts")].value,
        exact_fields[("defaults", "maximumAttempts")].tone,
    ) == ("1", "value.integer")
    assert (
        exact_fields[("defaults", "failFast")].value,
        exact_fields[("defaults", "failFast")].tone,
    ) == ("false", "value.boolean")
    baseline_field = exact_fields[("defaults", "baseline")]
    assert (baseline_field.value, baseline_field.tone, baseline_field.reference_kind) == (
        "Environment/baseline",
        "value.reference",
        "Environment",
    )

    secret_outline = ProjectResourceOutline(
        fields=(
            *outline.fields,
            ProjectOutlineField(
                path=("secrets",),
                label="secrets",
                value="object · 1 member",
                depth=1,
                category="container",
            ),
            ProjectOutlineField(
                path=("secrets", "apiToken"),
                label="apiToken",
                value="$env.API_TOKEN",
                depth=2,
                category="directive",
            ),
        ),
        relationships=outline.relationships,
    )
    summary_with_secret = msgspec.structs.replace(
        catalog.project.summary,
        outline=secret_outline,
    )
    overview_with_secret = project_overview(replace(catalog.project, summary=summary_with_secret))
    assert [
        (setting.name, setting.value, setting.category) for setting in overview_with_secret.secrets
    ] == [("apiToken", "$env.API_TOKEN", "directive")]


def test_environment_overview_groups_every_field_and_declared_health_request() -> None:
    catalog = _catalog()
    environment = next(
        entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
    )
    overview = environment_overview(environment)

    assert [setting.name for setting in overview.connection] == ["baseUrl"]
    assert [setting.name for setting in overview.runtime_inputs] == ["variables"]
    assert overview.request_defaults == ()
    assert [setting.name for setting in overview.policies] == ["safety"]
    assert [check.resource_id for check in overview.health_checks] == ["health"]
    assert environment_health_check_ids(environment) == ("health",)
    assert environment_health_check_targets(environment) == overview.health_checks
    assert overview.additional == ()

    projected_roots = {
        setting.name
        for setting in (
            *overview.connection,
            *overview.runtime_inputs,
            *overview.request_defaults,
            *overview.policies,
            *overview.additional,
        )
    } | ({"healthChecks"} if overview.health_checks else set())
    outline = environment.summary.outline
    assert outline is not None
    assert projected_roots == {field.path[0] for field in outline.fields if field.path}

    health_field = next(field for field in outline.fields if field.path == ("healthChecks", "0"))
    duplicate_outline = msgspec.structs.replace(
        outline,
        fields=(
            *outline.fields,
            msgspec.structs.replace(health_field, path=("healthChecks", "1")),
        ),
    )
    duplicate_entry = replace(
        environment,
        summary=msgspec.structs.replace(environment.summary, outline=duplicate_outline),
    )
    assert environment_health_check_ids(duplicate_entry) == ("health", "health")


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


def test_health_check_summary_and_batch_reject_contradictory_state() -> None:
    healthy = EnvironmentHealthCheckResult(
        run_id="health-run",
        request_count=1,
        completion="complete",
        gate="passed",
        adverse_facts=0,
        healthy=True,
    )

    assert HealthCheckBatch(value=healthy).value is healthy
    assert HealthCheckBatch(diagnostics=("health-check.failed",)).value is None
    with pytest.raises(ValueError, match="exactly result or diagnostics"):
        HealthCheckBatch()
    with pytest.raises(ValueError, match="exactly result or diagnostics"):
        HealthCheckBatch(value=healthy, diagnostics=("health-check.failed",))
    with pytest.raises(ValueError, match="request count"):
        replace(healthy, request_count=0)
    with pytest.raises(ValueError, match="contradicts"):
        replace(healthy, adverse_facts=1)


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
        legend = app.query_one(Legend)
        assert _legend_is_visible(legend)
        await pilot.press("l")
        await pilot.pause()
        assert not _legend_is_visible(legend)
        await pilot.press("l")
        await pilot.pause()
        assert _legend_is_visible(legend)
        await pilot.press("down", "down", "down")
        await pilot.pause()
        detail = app.query_one(ResourceDetailOverview).detail
        assert detail is not None
        assert detail.entry.summary.coordinate.resource_id == "audiences"


@_run_async
async def test_project_resource_uses_two_environment_cards_and_explicit_sections() -> None:
    app = ComparoApp(PROJECT)

    async with app.run_test(size=(180, 70)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        baseline = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )
        assert app.state.execution_environment == baseline.summary.coordinate
        assert app.query_one(ExplorerResourceTree).execution_environment == (
            baseline.summary.coordinate
        )
        app.query_one(ExplorerWorkspace).selected = catalog.project.key
        await _wait_until(pilot, lambda: len(app.query(ProjectResourceDetails)) == 1)

        project = app.query_one(ProjectResourceDetails)
        workspace = app.query_one(ExplorerWorkspace)
        panel = app.query_one("#resource-detail-panel", Panel)
        assert panel.border_subtitle == "overview · raw"
        assert not list(app.query(ResourceActionBar))
        cards = list(project.query(ProjectEnvironmentCard))
        assert len(cards) == 2
        assert [str(card.border_title) for card in cards] == ["Baseline", "Candidate"]
        rows = list(project.query(ProjectEnvironmentRow))
        assert [
            str(row.query_one(".project-environment-name", Static).content) for row in rows
        ] == ["Production baseline", "Staging candidate"]
        assert [row.query_one(Badge).spec.label for row in rows] == ["local", "local"]
        assert not list(project.query(KeyValueList))

        await pilot.press("g")
        await pilot.pause()
        detail = workspace.detail
        assert detail is not None
        assert detail.representation is ResourceRepresentation.DEPENDENCIES
        assert app.query_one("#resource-detail-panel", Panel).border_subtitle == "dependency graph"
        graph = app.query_one(ProjectDependencyGraph)
        assert len(graph.query_one(ProjectDependencyTree).nodes) >= 1

        await pilot.press("s")
        await _wait_until(
            pilot,
            lambda: (
                (detail := workspace.detail) is not None
                and detail.authored.phase is InspectionPhase.READY
            ),
        )
        assert app.query_one("#resource-detail-panel", Panel).border_subtitle == "source"

        await pilot.press("o")
        await pilot.pause()
        detail = workspace.detail
        assert detail is not None
        assert detail.representation is ResourceRepresentation.OVERVIEW
        assert detail.overview_mode is ResourceOverviewMode.RAW
        assert app.query_one("#resource-detail-panel", Panel).border_subtitle == "overview · raw"
        authored_headings = {
            str(item.content)
            for item in app.query_one(ProjectResourceDetails).query(Static)
            if item.has_class("resource-setting-heading")
        }
        assert any(
            "baseline  $use Environment/baseline" in item and "◉" not in item
            for item in authored_headings
        )

        await pilot.press("r")
        await _wait_until(
            pilot,
            lambda: (
                (detail := workspace.detail) is not None
                and detail.resolved.phase is not InspectionPhase.LOADING
            ),
        )
        detail = workspace.detail
        assert detail is not None
        assert detail.representation is ResourceRepresentation.OVERVIEW
        assert detail.overview_mode is ResourceOverviewMode.RESOLVED
        assert detail.resolved.phase is InspectionPhase.READY
        assert detail.resolved.value is not None
        assert detail.resolved.value.environment == baseline.summary.coordinate
        client_field = next(
            field for field in detail.resolved.value.fields if field.path == ("variables", "client")
        )
        assert (client_field.value, client_field.tone) == ("comparo-tui", "value.text")
        resolved_project = app.query_one(ProjectResourceDetails)
        resolved_headings = {
            str(item.content)
            for item in resolved_project.query(Static)
            if item.has_class("resource-setting-heading")
        }
        assert any("client  comparo-tui" in item for item in resolved_headings)
        assert any("data  .comparo" in item for item in resolved_headings)
        assert any("maximumAttempts  1" in item for item in resolved_headings)
        assert any("failFast  false" in item for item in resolved_headings)
        assert any("baseline  ◉ Environment/baseline" in item for item in resolved_headings)
        assert list(resolved_project.query(ProjectEnvironmentCard))
        assert app.query_one("#resource-detail-panel", Panel).border_subtitle == (
            "overview · resolved"
        )

        await pilot.press("r")
        await pilot.pause()
        detail = workspace.detail
        assert detail is not None
        assert detail.overview_mode is ResourceOverviewMode.RAW
        raw_headings = {
            str(item.content)
            for item in app.query_one(ProjectResourceDetails).query(Static)
            if item.has_class("resource-setting-heading")
        }
        assert any("client  <string · 11 chars>" in item for item in raw_headings)
        assert any("data  <string · 8 chars>" in item for item in raw_headings)
        assert any("maximumAttempts  <integer>" in item for item in raw_headings)
        assert any("failFast  <boolean>" in item for item in raw_headings)
        assert any(
            "baseline  $use Environment/baseline" in item and "◉" not in item
            for item in raw_headings
        )

        await pilot.press("r")
        await pilot.pause()
        detail = workspace.detail
        assert detail is not None
        assert detail.overview_mode is ResourceOverviewMode.RESOLVED
        cached_headings = {
            str(item.content)
            for item in app.query_one(ProjectResourceDetails).query(Static)
            if item.has_class("resource-setting-heading")
        }
        assert any("client  comparo-tui" in item for item in cached_headings)
        assert any("data  .comparo" in item for item in cached_headings)


@_run_async
async def test_environment_commit_and_context_binding_switch_the_shared_default() -> None:
    app = ComparoApp(PROJECT)

    async with app.run_test(size=(180, 55)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        workspace = app.query_one(ExplorerWorkspace)
        tree = app.query_one(ExplorerResourceTree)
        candidate = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "candidate"
        )

        workspace.selected = candidate.key
        await _wait_until(
            pilot,
            lambda: workspace.detail is not None and workspace.detail.key == candidate.key,
        )
        app.refresh_bindings()
        await pilot.pause()
        context_binding = app.screen.active_bindings["space"]
        assert context_binding.binding.description == "Set context"
        assert context_binding.enabled
        await pilot.press("space")
        await _wait_until(
            pilot,
            lambda: app.state.execution_environment == candidate.summary.coordinate,
        )
        assert tree.execution_environment == candidate.summary.coordinate
        assert (
            " EXEC "
            in tree.format_node(
                MenuNode(
                    candidate.key,
                    candidate.summary.name or candidate.summary.coordinate.resource_id,
                )
            ).plain
        )

        workspace.selected = catalog.project.key
        await _wait_until(
            pilot,
            lambda: workspace.detail is not None and workspace.detail.key == catalog.project.key,
        )
        await pilot.press("r")
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.resolved.phase is InspectionPhase.READY
            ),
        )
        assert workspace.detail is not None
        assert workspace.detail.resolved.value is not None
        assert workspace.detail.resolved.value.environment == candidate.summary.coordinate

        baseline = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )
        navigator.post_message(ExplorerNavigator.ResourceSelected(navigator, baseline))
        await pilot.pause()
        assert app.state.execution_environment == candidate.summary.coordinate

        workspace.selected = baseline.key
        await _wait_until(
            pilot,
            lambda: workspace.detail is not None and workspace.detail.key == baseline.key,
        )
        await pilot.press("space")
        await _wait_until(
            pilot,
            lambda: app.state.execution_environment == baseline.summary.coordinate,
        )
        assert (
            " EXEC "
            in tree.format_node(
                MenuNode(
                    baseline.key, baseline.summary.name or baseline.summary.coordinate.resource_id
                )
            ).plain
        )
        assert workspace.detail is not None
        assert workspace.detail.execution_environment == baseline.summary.coordinate
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.resolved.phase is InspectionPhase.READY
            ),
        )
        assert workspace.detail.resolved.value is not None
        assert workspace.detail.resolved.value.environment == baseline.summary.coordinate


@_run_async
async def test_environment_overview_resolves_its_own_context_and_runs_declared_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def healthy(
        _backend: TuiBackend,
        environment: ResourceCoordinate,
        requests: tuple[ResourceCoordinate, ...],
    ) -> HealthCheckBatch:
        assert environment.resource_id == "candidate"
        assert tuple(request.resource_id for request in requests) == ("health",)
        return HealthCheckBatch(
            value=EnvironmentHealthCheckResult(
                run_id="health-run-0001",
                request_count=1,
                completion="complete",
                gate="not-requested",
                adverse_facts=0,
                healthy=True,
            )
        )

    monkeypatch.setattr(TuiBackend, "run_environment_health_checks", healthy)
    app = ComparoApp(PROJECT)
    notifications: list[tuple[str, str, str, float | None, bool]] = []

    def notify(
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        notifications.append((message, title, severity, timeout, markup))

    monkeypatch.setattr(app, "notify", notify)

    async with app.run_test(size=(180, 70)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        workspace = app.query_one(ExplorerWorkspace)
        candidate = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "candidate"
        )
        baseline = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )
        assert app.state.execution_environment == baseline.summary.coordinate

        workspace.selected = candidate.key
        await _wait_until(
            pilot,
            lambda: len(app.query(EnvironmentResourceDetails)) == 1,
        )
        assert not list(app.query(ResourceActionBar))
        strip = app.query_one(EnvironmentContextStrip)
        assert [badge.spec.label for badge in strip.query(Badge)] == ["candidate", "local"]
        assert len(strip.query(".environment-context-hint")) == 1
        assert app.query_one("#resource-detail-panel", Panel).border_subtitle == "overview · raw"
        raw_checks = {
            str(item.content) for item in app.query_one(EnvironmentHealthStatus).query(Static)
        }
        assert any("$use Request/health" in item and "⇄" not in item for item in raw_checks)

        app.refresh_bindings()
        await pilot.pause()
        assert app.screen.active_bindings["h"].enabled
        assert app.screen.active_bindings["r"].enabled
        await pilot.press("r")
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.resolved.phase is InspectionPhase.READY
            ),
        )
        detail = workspace.detail
        assert detail is not None
        assert detail.resolved.value is not None
        assert detail.resolved.value.environment == candidate.summary.coordinate
        resolved_headings = {
            str(item.content)
            for item in app.query_one(EnvironmentResourceDetails).query(Static)
            if item.has_class("resource-setting-heading")
        }
        assert any(
            "baseUrl  http://127.0.0.1:18080/candidate" in item for item in resolved_headings
        )
        assert any("safety  ⊘ SafetyProfile/local-read-only" in item for item in resolved_headings)
        resolved_checks = {
            str(item.content) for item in app.query_one(EnvironmentHealthStatus).query(Static)
        }
        assert any("⇄ Request/health" in item for item in resolved_checks)

        await pilot.press("h")
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.health.phase is InspectionPhase.READY
            ),
        )
        detail = workspace.detail
        assert detail is not None
        assert detail.health.value is not None
        assert detail.health.value.healthy
        health_lines = {
            str(item.content) for item in app.query_one(EnvironmentHealthStatus).query(Static)
        }
        assert any("healthy" in item and "health-r" in item for item in health_lines)
        assert notifications == [
            (
                "1 request(s) · complete · gate not-requested · run health-r…",
                "Staging candidate is healthy",
                "information",
                5,
                False,
            )
        ]


@_run_async
async def test_health_run_survives_tree_selection_and_prevents_duplicate_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_health(
        _backend: TuiBackend,
        _environment: ResourceCoordinate,
        _requests: tuple[ResourceCoordinate, ...],
    ) -> HealthCheckBatch:
        started.set()
        await release.wait()
        return HealthCheckBatch(
            value=EnvironmentHealthCheckResult(
                run_id="detached-health-run",
                request_count=1,
                completion="complete",
                gate="passed",
                adverse_facts=0,
                healthy=True,
            )
        )

    monkeypatch.setattr(TuiBackend, "run_environment_health_checks", slow_health)
    app = ComparoApp(PROJECT)
    notifications: list[str] = []

    def notify(message: str, **_options: object) -> None:
        notifications.append(message)

    monkeypatch.setattr(app, "notify", notify)

    async with app.run_test(size=(180, 70)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        workspace = app.query_one(ExplorerWorkspace)
        candidate = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "candidate"
        )
        baseline = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )

        workspace.selected = candidate.key
        await _wait_until(pilot, lambda: workspace.detail is not None)
        await pilot.press("h")
        await _wait_until(pilot, started.is_set)
        assert workspace.detail is not None
        assert workspace.detail.health.phase is InspectionPhase.LOADING

        workspace.selected = baseline.key
        await _wait_until(
            pilot,
            lambda: workspace.detail is not None and workspace.detail.key == baseline.key,
        )
        workspace.selected = candidate.key
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.key == candidate.key
                and workspace.detail.health.phase is InspectionPhase.LOADING
            ),
        )
        app.refresh_bindings()
        await pilot.pause()
        assert "h" not in app.screen.active_bindings

        workspace.selected = baseline.key
        await _wait_until(
            pilot,
            lambda: workspace.detail is not None and workspace.detail.key == baseline.key,
        )
        release.set()
        await _wait_until(pilot, lambda: bool(notifications))
        workspace.selected = candidate.key
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.key == candidate.key
                and workspace.detail.health.phase is InspectionPhase.READY
            ),
        )
        assert workspace.detail is not None
        assert workspace.detail.health.value is not None
        assert workspace.detail.health.value.run_id == "detached-health-run"


@_run_async
async def test_catalog_reload_drops_health_state_when_a_probe_revision_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def healthy(
        backend: TuiBackend,
        environment: ResourceCoordinate,
        requests: tuple[ResourceCoordinate, ...],
    ) -> HealthCheckBatch:
        return HealthCheckBatch(
            value=EnvironmentHealthCheckResult(
                run_id="revision-bound-health",
                request_count=len(requests),
                completion="complete",
                gate="not-requested",
                adverse_facts=0,
                healthy=True,
            )
        )

    monkeypatch.setattr(TuiBackend, "run_environment_health_checks", healthy)
    project = tmp_path / "project"
    shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
    app = ComparoApp(project)

    async with app.run_test(size=(180, 70)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        workspace = app.query_one(ExplorerWorkspace)
        baseline = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )
        workspace.selected = baseline.key
        await _wait_until(pilot, lambda: workspace.detail is not None)
        await pilot.press("h")
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.health.phase is InspectionPhase.READY
            ),
        )
        previous_targets = environment_health_check_targets(baseline)
        source = project / ".comparo" / "requests.yaml"
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "Stable health Request useful for assertion-only runs.",
                "Updated health Request useful for assertion-only runs.",
            ),
            encoding="utf-8",
        )

        workspace.action_reload()
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and environment_health_check_targets(workspace.detail.entry) != previous_targets
                and workspace.detail.health.phase is InspectionPhase.IDLE
            ),
        )


@_run_async
async def test_context_change_does_not_invalidate_loading_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    original = TuiBackend.inspect_authored

    def slow_source(
        backend: TuiBackend,
        coordinate: ResourceCoordinate,
        expected_sha256: str | None,
    ) -> AuthoredResourceWindow:
        started.set()
        assert release.wait(timeout=3)
        return original(backend, coordinate, expected_sha256)

    monkeypatch.setattr(TuiBackend, "inspect_authored", slow_source)
    app = ComparoApp(PROJECT)

    async with app.run_test(size=(180, 70)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        workspace = app.query_one(ExplorerWorkspace)
        candidate = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "candidate"
        )

        workspace.selected = candidate.key
        await _wait_until(pilot, lambda: workspace.detail is not None)
        await pilot.press("s")
        await _wait_until(pilot, started.is_set)
        await pilot.press("space")
        assert app.state.execution_environment == candidate.summary.coordinate
        release.set()
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.key == candidate.key
                and workspace.detail.authored.phase is InspectionPhase.READY
            ),
        )


@_run_async
async def test_explorer_keeps_the_active_view_across_tree_selection_changes() -> None:
    app = ComparoApp(PROJECT)

    async with app.run_test(size=(180, 70)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        workspace = app.query_one(ExplorerWorkspace)
        baseline = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )
        candidate = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "candidate"
        )

        workspace.selected = catalog.project.key
        await _wait_until(pilot, lambda: workspace.detail is not None)
        await pilot.press("g")
        workspace.selected = baseline.key
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.key == baseline.key
                and workspace.detail.representation is ResourceRepresentation.DEPENDENCIES
            ),
        )

        await pilot.press("s")
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.authored.phase is InspectionPhase.READY
            ),
        )
        workspace.selected = candidate.key
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.key == candidate.key
                and workspace.detail.representation is ResourceRepresentation.AUTHORED
                and workspace.detail.authored.phase is InspectionPhase.READY
            ),
        )
        assert workspace.detail is not None
        assert workspace.detail.authored.value is not None
        assert workspace.detail.authored.value.coordinate == candidate.summary.coordinate

        await pilot.press("o", "r")
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.overview_mode is ResourceOverviewMode.RESOLVED
                and workspace.detail.resolved.phase is InspectionPhase.READY
            ),
        )
        workspace.selected = baseline.key
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.key == baseline.key
                and workspace.detail.representation is ResourceRepresentation.OVERVIEW
                and workspace.detail.overview_mode is ResourceOverviewMode.RESOLVED
                and workspace.detail.resolved.phase is InspectionPhase.READY
            ),
        )
        assert workspace.detail is not None
        assert workspace.detail.resolved.value is not None
        assert workspace.detail.resolved.value.environment == baseline.summary.coordinate


@_run_async
async def test_catalog_reload_rehydrates_the_active_view_from_the_new_source_revision(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("artifacts"))
    app = ComparoApp(project)

    async with app.run_test(size=(180, 70)) as pilot:
        navigator = app.query_one(ExplorerNavigator)
        await _wait_until(pilot, lambda: navigator.catalog is not None)
        catalog = navigator.catalog
        assert catalog is not None
        workspace = app.query_one(ExplorerWorkspace)
        candidate = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "candidate"
        )
        workspace.selected = candidate.key
        await _wait_until(pilot, lambda: workspace.detail is not None)
        await pilot.press("s")
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.authored.phase is InspectionPhase.READY
            ),
        )
        assert workspace.detail is not None
        original_source_revision = workspace.detail.entry.source.sha256
        original_resource_revision = workspace.detail.entry.summary.coordinate.fingerprint
        source = project / ".comparo" / "environments.yaml"
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "Stable reference behavior served from the loopback demo service.",
                "Stable reference behavior from a locally managed demo service.",
            ),
            encoding="utf-8",
        )

        workspace.action_reload()
        await _wait_until(
            pilot,
            lambda: (
                workspace.detail is not None
                and workspace.detail.entry.source.sha256 != original_source_revision
                and workspace.detail.representation is ResourceRepresentation.AUTHORED
                and workspace.detail.authored.phase is InspectionPhase.READY
            ),
        )

        assert workspace.detail is not None
        assert workspace.detail.entry.summary.coordinate.fingerprint == original_resource_revision
        assert workspace.detail.authored.value is not None
        assert workspace.detail.authored.value.source_sha256 == workspace.detail.entry.source.sha256


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

        summary = app.query_one(ResourceSummary)
        description = summary.query_one(".resource-description", Static)
        provenance = summary.query_one(".resource-provenance", Caption)
        tags = " ".join(f"#{tag}" for tag in request.summary.tags) or "untagged"
        assert str(description.content) == request.summary.description
        assert str(provenance.content) == (
            f"rev {request.summary.coordinate.fingerprint[:12]}… | "
            f"{request.source.filename} · line {request.summary.source.line} | {tags}"
        )
        assert not any(isinstance(child, KeyValueList) for child in summary.children)
        panel = app.query_one("#resource-detail-panel", Panel)
        assert panel.border_subtitle == ResourceRepresentation.OVERVIEW.value
        assert ResourceRepresentation.OVERVIEW.value not in str(panel.border_title)

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
        panel = app.query_one("#resource-detail-panel", Panel)
        assert panel.border_subtitle == ResourceRepresentation.AUTHORED.value
        assert ResourceRepresentation.AUTHORED.value not in str(panel.border_title)

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
