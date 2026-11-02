"""Reviewed profile publication rejects invalid selections and changed destinations."""

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from comparo.application import ClearProfileSelection
from comparo.application import PlanningOptions
from comparo.application import ProfileSelectionOverrides
from comparo.application.profile_authoring import ProfileAuthoringError
from comparo.application.profile_authoring import prepare_profile_document
from comparo.application.profile_authoring import save_profile_document
from comparo.application.project_workspace import ProjectWorkspaceRequest


def _workspace(tmp_path: Path) -> ProjectWorkspaceRequest:
    project = tmp_path / "quickstart"
    shutil.copytree(Path(__file__).parents[2] / "examples" / "quickstart", project)
    return ProjectWorkspaceRequest(config=project / "comparo.yaml")


@pytest.mark.parametrize(
    ("resource_id", "name"),
    [
        ("Upper", "Valid"),
        ("a" * 64, "Valid"),
        ("valid", ""),
        ("valid", "a" * 121),
        ("valid", "a\nb"),
    ],
)
def test_profile_identity_must_be_bounded_printable_text(
    tmp_path: Path, resource_id: str, name: str
) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(ProfileAuthoringError):
        prepare_profile_document(workspace, PlanningOptions(), resource_id, name)
    assert not list((tmp_path / "quickstart" / ".comparo").glob("execution-profile-*.yaml"))


def test_execution_profile_and_gate_clear_cannot_be_saved_as_manual_selection(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    for options in (
        PlanningOptions(execution_profile="existing"),
        PlanningOptions(profiles=ProfileSelectionOverrides(gate=ClearProfileSelection())),
    ):
        with pytest.raises(ProfileAuthoringError):
            prepare_profile_document(workspace, options, "saved", "Saved")


def test_profile_preserves_explicit_empty_rules_gate_and_retry_controls(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    document = prepare_profile_document(
        workspace,
        PlanningOptions(
            subjects=("health",),
            environments=("candidate",),
            trials=3,
            maximum_attempts=2,
            fail_fast=False,
            profiles=ProfileSelectionOverrides(assertions=(), diffs=("body",), gate="strict"),
        ),
        "saved",
        "Saved",
    )
    spec = yaml.safe_load(document.content)["spec"]
    assert spec == {
        "subjects": [{"$use": "Request/health"}],
        "environments": [{"$use": "candidate"}],
        "trials": 3,
        "maximumAttempts": 2,
        "failFast": False,
        "assertions": [],
        "diffs": [{"$use": "DiffProfile/body"}],
        "gate": {"$use": "GateProfile/strict"},
    }
    assert not document.destination.exists()


def test_profile_review_and_save_reject_a_broken_project(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    document = prepare_profile_document(
        workspace, PlanningOptions(subjects=("health",)), "saved", "Saved"
    )
    workspace.config.write_text("not: a project\n")
    with pytest.raises(ProfileAuthoringError, match="Correct the project"):
        prepare_profile_document(workspace, PlanningOptions(), "other", "Other")
    with pytest.raises(ProfileAuthoringError, match="no longer compiles"):
        save_profile_document(workspace, document)
    assert not document.destination.exists()
    assert not list(document.destination.parent.glob("*.tmp"))


def test_profile_publication_rejects_escaping_and_symlinked_destinations(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    document = prepare_profile_document(
        workspace, PlanningOptions(subjects=("health",)), "saved", "Saved"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace.config.parent / "link"
    link.symlink_to(outside, target_is_directory=True)
    for destination in (
        outside / "saved.yaml",
        link / "saved.yaml",
        workspace.config.parent / ".." / "saved.yaml",
    ):
        with pytest.raises(ProfileAuthoringError):
            save_profile_document(workspace, replace(document, destination=destination))
        assert not destination.exists()
    assert list(outside.iterdir()) == []
