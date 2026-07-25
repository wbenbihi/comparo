"""Tests for bounded application services over sanitized artifact bundles."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.artifacts import initialize_project_artifact_root
from comparo.application.artifacts import inspect_artifact
from comparo.application.artifacts import read_artifact_blob
from comparo.application.artifacts import render_artifact_report
from comparo.application.artifacts import verify_artifact
from comparo.core.artifacts.model import RunArtifact
from comparo.reporters import ReportFormat
from tests.adapters.artifacts.test_local_store import _stored_artifact
from tests.core.artifacts.test_capture_compiler import _limits as domain_limits


def _limits(**changes: int) -> ArtifactReadLimits:
    values = {
        "maximum_blobs": 16,
        "maximum_blob_bytes": 1024,
        "maximum_total_blob_bytes": 4096,
        "maximum_manifest_bytes": 1_000_000,
        "maximum_report_bytes": 1_000_000,
        "maximum_redaction_patterns": 4096,
        "maximum_redaction_pattern_bytes": 1_048_576,
        "maximum_redaction_value_bytes": 262_144,
        "maximum_redaction_value_nodes": 4096,
    }
    values.update(changes)
    return ArtifactReadLimits(**values)


def _bundle(tmp_path: Path) -> tuple[Path, RunArtifact]:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(root)
    outcome = asyncio.run(sink.persist(artifact, blobs, domain_limits()))
    sink.close()
    assert outcome.bundle_id == "artifact-0"
    return root, artifact


def test_project_artifact_root_initialization_is_private_idempotent_and_contained(
    tmp_path: Path,
) -> None:
    root = initialize_project_artifact_root(tmp_path.resolve(), Path(".comparo/artifacts"))
    assert root == (tmp_path / ".comparo" / "artifacts").resolve()
    assert root.is_dir()
    assert root.stat().st_mode & 0o022 == 0
    assert initialize_project_artifact_root(tmp_path.resolve(), Path(".comparo/artifacts")) == root


@pytest.mark.parametrize(
    ("project", "selected", "error"),
    [
        (cast("Path", object()), Path("artifacts"), TypeError),
        (Path("relative"), Path("artifacts"), ArtifactOperationError),
        (Path("/"), Path("/absolute"), ArtifactOperationError),
        (Path("/"), Path("../escape"), ArtifactOperationError),
    ],
)
def test_project_artifact_root_initialization_rejects_invalid_coordinates(
    project: Path,
    selected: Path,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        initialize_project_artifact_root(project, selected)


def test_project_artifact_root_initialization_rejects_unsafe_or_missing_roots(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ArtifactOperationError) as unavailable:
        initialize_project_artifact_root(missing, Path("artifacts"))
    assert unavailable.value.code is ArtifactOperationErrorCode.ROOT_UNAVAILABLE

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(ArtifactOperationError) as rejected:
        initialize_project_artifact_root(tmp_path.resolve(), Path("unsafe"))
    assert rejected.value.code is ArtifactOperationErrorCode.ROOT_UNSAFE

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ArtifactOperationError) as linked:
        initialize_project_artifact_root(tmp_path.resolve(), Path("linked/artifacts"))
    assert linked.value.code is ArtifactOperationErrorCode.ROOT_UNAVAILABLE


def test_verify_inspect_and_report_share_authenticated_artifact(tmp_path: Path) -> None:
    root, artifact = _bundle(tmp_path)

    assert verify_artifact(root, "artifact-0", _limits()) == artifact
    inspected = inspect_artifact(root, "artifact-0", _limits())
    report = render_artifact_report(root, "artifact-0", _limits(), ReportFormat.JUNIT)

    assert inspected.api_version == "comparo.artifact/v1alpha1"
    assert inspected.artifact_id == "artifact-0"
    assert inspected.executions == len(artifact.executions)
    assert inspected.comparisons == len(artifact.comparisons)
    assert inspected.assertions == sum(len(item.assertions) for item in artifact.executions)
    assert inspected.diffs == sum(len(item.diffs) for item in artifact.comparisons)
    assert inspected.blobs == 2
    assert inspected.blob_bytes == 9
    assert inspected.coverage_issues == len(artifact.coverage_issues)
    assert report.format is ReportFormat.JUNIT
    assert report.content.startswith(b"<?xml")


def test_blob_read_returns_only_explicit_sanitized_blob(tmp_path: Path) -> None:
    root, artifact = _bundle(tmp_path)
    expected = {4: {b"left"}, 5: {b"right"}}

    for blob in artifact.blobs:
        payload = asyncio.run(
            read_artifact_blob(
                root,
                "artifact-0",
                blob.sha256,
                _limits(),
                maximum_output_bytes=blob.byte_count,
            )
        )
        assert payload in expected[blob.byte_count]


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_blobs": 0},
        {"maximum_manifest_bytes": -1},
        {"maximum_blob_bytes": 5, "maximum_total_blob_bytes": 4},
    ],
)
def test_read_limits_reject_invalid_values(changes: dict[str, int]) -> None:
    with pytest.raises(ArtifactOperationError) as caught:
        _limits(**changes)
    assert caught.value.code is ArtifactOperationErrorCode.INVALID_LIMIT


def test_artifact_failures_are_value_free(tmp_path: Path) -> None:
    missing = tmp_path / "sensitive-project-name"
    with pytest.raises(ArtifactOperationError) as caught:
        verify_artifact(missing, "missing", _limits())

    assert caught.value.code is ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE
    assert "sensitive-project-name" not in str(caught.value)


def test_blob_failures_distinguish_selection_and_output_budget(tmp_path: Path) -> None:
    root, artifact = _bundle(tmp_path)
    blob = artifact.blobs[0]

    with pytest.raises(ArtifactOperationError) as unavailable:
        asyncio.run(
            read_artifact_blob(
                tmp_path / "missing",
                "artifact-0",
                blob.sha256,
                _limits(),
                maximum_output_bytes=1024,
            )
        )
    assert unavailable.value.code is ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE

    with pytest.raises(ArtifactOperationError) as missing:
        asyncio.run(
            read_artifact_blob(
                root,
                "artifact-0",
                "0" * 64,
                _limits(),
                maximum_output_bytes=1024,
            )
        )
    assert missing.value.code is ArtifactOperationErrorCode.BLOB_NOT_FOUND

    with pytest.raises(ArtifactOperationError) as exceeded:
        asyncio.run(
            read_artifact_blob(
                root,
                "artifact-0",
                blob.sha256,
                _limits(),
                maximum_output_bytes=blob.byte_count - 1,
            )
        )
    assert exceeded.value.code is ArtifactOperationErrorCode.OUTPUT_LIMIT_EXCEEDED

    with pytest.raises(ArtifactOperationError) as invalid:
        asyncio.run(
            read_artifact_blob(
                root,
                "artifact-0",
                blob.sha256,
                _limits(),
                maximum_output_bytes=-1,
            )
        )
    assert invalid.value.code is ArtifactOperationErrorCode.INVALID_LIMIT

    with pytest.raises(ArtifactOperationError) as wrong_type:
        asyncio.run(
            read_artifact_blob(
                root,
                "artifact-0",
                cast("str", object()),
                _limits(),
                maximum_output_bytes=1024,
            )
        )
    assert wrong_type.value.code is ArtifactOperationErrorCode.BLOB_NOT_FOUND


def test_report_budget_and_untyped_limits_fail_safely(tmp_path: Path) -> None:
    root, _artifact = _bundle(tmp_path)
    with pytest.raises(ArtifactOperationError) as report:
        render_artifact_report(
            root,
            "artifact-0",
            _limits(maximum_report_bytes=0),
            ReportFormat.MARKDOWN,
        )
    assert report.value.code is ArtifactOperationErrorCode.REPORT_FAILED

    with pytest.raises(TypeError, match="ArtifactReadLimits"):
        verify_artifact(root, "artifact-0", object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="error code"):
        ArtifactOperationError(cast("ArtifactOperationErrorCode", "bad"))
