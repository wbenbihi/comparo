"""Author new execution profiles from explicit safe planning selections."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from comparo.application.planning import ClearProfileSelection
from comparo.application.planning import PlanningOptions
from comparo.application.preflight import resolve_project_root
from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.configuration.loader import compile_project_path
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString


class ProfileAuthoringError(ValueError):
    """Fixed user-facing explanation for an unrepresentable or invalid profile."""


@dataclass(frozen=True)
class ProfileDocument:
    """Exact destination and YAML reviewed before an exclusive write."""

    resource_id: str
    destination: Path
    content: str


def prepare_profile_document(
    workspace: ProjectWorkspaceRequest, options: PlanningOptions, resource_id: str, name: str
) -> ProfileDocument:
    """Serialize only representable authored selections, never live or resolved values."""
    _validate_selections(options, resource_id, name)
    loaded = compile_project_path(
        workspace.config,
        additional_sources=workspace.additional_sources,
        limits=workspace.load_limits,
    )
    project = loaded.compilation.project
    if project is None:
        raise ProfileAuthoringError("Correct the project configuration before saving a profile.")
    if any(
        item.definition.identity.kind.value == "ExecutionProfile"
        and item.definition.identity.resource_id.value == resource_id
        for item in project.resources
    ):
        raise ProfileAuthoringError("An ExecutionProfile with that ID already exists.")
    parent = _data_directory(workspace, project.project.spec)
    spec = _profile_spec(options)
    content = yaml.safe_dump(
        {
            "apiVersion": "comparo/v1alpha1",
            "kind": "ExecutionProfile",
            "metadata": {"id": resource_id, "name": name},
            "spec": spec,
        },
        sort_keys=False,
    )
    return ProfileDocument(resource_id, parent / f"execution-profile-{resource_id}.yaml", content)


def _validate_selections(options: PlanningOptions, resource_id: str, name: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", resource_id):
        raise ProfileAuthoringError(
            "Use a lowercase ID beginning with a letter (up to 63 characters)."
        )
    if not name or len(name) > 120 or not name.isprintable():
        raise ProfileAuthoringError("Enter a printable profile name of at most 120 characters.")
    if options.execution_profile is not None:
        raise ProfileAuthoringError("Choose Configure manually before saving a new profile.")
    if options.matrix_filters:
        raise ProfileAuthoringError(
            "Matrix case filters are invocation-only. Clear them before saving a profile."
        )
    if isinstance(options.profiles.gate, ClearProfileSelection):
        raise ProfileAuthoringError(
            "Clearing an inherited gate is invocation-only. Choose a gate or Inherit."
        )
    if any(
        (
            options.matrices,
            options.budgets,
            options.value_resources,
            options.inline_values,
            options.environment_values,
            options.requests,
        )
    ):
        raise ProfileAuthoringError(
            "Invocation value, matrix and request overrides cannot be saved."
        )


def _data_directory(workspace: ProjectWorkspaceRequest, spec: object) -> Path:
    if not isinstance(spec, CanonicalObject):
        raise ProfileAuthoringError("The Project must have a static data directory.")
    data = next((member.value for member in spec.members if member.name == "data"), None)
    if isinstance(data, (CanonicalObject, JsonObject)):
        data = next((member.value for member in data.members if member.name == "root"), None)
    if data is not None and not isinstance(data, JsonString):
        raise ProfileAuthoringError("The Project must have a static data directory.")
    root = resolve_project_root(workspace.config)
    relative = Path(data.value if isinstance(data, JsonString) else ".comparo")
    _validate_destination(root, root / relative)
    return root / relative


def _validate_destination(root: Path, parent: Path) -> None:
    if not parent.is_relative_to(root) or ".." in parent.parts:
        raise ProfileAuthoringError("Profile saving requires a project-local data directory.")
    current = root
    for part in parent.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ProfileAuthoringError("Profile saving does not follow directory symlinks.")


def _profile_spec(options: PlanningOptions) -> dict[str, object]:
    spec: dict[str, object] = {
        "subjects": [{"$use": f"Request/{item}"} for item in options.subjects]
    }
    if options.environments:
        spec["environments"] = [{"$use": item} for item in options.environments]
    if options.baseline:
        spec["baseline"] = {"$use": options.baseline}
        spec["candidates"] = [{"$use": item} for item in options.candidates]
    spec.update(
        {
            key: value
            for key, value in (
                ("trials", options.trials),
                ("maximumAttempts", options.maximum_attempts),
                ("failFast", options.fail_fast),
            )
            if value is not None
        }
    )
    for key, kind, values in (
        ("assertions", "AssertionProfile", options.profiles.assertions),
        ("diffs", "DiffProfile", options.profiles.diffs),
    ):
        if values is not None:
            spec[key] = [{"$use": f"{kind}/{item}"} for item in values]
    if isinstance(options.profiles.gate, str):
        spec["gate"] = {"$use": f"GateProfile/{options.profiles.gate}"}
    return spec


def save_profile_document(workspace: ProjectWorkspaceRequest, document: ProfileDocument) -> None:
    """Validate against the project and publish a new file without overwriting any source."""
    if document.destination.exists() or document.destination.is_symlink():
        raise FileExistsError
    parent = document.destination.parent
    _validate_destination(resolve_project_root(workspace.config), parent)
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tmp", dir=parent, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(document.content)
            output.flush()
            os.fsync(output.fileno())
        checked = compile_project_path(
            workspace.config,
            additional_sources=(*workspace.additional_sources, temporary),
            limits=workspace.load_limits,
        )
        if checked.compilation.project is None:
            raise ProfileAuthoringError(
                "The profile no longer compiles against this project. Review it again."
            )
        # Hard-link publication is atomic and fails if the exact destination already exists.
        os.link(temporary, document.destination, follow_symlinks=False)
        try:
            discovered = compile_project_path(
                workspace.config,
                additional_sources=workspace.additional_sources,
                limits=workspace.load_limits,
            ).compilation.project
            if discovered is None or not any(
                item.definition.identity.kind.value == "ExecutionProfile"
                and item.definition.identity.resource_id.value == document.resource_id
                for item in discovered.resources
            ):
                raise ProfileAuthoringError(
                    "The project's data filters exclude this profile. No file was kept."
                )
        except Exception:
            document.destination.unlink()
            raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
