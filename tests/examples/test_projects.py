"""Regression checks for the runnable example project catalog."""

import asyncio
import importlib.util
import shutil
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from comparo.application import CompareExecutionControls
from comparo.application import CompareIntent
from comparo.application import ComparePrepareRequest
from comparo.application import CompareService
from comparo.application import persisted_production_execution_defaults_v1
from comparo.application.planning import PlanningOptions
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import prepare_project_plan
from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.application.project_workspace import inspect_project_workspace
from comparo.application.runtime_capabilities import preflight_runtime_capabilities
from comparo.core.domain.identity import DefinitionRef

_EXAMPLES = Path(__file__).parents[2] / "examples"


def _demo_request_handler() -> type[BaseHTTPRequestHandler]:
    """Load the uninstalled example server without changing the test import path."""
    source = _EXAMPLES / "support" / "demo_server.py"
    spec = importlib.util.spec_from_file_location("comparo_example_demo_server", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("example demo server is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, "DemoRequestHandler", None)
    if not isinstance(handler, type) or not issubclass(handler, BaseHTTPRequestHandler):
        raise RuntimeError("example demo server has no HTTP request handler")
    return handler


@pytest.mark.parametrize(
    ("project_id", "resources", "cells", "executions", "comparisons"),
    [
        ("quickstart", 8, 1, 2, 1),
        ("environment-drift", 15, 4, 8, 4),
        ("tui-showcase", 44, 8, 16, 8),
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


def test_showcase_request_catalog_covers_supported_shapes_and_honest_edge_states() -> None:
    config = _EXAMPLES / "tui-showcase" / "comparo.yaml"
    workspace = inspect_project_workspace(ProjectWorkspaceRequest(config=config))

    assert workspace.snapshot is not None
    requests = {
        resource.coordinate.resource_id: resource
        for resource in workspace.snapshot.resources
        if resource.coordinate.kind == "Request"
    }
    assert set(requests) == {
        "absolute-health",
        "all-inputs",
        "bytes-payload",
        "catalog",
        "directive-inputs",
        "form-payload",
        "health",
        "inline-contract",
        "json-payload",
        "multipart-payload",
        "ordered-occurrences",
        "release",
        "remote-schema",
        "request-local-policies",
        "streaming-events",
        "text-payload",
        "unavailable-credential",
    }
    assert all(request.outline is not None for request in requests.values())
    body_requests = {
        request_id
        for request_id, request in requests.items()
        if request.outline is not None
        and any(field.path == ("request", "bodyType") for field in request.outline.fields)
    }
    assert body_requests == {
        "bytes-payload",
        "form-payload",
        "json-payload",
        "multipart-payload",
        "text-payload",
    }
    remote = requests["remote-schema"]
    assert remote.outline is not None
    assert (
        next(field for field in remote.outline.fields if field.path == ("responseSchema",)).value
        == "remote schema (URI concealed)"
    )

    for profile in ("streaming-chunks", "read-routing"):
        prepared = prepare_project_plan(
            ProjectPlanRequest(
                config=config,
                additional_sources=(),
                policy=None,
                options=PlanningOptions(execution_profile=profile),
                invocation_id=UUID("50000000-0000-0000-0000-000000000005"),
                run_id=UUID("60000000-0000-0000-0000-000000000006"),
            )
        )
        assert prepared.diagnostics == ()
        assert prepared.project is not None
        assert preflight_runtime_capabilities(prepared.project).supported


def test_showcase_streaming_profile_compares_semantic_sse_content() -> None:
    config = _EXAMPLES / "tui-showcase" / "comparo.yaml"
    workspace = inspect_project_workspace(ProjectWorkspaceRequest(config=config))
    prepared = prepare_project_plan(
        ProjectPlanRequest(
            config=config,
            additional_sources=(),
            policy=None,
            options=PlanningOptions(execution_profile="streaming-chunks"),
            invocation_id=UUID("70000000-0000-0000-0000-000000000007"),
            run_id=UUID("80000000-0000-0000-0000-000000000008"),
        )
    )

    assert workspace.snapshot is not None
    semantic = next(
        resource
        for resource in workspace.snapshot.resources
        if resource.coordinate.resource_id == "equivalent-stream-content"
    )
    assert semantic.outline is not None
    semantic_paths = {field.path for field in semantic.outline.fields}
    assert ("rules", "0", "transforms", "0", "name") in semantic_paths
    assert ("rules", "0", "reducer", "name") in semantic_paths

    assert prepared.project is not None
    subject = prepared.project.prepared.effective.subjects[0]
    assert all(type(profile) is DefinitionRef for profile in subject.profiles.diffs)
    assert tuple(
        cast(DefinitionRef, profile).identity.resource_id.value
        for profile in subject.profiles.diffs
    ) == (
        "stable-status",
        "equivalent-stream-content",
    )


def test_showcase_streaming_profile_passes_end_to_end(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _demo_request_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project = tmp_path / "tui-showcase"
    shutil.copytree(
        _EXAMPLES / "tui-showcase",
        project,
        ignore=shutil.ignore_patterns("artifacts"),
    )
    port = int(server.server_address[1])
    environments = project / ".comparo" / "environments.yaml"
    environments.write_text(
        environments.read_text(encoding="utf-8").replace("18080", str(port)),
        encoding="utf-8",
    )
    artifact_root = project / ".comparo" / "artifacts"
    artifact_root.mkdir()

    async def execute() -> None:
        controls = CompareExecutionControls(
            settings=persisted_production_execution_defaults_v1(),
            artifact_root=artifact_root,
            approve_mutations=False,
            tool_version="test",
        )
        compare = CompareService(maximum_outstanding=1)
        try:
            prepared = compare.prepare(
                ComparePrepareRequest(
                    intent=CompareIntent.EXECUTION_PROFILE,
                    project=ProjectPlanRequest(
                        config=project / "comparo.yaml",
                        additional_sources=(),
                        policy=None,
                        options=PlanningOptions(execution_profile="streaming-chunks"),
                        invocation_id=UUID("90000000-0000-0000-0000-000000000009"),
                        run_id=UUID("a0000000-0000-0000-0000-00000000000a"),
                    ),
                    controls=controls,
                )
            )
            assert prepared.diagnostics == ()
            assert prepared.handle is not None
            session = compare.start(prepared.handle, controls)
            result = await session.result()
            await session.close()

            assert result.diagnostics == ()
            assert result.artifact is not None
            artifact = result.artifact
            assert artifact.completion.kind.value == "complete"
            assert artifact.gate.verdict.value == "passed"
            assert len(artifact.executions) == 2
            assert all(
                all(fact.outcome.value == "satisfied" for fact in execution.assertions)
                for execution in artifact.executions
            )
            assert len(artifact.comparisons) == 1
            assert tuple(fact.outcome.value for fact in artifact.comparisons[0].diffs) == (
                "equivalent",
                "equivalent",
            )
        finally:
            compare.close()

    try:
        asyncio.run(execute())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
