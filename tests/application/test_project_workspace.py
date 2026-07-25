"""Safe Explorer project-workspace facade tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import SourceCoordinate
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.project_workspace import ProjectSourceSummary
from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.application.project_workspace import ProjectWorkspaceSnapshot
from comparo.application.project_workspace import inspect_project_workspace
from comparo.configuration.loader import DEFAULT_PROJECT_LOAD_LIMITS

_SECRET_CANARY = "workspace-secret-canary"


def _write_project(root: Path) -> Path:
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata:\n"
        "  id: demo\n"
        "  name: Demo project\n"
        "  description: Explorer fixture\n"
        "  tags: [local, smoke-test]\n"
        "spec: {data: .comparo}\n",
        encoding="utf-8",
    )
    data = root / ".comparo"
    data.mkdir()
    (data / "resources.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: staging, name: Staging, tags: [pre-release]}\n"
        "spec: {baseUrl: https://staging.example.test}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Request\n"
        "metadata: {id: ping, name: Ping}\n"
        "spec: {request: {method: GET, endpoint: /ping}}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Value\n"
        "metadata: {id: credentials, name: Credentials}\n"
        f"spec: {{value: {{token: {_SECRET_CANARY}}}}}\n",
        encoding="utf-8",
    )
    return config


def _coordinate(kind: str = "Request", resource_id: str = "ping") -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="local",
        kind=kind,
        resource_id=resource_id,
        fingerprint="0" * 64,
    )


def _resource(kind: str = "Project", resource_id: str = "demo") -> ProjectResourceSummary:
    return ProjectResourceSummary(
        coordinate=_coordinate(kind, resource_id),
        name="Name",
        description="Description",
        tags=("tag",),
        source=SourceCoordinate(source="entry-config", document=1, line=1, column=1),
    )


def _snapshot() -> ProjectWorkspaceSnapshot:
    return ProjectWorkspaceSnapshot(
        api_version="comparo/v1alpha1",
        namespace="local",
        project=_resource(),
        resources=(_resource("Request", "ping"),),
    )


def _source(
    *resources: ResourceCoordinate,
    source_id: str = "entry-config",
    entry: bool = True,
) -> ProjectSourceSummary:
    return ProjectSourceSummary(
        source_id=source_id,
        path=str(Path.cwd() / "comparo.yaml"),
        entry=entry,
        resources=resources,
    )


def test_workspace_projects_only_safe_resource_metadata_and_source_membership(
    tmp_path: Path,
) -> None:
    outcome = inspect_project_workspace(ProjectWorkspaceRequest(config=_write_project(tmp_path)))

    snapshot = outcome.snapshot
    assert snapshot is not None
    assert not outcome.diagnostics
    assert snapshot.api_version == "comparo/v1alpha1"
    assert snapshot.namespace == "local"
    assert snapshot.project.name == "Demo project"
    assert snapshot.project.description == "Explorer fixture"
    assert snapshot.project.tags == ("local", "smoke-test")
    assert tuple(item.coordinate.kind for item in snapshot.resources) == (
        "Environment",
        "Request",
        "Value",
    )
    assert tuple(item.coordinate.resource_id for item in snapshot.resources) == (
        "staging",
        "ping",
        "credentials",
    )
    assert tuple(source.entry for source in outcome.sources) == (True, False)
    assert outcome.sources[0].resources == (snapshot.project.coordinate,)
    assert set(outcome.sources[1].resources) == {
        resource.coordinate for resource in snapshot.resources
    }
    assert all(Path(source.path).is_absolute() for source in outcome.sources)
    assert "spec" not in ProjectResourceSummary.__struct_fields__
    encoded = msgspec.json.encode(outcome).decode()
    assert _SECRET_CANARY not in encoded
    assert "https://staging.example.test" not in encoded


def test_workspace_returns_value_free_diagnostics_and_available_source_catalog(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "comparo.yaml"
    invalid.write_text(f"{_SECRET_CANARY}: [", encoding="utf-8")

    outcome = inspect_project_workspace(ProjectWorkspaceRequest(config=invalid))

    assert outcome.snapshot is None
    assert outcome.diagnostics
    assert {item.stage for item in outcome.diagnostics} == {"configuration"}
    assert len(outcome.sources) == 1
    assert outcome.sources[0].entry
    assert outcome.sources[0].resources == ()
    assert _SECRET_CANARY not in msgspec.json.encode(outcome).decode()

    missing = inspect_project_workspace(ProjectWorkspaceRequest(config=tmp_path / "missing.yaml"))
    assert missing.snapshot is None
    assert missing.sources == ()
    assert "configuration.source.entry-missing" in {item.code for item in missing.diagnostics}


def test_workspace_applies_sources_and_caller_owned_load_limits(tmp_path: Path) -> None:
    config = _write_project(tmp_path)
    extra = tmp_path / "extra.yaml"
    extra.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Schema\n"
        "metadata: {id: external, name: External}\n"
        "spec: {type: object}\n",
        encoding="utf-8",
    )
    accepted = inspect_project_workspace(
        ProjectWorkspaceRequest(config=config, additional_sources=(extra,))
    )
    assert accepted.snapshot is not None
    assert any(
        resource.coordinate.resource_id == "external" for resource in accepted.snapshot.resources
    )

    limits = msgspec.structs.replace(DEFAULT_PROJECT_LOAD_LIMITS, maximum_sources=1)
    rejected = inspect_project_workspace(
        ProjectWorkspaceRequest(
            config=config,
            additional_sources=(extra,),
            load_limits=limits,
        )
    )
    assert rejected.snapshot is None
    assert "configuration.source.limit-exceeded" in {item.code for item in rejected.diagnostics}


def test_workspace_contains_unexpected_loader_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_safely(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(_SECRET_CANARY)

    monkeypatch.setattr(
        "comparo.application.project_workspace.compile_project_path",
        fail_safely,
    )
    outcome = inspect_project_workspace(ProjectWorkspaceRequest(config=Path("comparo.yaml")))
    assert outcome.snapshot is None
    assert outcome.sources == ()
    assert outcome.diagnostics == (
        ApplicationDiagnostic(
            code="project.workspace.internal",
            severity="error",
            stage="configuration",
        ),
    )
    assert _SECRET_CANARY not in msgspec.json.encode(outcome).decode()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"config": object()}, "config"),
        ({"additional_sources": []}, "sources"),
        ({"additional_sources": (object(),)}, "sources"),
        ({"load_limits": object()}, "load limits"),
    ],
)
def test_workspace_request_rejects_untyped_inputs(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "config": Path("comparo.yaml"),
        "additional_sources": (),
        "load_limits": DEFAULT_PROJECT_LOAD_LIMITS,
    }
    values.update(changes)
    with pytest.raises(TypeError, match=message):
        ProjectWorkspaceRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"coordinate": object()}, "coordinate"),
        ({"name": 1}, "metadata"),
        ({"description": 1}, "metadata"),
        ({"tags": []}, "tags"),
        ({"tags": (object(),)}, "tags"),
        ({"source": object()}, "source"),
    ],
)
def test_resource_summary_rejects_untyped_state(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "coordinate": _coordinate(),
        "name": None,
        "description": None,
        "tags": (),
        "source": SourceCoordinate(source="source", document=1, line=1, column=1),
    }
    values.update(changes)
    with pytest.raises(TypeError, match=message):
        ProjectResourceSummary(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"source_id": 1}, TypeError, "ID"),
        ({"source_id": ""}, ValueError, "ID"),
        ({"path": Path("relative")}, TypeError, "path"),
        ({"path": ""}, ValueError, "path"),
        ({"path": "relative"}, ValueError, "path"),
        ({"entry": 1}, TypeError, "entry"),
        ({"resources": []}, TypeError, "resources"),
        ({"resources": (object(),)}, TypeError, "resources"),
        ({"resources": (_coordinate(), _coordinate())}, ValueError, "unique"),
    ],
)
def test_source_summary_rejects_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    values: dict[str, object] = {
        "source_id": "source",
        "path": str(Path.cwd() / "source.yaml"),
        "entry": False,
        "resources": (),
    }
    values.update(changes)
    with pytest.raises(error, match=message):
        ProjectSourceSummary(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"api_version": 1}, ValueError, "API version"),
        ({"api_version": ""}, ValueError, "API version"),
        ({"namespace": 1}, ValueError, "namespace"),
        ({"namespace": ""}, ValueError, "namespace"),
        ({"project": object()}, TypeError, "root"),
        ({"project": _resource("Request", "wrong")}, ValueError, "Project"),
        ({"resources": []}, TypeError, "resources"),
        ({"resources": (object(),)}, TypeError, "resources"),
        ({"resources": (_resource(),)}, ValueError, "contain Project"),
        (
            {"resources": (_resource("Request", "ping"), _resource("Request", "ping"))},
            ValueError,
            "unique",
        ),
    ],
)
def test_workspace_snapshot_rejects_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    values: dict[str, object] = {
        "api_version": "comparo/v1alpha1",
        "namespace": "local",
        "project": _resource(),
        "resources": (_resource("Request", "ping"),),
    }
    values.update(changes)
    with pytest.raises(error, match=message):
        ProjectWorkspaceSnapshot(**values)  # type: ignore[arg-type]


def test_workspace_result_rejects_invalid_outer_state() -> None:
    diagnostic = ApplicationDiagnostic(code="test", severity="error", stage="configuration")
    with pytest.raises(TypeError, match="snapshot"):
        ProjectWorkspaceResult(snapshot=cast("ProjectWorkspaceSnapshot", object()))
    with pytest.raises(TypeError, match="sources"):
        ProjectWorkspaceResult(
            diagnostics=(diagnostic,),
            sources=cast("tuple[ProjectSourceSummary, ...]", []),
        )
    with pytest.raises(TypeError, match="sources"):
        ProjectWorkspaceResult(
            diagnostics=(diagnostic,),
            sources=cast("tuple[ProjectSourceSummary, ...]", (object(),)),
        )
    with pytest.raises(TypeError, match="diagnostics"):
        ProjectWorkspaceResult(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", []))
    with pytest.raises(TypeError, match="diagnostics"):
        ProjectWorkspaceResult(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", (object(),)))
    with pytest.raises(ValueError, match="requires diagnostics"):
        ProjectWorkspaceResult()
    assert ProjectWorkspaceResult(diagnostics=(diagnostic,)).diagnostics == (diagnostic,)


def test_workspace_result_rejects_invalid_success_source_closure() -> None:
    snapshot = _snapshot()
    project = snapshot.project.coordinate
    request = snapshot.resources[0].coordinate
    diagnostic = ApplicationDiagnostic(code="test", severity="error", stage="configuration")
    with pytest.raises(ValueError, match="cannot contain diagnostics"):
        ProjectWorkspaceResult(
            snapshot=snapshot,
            sources=(_source(project, request),),
            diagnostics=(diagnostic,),
        )
    with pytest.raises(ValueError, match="one entry"):
        ProjectWorkspaceResult(snapshot=snapshot, sources=(_source(project, request, entry=False),))
    with pytest.raises(ValueError, match="source IDs"):
        ProjectWorkspaceResult(
            snapshot=snapshot,
            sources=(
                _source(project, source_id="same"),
                _source(request, source_id="same", entry=False),
            ),
        )
    with pytest.raises(ValueError, match="cover every resource"):
        ProjectWorkspaceResult(
            snapshot=snapshot,
            sources=(
                _source(project),
                _source(project, source_id="duplicate", entry=False),
            ),
        )
    with pytest.raises(ValueError, match="cover every resource"):
        ProjectWorkspaceResult(snapshot=snapshot, sources=(_source(project),))
    accepted = ProjectWorkspaceResult(
        snapshot=snapshot,
        sources=(
            _source(project),
            _source(request, source_id="resources", entry=False),
        ),
    )
    assert accepted.snapshot == snapshot


def test_workspace_inspection_rejects_untyped_request() -> None:
    with pytest.raises(TypeError, match="ProjectWorkspaceRequest"):
        inspect_project_workspace(cast("ProjectWorkspaceRequest", object()))
