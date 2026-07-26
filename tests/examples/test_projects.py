"""Regression checks for the runnable example project catalog."""

from pathlib import Path
from uuid import UUID

import pytest

from comparo.application.planning import PlanningOptions
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import prepare_project_plan
from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.application.project_workspace import inspect_project_workspace
from comparo.application.runtime_capabilities import preflight_runtime_capabilities

_EXAMPLES = Path(__file__).parents[2] / "examples"


@pytest.mark.parametrize(
    ("project_id", "resources", "cells", "executions", "comparisons"),
    [
        ("quickstart", 8, 1, 2, 1),
        ("environment-drift", 15, 4, 8, 4),
        ("tui-showcase", 23, 8, 16, 8),
    ],
)
def test_example_projects_compile_plan_and_target_supported_runtime_capabilities(
    project_id: str,
    resources: int,
    cells: int,
    executions: int,
    comparisons: int,
) -> None:
    config = _EXAMPLES / project_id / "comparo.yaml"
    workspace = inspect_project_workspace(ProjectWorkspaceRequest(config=config))

    assert workspace.diagnostics == ()
    assert workspace.snapshot is not None
    assert workspace.snapshot.project.coordinate.resource_id == project_id
    assert len(workspace.snapshot.resources) == resources
    assert all(
        resource.name and resource.description and resource.tags
        for resource in workspace.snapshot.resources
    )

    prepared = prepare_project_plan(
        ProjectPlanRequest(
            config=config,
            additional_sources=(),
            policy=None,
            options=PlanningOptions(),
            invocation_id=UUID("10000000-0000-0000-0000-000000000001"),
            run_id=UUID("20000000-0000-0000-0000-000000000002"),
        )
    )

    assert prepared.diagnostics == ()
    assert prepared.project is not None
    cardinality = prepared.project.prepared.plan.cardinality
    assert cardinality.cells.value == cells
    assert cardinality.environment_executions.value == executions
    assert cardinality.comparisons.value == comparisons
    assert preflight_runtime_capabilities(prepared.project).supported


def test_showcase_contains_two_independently_bound_matrices() -> None:
    config = _EXAMPLES / "tui-showcase" / "comparo.yaml"
    prepared = prepare_project_plan(
        ProjectPlanRequest(
            config=config,
            additional_sources=(),
            policy=None,
            options=PlanningOptions(),
            invocation_id=UUID("30000000-0000-0000-0000-000000000003"),
            run_id=UUID("40000000-0000-0000-0000-000000000004"),
        )
    )

    assert prepared.project is not None
    matrices = prepared.project.prepared.effective.subjects[0].matrices
    assert tuple(matrix.binding.value for matrix in matrices) == ("audiences", "locales")
    assert tuple(len(matrix.cases) for matrix in matrices) == (4, 2)


def test_showcase_explorer_projects_inverse_resource_consumers() -> None:
    workspace = inspect_project_workspace(
        ProjectWorkspaceRequest(config=_EXAMPLES / "tui-showcase" / "comparo.yaml")
    )

    assert workspace.snapshot is not None
    audiences = next(
        resource
        for resource in workspace.snapshot.resources
        if resource.coordinate.kind == "Matrix" and resource.coordinate.resource_id == "audiences"
    )
    assert audiences.outline is not None
    consumers = tuple(
        relationship.target
        for relationship in audiences.outline.relationships
        if relationship.origin == "used-by"
    )
    assert any(
        consumer.kind == "Request" and consumer.resource_id == "catalog" for consumer in consumers
    )
