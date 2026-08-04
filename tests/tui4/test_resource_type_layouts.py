"""Matrix, Schema, and Value Explorer layout and resolution contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import override

import msgspec
from textual.app import App
from textual.app import ComposeResult
from textual.pilot import Pilot
from textual.widgets import TabbedContent

from comparo.application import ProjectWorkspaceRequest
from comparo.application import inspect_project_workspace
from comparo.tui4.app import ComparoApp
from comparo.tui4.backend import TuiBackend
from comparo.tui4.components.resources import resource_widget
from comparo.tui4.components.resources.matrix import MatrixContextStrip
from comparo.tui4.components.resources.matrix import MatrixResourceDetails
from comparo.tui4.components.resources.schema import SchemaContextStrip
from comparo.tui4.components.resources.schema import SchemaResourceDetails
from comparo.tui4.components.resources.value import ValueContextStrip
from comparo.tui4.components.resources.value import ValueResourceDetails
from comparo.tui4.components.table import Table
from comparo.tui4.components.typography import Hint
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.resource import InspectionPhase
from comparo.tui4.models.resource import ResolvedResourceView
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import initial_detail
from comparo.tui4.models.resource import loaded_resolved
from comparo.tui4.models.resource import loading_resolved
from comparo.tui4.models.resource_presenters import matrix_overview
from comparo.tui4.models.resource_presenters import resolved_fields
from comparo.tui4.models.resource_presenters import resource_resolution_paths
from comparo.tui4.models.resource_presenters import resource_resolution_subject_id
from comparo.tui4.models.resource_presenters import resource_resolution_subject_ids
from comparo.tui4.models.resource_presenters import schema_overview
from comparo.tui4.models.resource_presenters import value_overview
from comparo.tui4.workspaces.explorer import ExplorerWorkspace

PROJECT = Path(__file__).parents[2] / "examples" / "tui-showcase"


def _catalog() -> ExplorerCatalog:
    """Compile the comprehensive showcase through the public application facade."""
    result = inspect_project_workspace(ProjectWorkspaceRequest(config=PROJECT))
    catalog = build_catalog(result)
    assert catalog is not None
    return catalog


def _entry(catalog: ExplorerCatalog, kind: str, resource_id: str) -> ResourceEntry:
    """Select one exact showcase resource."""
    return next(
        entry
        for entry in catalog.entries.values()
        if entry.summary.coordinate.kind == kind
        and entry.summary.coordinate.resource_id == resource_id
    )


async def _wait_until(pilot: Pilot[None], predicate: Callable[[], bool]) -> None:
    """Wait for worker-backed state with a short deterministic ceiling."""
    for _attempt in range(200):
        if predicate():
            return
        await pilot.pause(0.01)
    raise AssertionError("condition was not reached")


def _collision_catalog(root: Path) -> ExplorerCatalog:
    """Compile valid dotted property names that collide only when joined for display."""
    data = root / ".comparo"
    data.mkdir()
    (root / "comparo.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: collisions, name: Collisions}\n"
        "spec: {data: .comparo}\n",
        encoding="utf-8",
    )
    (data / "resources.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Schema\n"
        "metadata: {id: dotted, name: Dotted schema}\n"
        "spec:\n"
        "  type: object\n"
        "  properties:\n"
        "    'a.b': {type: string}\n"
        "    a:\n"
        "      type: object\n"
        "      properties:\n"
        "        b: {type: string}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Value\n"
        "metadata: {id: dotted-value, name: Dotted value}\n"
        "spec:\n"
        "  value:\n"
        "    'a.b': {$env: FIRST}\n"
        "    a:\n"
        "      b: {$env: SECOND}\n",
        encoding="utf-8",
    )
    catalog = build_catalog(inspect_project_workspace(ProjectWorkspaceRequest(config=root)))
    assert catalog is not None
    return catalog


def test_matrix_projection_keeps_authored_and_planned_semantics_distinct() -> None:
    catalog = _catalog()
    audiences = _entry(catalog, "Matrix", "audiences")
    locales = _entry(catalog, "Matrix", "locales")

    product = matrix_overview(audiences)
    explicit = matrix_overview(locales)

    assert product.mode == "axes"
    assert product.combine == "product"
    assert tuple(axis.id for axis in product.axes) == ("region", "plan")
    assert tuple(option.id for option in product.axes[0].options) == ("eu", "us")
    assert tuple(case.id for case in product.cases) == ("enterprise-apac",)
    assert product.exclusions == ("us-free",)
    assert tuple(item.target_id for item in product.consumers) == ("catalog",)
    assert explicit.mode == "explicit cases"
    assert tuple(case.id for case in explicit.cases) == ("english", "french")


def test_schema_and_value_projections_expose_complete_safe_anatomy() -> None:
    catalog = _catalog()
    schema = schema_overview(_entry(catalog, "Schema", "catalog-response"))
    value = value_overview(_entry(catalog, "Value", "catalog-query"))

    assert schema.source == "local"
    assert schema.root_type == "object"
    assert schema.required == ("contractVersion", "items")
    assert tuple((item.path, item.type, item.required) for item in schema.properties) == (
        (("contractVersion",), "integer", True),
        (("items",), "array", True),
    )
    assert tuple(item.target_id for item in schema.consumers) == ("catalog",)
    assert value.value is not None
    assert value.value.value == "object · 3 members"
    assert tuple((item.path, item.expression) for item in value.directives) == (
        (("region",), "$matrix.region"),
        (("plan",), "$matrix.plan"),
        (("locale",), "$matrix.locale"),
    )
    assert tuple(item.target_id for item in value.consumers) == ("catalog",)


def test_matrix_and_value_resolution_select_their_declared_consumer_scope() -> None:
    catalog = _catalog()
    matrix = _entry(catalog, "Matrix", "audiences")
    value = _entry(catalog, "Value", "catalog-query")
    shared = _entry(catalog, "Value", "common-headers")

    assert resource_resolution_subject_id(matrix) == "catalog"
    assert resource_resolution_subject_id(value) == "catalog"
    assert resource_resolution_subject_id(shared) is None
    assert resource_resolution_subject_ids(shared) == ("catalog", "health", "release")
    assert resource_resolution_paths(matrix) == (("axes",), ("include",))
    assert resource_resolution_paths(value) == (("value",),)


def test_backend_reuses_compare_planning_and_masked_resolution_for_resource_kinds() -> None:
    async def inspect() -> None:
        catalog = _catalog()
        environment = next(
            entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
        )
        matrix = _entry(catalog, "Matrix", "audiences")
        value = _entry(catalog, "Value", "catalog-query")
        shared = _entry(catalog, "Value", "common-headers")
        backend = TuiBackend(PROJECT)
        try:
            matrix_batch = await backend.inspect_resolved_fields(
                matrix.summary.coordinate,
                environment.summary.coordinate,
                resource_resolution_paths(matrix),
                subject_id=resource_resolution_subject_id(matrix),
            )
            value_batch = await backend.inspect_resolved_fields(
                value.summary.coordinate,
                environment.summary.coordinate,
                resource_resolution_paths(value),
                subject_id=resource_resolution_subject_id(value),
            )
            shared_batch = await backend.inspect_resolved_fields(
                shared.summary.coordinate,
                environment.summary.coordinate,
                resource_resolution_paths(shared),
                subject_id="catalog",
            )
            rejected_implicit = await backend.inspect_resolved_fields(
                shared.summary.coordinate,
                environment.summary.coordinate,
                resource_resolution_paths(shared),
            )
        finally:
            await backend.shutdown()

        assert matrix_batch.diagnostics == ()
        assert matrix_batch.matrix_plan is not None
        assert matrix_batch.matrix_plan.subject.resource_id == "catalog"
        assert matrix_batch.matrix_plan.cases == (
            "eu-free",
            "eu-pro",
            "us-pro",
            "enterprise-apac",
        )
        assert matrix_batch.total_occurrences == 8
        assert matrix_batch.occurrence is not None
        assert tuple((item.binding, item.case_id) for item in matrix_batch.occurrence.matrices) == (
            ("audiences", "eu-free"),
            ("locales", "english"),
        )
        assert value_batch.values is not None
        values = {field.path: field.value for field in resolved_fields(value_batch.values).fields}
        assert values == {("value",): "<masked>"}
        assert shared_batch.values is not None
        shared_values = {
            field.path: field.value for field in resolved_fields(shared_batch.values).fields
        }
        assert shared_values == {("value",): "<masked>"}
        assert "application/json" not in msgspec.json.encode(shared_batch.values).decode()
        assert rejected_implicit.diagnostics == ("resolution.subject.required",)

    asyncio.run(inspect())


def test_dedicated_kind_widgets_use_native_tabs_and_restore_sections() -> None:
    catalog = _catalog()
    matrix_entry = _entry(catalog, "Matrix", "audiences")
    schema_entry = _entry(catalog, "Schema", "catalog-response")
    value_entry = _entry(catalog, "Value", "catalog-query")
    subject = _entry(catalog, "Request", "catalog")
    environment = next(
        entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
    )
    matrix_detail = loading_resolved(initial_detail(matrix_entry, environment.summary.coordinate))
    matrix_detail = loaded_resolved(
        matrix_detail,
        ResolvedResourceView(
            environment=environment.summary.coordinate,
            subject=subject.summary.coordinate,
            execution_ordinal=0,
            total_occurrences=1,
            cell_id="cell",
            trial_id="trial",
            fields=(),
        ),
    )
    schema_detail = initial_detail(schema_entry, environment.summary.coordinate)
    value_detail = initial_detail(value_entry, environment.summary.coordinate)

    class ResourceKindsApp(App[None]):
        @override
        def compose(self) -> ComposeResult:
            yield resource_widget(matrix_detail, section="matrix-cases")
            yield resource_widget(schema_detail, section="schema-composition")
            yield resource_widget(value_detail, section="value-inputs")

    async def inspect() -> None:
        app = ResourceKindsApp()
        async with app.run_test(size=(180, 60)):
            assert type(app.query_one(MatrixResourceDetails)) is MatrixResourceDetails
            assert type(app.query_one(SchemaResourceDetails)) is SchemaResourceDetails
            assert type(app.query_one(ValueResourceDetails)) is ValueResourceDetails
            assert len(app.query(MatrixContextStrip)) == 1
            assert len(app.query(SchemaContextStrip)) == 1
            assert len(app.query(ValueContextStrip)) == 1
            assert app.query_one("#matrix-tabs", TabbedContent).active == "matrix-cases"
            assert app.query_one("#schema-tabs", TabbedContent).active == "schema-composition"
            assert app.query_one("#value-tabs", TabbedContent).active == "value-inputs"

    asyncio.run(inspect())


def test_dotted_display_paths_never_become_datatable_identity(tmp_path: Path) -> None:
    catalog = _collision_catalog(tmp_path)
    schema = initial_detail(_entry(catalog, "Schema", "dotted"))
    value = initial_detail(_entry(catalog, "Value", "dotted-value"))

    class CollisionApp(App[None]):
        @override
        def compose(self) -> ComposeResult:
            yield resource_widget(schema)
            yield resource_widget(value, section="value-inputs")

    async def inspect() -> None:
        app = CollisionApp()
        async with app.run_test(size=(140, 45)):
            tables = tuple(app.query(Table))
            assert len(tables) == 2
            assert tuple(len(table.records) for table in tables) == (3, 2)
            for table in tables:
                identities = tuple(record.id for record in table.records)
                assert len(identities) == len(set(identities))

    asyncio.run(inspect())


def test_explorer_rejects_implicit_resolution_when_consumers_are_ambiguous() -> None:
    async def inspect() -> None:
        app = ComparoApp(PROJECT)
        async with app.run_test(size=(180, 55)) as pilot:
            workspace = app.query_one(ExplorerWorkspace)
            await _wait_until(pilot, lambda: workspace.catalog is not None)
            assert workspace.catalog is not None
            shared = _entry(workspace.catalog, "Value", "common-headers")
            workspace.selected = shared.key
            await _wait_until(
                pilot,
                lambda: workspace.detail is not None and workspace.detail.key == shared.key,
            )

            workspace.action_toggle_resource_resolution()
            await pilot.pause()

            assert workspace.detail is not None
            assert workspace.detail.overview_mode is ResourceOverviewMode.RESOLVED
            assert workspace.detail.resolved.phase is InspectionPhase.UNAVAILABLE
            assert workspace.detail.resolved.message == (
                "Choose an explicit consumer before resolving (3 available)."
            )

    asyncio.run(inspect())


def test_matrix_plan_copy_tracks_resolution_lifecycle() -> None:
    catalog = _catalog()
    matrix = _entry(catalog, "Matrix", "audiences")
    environment = next(
        entry for entry in catalog.entries.values() if entry.facts.role == "baseline"
    )
    loading = loading_resolved(initial_detail(matrix, environment.summary.coordinate))

    class LoadingMatrixApp(App[None]):
        @override
        def compose(self) -> ComposeResult:
            yield resource_widget(loading, section="matrix-plan")

    async def inspect() -> None:
        app = LoadingMatrixApp()
        async with app.run_test(size=(140, 35)):
            copy = tuple(str(item.content) for item in app.query(Hint))
            assert any("Compiling Matrix cases" in item for item in copy)
            assert all("Press r to compile" not in item for item in copy)

    asyncio.run(inspect())
