"""Tests for bounded application services over sanitized artifact bundles."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.application import artifacts as artifacts_module
from comparo.application.artifacts import ArtifactBodyMessage
from comparo.application.artifacts import ArtifactBodyReference
from comparo.application.artifacts import ArtifactBodyWindow
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.artifacts import initialize_project_artifact_root
from comparo.application.artifacts import inspect_artifact
from comparo.application.artifacts import read_artifact_blob
from comparo.application.artifacts import read_artifact_body_window
from comparo.application.artifacts import render_artifact_report
from comparo.application.artifacts import validate_existing_artifact_root
from comparo.application.artifacts import validate_project_artifact_root
from comparo.application.artifacts import verify_artifact
from comparo.core.artifacts.common import ArtifactCapture
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import ArtifactSourceCaptureState
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


def test_body_windows_use_result_coordinates_and_truthful_continuations(
    tmp_path: Path,
) -> None:
    root, artifact = _bundle(tmp_path)
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    reference = ArtifactBodyReference(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        exchange_id=exchange.exchange_id,
        message=ArtifactBodyMessage.RESPONSE,
    )

    first = asyncio.run(
        read_artifact_body_window(
            root,
            "artifact-0",
            reference,
            _limits(),
            offset=0,
            maximum_output_bytes=3,
        )
    )
    second = asyncio.run(
        read_artifact_body_window(
            root,
            "artifact-0",
            reference,
            _limits(),
            offset=first.next_offset or 0,
            maximum_output_bytes=3,
        )
    )

    assert first.content == b"rig"
    assert first.total_bytes == 5
    assert first.next_offset == 3
    assert second.content == b"ht"
    assert second.next_offset is None
    assert first.media_type == "text/plain"
    assert first.source_state == "complete"
    assert first.extent == "complete"


def test_body_window_rejects_unknown_coordinates_without_leaking_values(
    tmp_path: Path,
) -> None:
    root, _artifact = _bundle(tmp_path)
    reference = ArtifactBodyReference(
        execution_id="unknown",
        attempt_id="unknown",
        exchange_id="unknown",
        message=ArtifactBodyMessage.REQUEST,
    )

    with pytest.raises(ArtifactOperationError) as caught:
        asyncio.run(
            read_artifact_body_window(
                root,
                "artifact-0",
                reference,
                _limits(),
                offset=0,
                maximum_output_bytes=1,
            )
        )

    assert caught.value.code is ArtifactOperationErrorCode.BODY_NOT_FOUND
    assert "unknown" not in str(caught.value)


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


def test_body_reference_and_window_contracts_reject_ambiguous_state() -> None:
    reference = ArtifactBodyReference(
        execution_id="execution",
        attempt_id="attempt",
        exchange_id="exchange",
        message=ArtifactBodyMessage.REQUEST,
    )
    for reference_changes, error in (
        ({"execution_id": ""}, ValueError),
        ({"attempt_id": "x" * 65}, ValueError),
        ({"exchange_id": cast("str", object())}, ValueError),
        ({"message": cast("ArtifactBodyMessage", "request")}, TypeError),
    ):
        with pytest.raises(error):
            msgspec.structs.replace(reference, **reference_changes)

    window = ArtifactBodyWindow(
        reference=reference,
        source_state="complete",
        extent="complete",
        reason=None,
        media_type="application/json",
        total_bytes=10,
        offset=0,
        content=b"12345",
        next_offset=5,
        redaction_count=0,
    )
    for window_changes, error in (
        ({"reference": cast("ArtifactBodyReference", object())}, TypeError),
        ({"source_state": ""}, ValueError),
        ({"extent": cast("str", object())}, ValueError),
        ({"reason": ""}, ValueError),
        ({"media_type": cast("str", object())}, TypeError),
        ({"total_bytes": -1}, ValueError),
        ({"offset": -1}, ValueError),
        ({"content": cast("bytes", object())}, ValueError),
        ({"next_offset": 0}, ValueError),
        ({"next_offset": 10}, ValueError),
        ({"next_offset": 5, "total_bytes": None}, ValueError),
        ({"redaction_count": -1}, ValueError),
    ):
        with pytest.raises(error):
            msgspec.structs.replace(window, **window_changes)


def test_existing_and_project_root_validation_is_read_only_and_private(tmp_path: Path) -> None:
    project = tmp_path.resolve()
    selected = Path(".comparo/artifacts")
    initialized = initialize_project_artifact_root(project, selected)
    assert validate_project_artifact_root(project, selected) == initialized
    assert validate_existing_artifact_root(initialized) == initialized

    for root, relative, error in (
        (cast("Path", object()), selected, TypeError),
        (Path("relative"), selected, ArtifactOperationError),
        (project, Path("/absolute"), ArtifactOperationError),
        (project, Path("../escape"), ArtifactOperationError),
    ):
        with pytest.raises(error):
            validate_project_artifact_root(root, relative)

    with pytest.raises(TypeError, match="requires a Path"):
        validate_existing_artifact_root(cast("Path", object()))
    with pytest.raises(ArtifactOperationError) as relative_error:
        validate_existing_artifact_root(Path("relative"))
    assert relative_error.value.code is ArtifactOperationErrorCode.ROOT_UNSAFE
    with pytest.raises(ArtifactOperationError) as missing:
        validate_existing_artifact_root(tmp_path / "missing")
    assert missing.value.code is ArtifactOperationErrorCode.ROOT_UNAVAILABLE

    unsafe = tmp_path / "unsafe-read"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(ArtifactOperationError) as permissions:
        validate_existing_artifact_root(unsafe)
    assert permissions.value.code is ArtifactOperationErrorCode.ROOT_UNSAFE
    with pytest.raises(ArtifactOperationError) as project_permissions:
        validate_project_artifact_root(project, Path("unsafe-read"))
    assert project_permissions.value.code is ArtifactOperationErrorCode.ROOT_UNSAFE

    with pytest.raises(ArtifactOperationError) as absent_child:
        validate_project_artifact_root(project, Path("missing/child"))
    assert absent_child.value.code is ArtifactOperationErrorCode.ROOT_UNAVAILABLE


def test_body_read_rejects_invalid_limits_offsets_and_nested_coordinates(tmp_path: Path) -> None:
    root, artifact = _bundle(tmp_path)
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    reference = ArtifactBodyReference(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        exchange_id=exchange.exchange_id,
        message=ArtifactBodyMessage.RESPONSE,
    )
    calls = (
        (cast("ArtifactBodyReference", object()), 0, 1, TypeError),
        (reference, -1, 1, ArtifactOperationError),
        (reference, 0, 0, ArtifactOperationError),
        (reference, 100, 1, ArtifactOperationError),
    )
    for selected, offset, maximum, error in calls:
        with pytest.raises(error):
            asyncio.run(
                read_artifact_body_window(
                    root,
                    "artifact-0",
                    selected,
                    _limits(),
                    offset=offset,
                    maximum_output_bytes=maximum,
                )
            )

    for missing_reference in (
        msgspec.structs.replace(reference, attempt_id="missing"),
        msgspec.structs.replace(reference, exchange_id="missing"),
    ):
        with pytest.raises(ArtifactOperationError) as missing:
            asyncio.run(
                read_artifact_body_window(
                    root,
                    "artifact-0",
                    missing_reference,
                    _limits(),
                    offset=0,
                    maximum_output_bytes=1,
                )
            )
        assert missing.value.code is ArtifactOperationErrorCode.BODY_NOT_FOUND

    with pytest.raises(ArtifactOperationError) as unavailable:
        asyncio.run(
            read_artifact_body_window(
                tmp_path / "missing",
                "artifact-0",
                reference,
                _limits(),
                offset=0,
                maximum_output_bytes=1,
            )
        )
    assert unavailable.value.code is ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE


def test_existing_root_reports_a_resolve_race_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)

    def fail_resolve(_path: Path, *, strict: bool = False) -> Path:
        del strict
        raise OSError("value-free test failure")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(ArtifactOperationError) as unavailable:
        validate_existing_artifact_root(root)
    assert unavailable.value.code is ArtifactOperationErrorCode.ROOT_UNAVAILABLE


def test_omitted_and_absent_body_captures_are_distinguished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, artifact = _bundle(tmp_path)
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    omitted = ArtifactCapture(
        source_state=ArtifactSourceCaptureState.POLICY_OMITTED,
        extent=ArtifactCaptureExtent.OMITTED,
        source_byte_count=None,
        blob=None,
        reason=ArtifactOmissionReason.POLICY,
    )
    omitted_request = msgspec.structs.replace(exchange.request, body=omitted)
    omitted_exchange = msgspec.structs.replace(exchange, request=omitted_request)
    omitted_attempt = msgspec.structs.replace(attempt, exchanges=(omitted_exchange,))
    omitted_execution = msgspec.structs.replace(execution, attempts=(omitted_attempt,))
    omitted_artifact = msgspec.structs.replace(
        artifact,
        executions=(omitted_execution, *artifact.executions[1:]),
    )
    reference = ArtifactBodyReference(
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        exchange_id=exchange.exchange_id,
        message=ArtifactBodyMessage.REQUEST,
    )

    class Bundle:
        artifact = omitted_artifact

        def __enter__(self) -> Bundle:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(artifacts_module, "open_local_artifact_bundle", lambda *_a, **_k: Bundle())
    window = asyncio.run(
        read_artifact_body_window(
            root,
            "artifact-0",
            reference,
            _limits(),
            offset=0,
            maximum_output_bytes=1,
        )
    )
    assert window.content == b""
    assert window.reason == "policy"
    with pytest.raises(ArtifactOperationError) as invalid_offset:
        asyncio.run(
            read_artifact_body_window(
                root,
                "artifact-0",
                reference,
                _limits(),
                offset=1,
                maximum_output_bytes=1,
            )
        )
    assert invalid_offset.value.code is ArtifactOperationErrorCode.INVALID_LIMIT

    absent_request = msgspec.structs.replace(exchange.request, body=None)
    absent_exchange = msgspec.structs.replace(exchange, request=absent_request)
    absent_attempt = msgspec.structs.replace(attempt, exchanges=(absent_exchange,))
    absent_execution = msgspec.structs.replace(execution, attempts=(absent_attempt,))
    absent_artifact = msgspec.structs.replace(
        artifact,
        executions=(absent_execution, *artifact.executions[1:]),
    )
    with pytest.raises(ArtifactOperationError) as absent:
        artifacts_module._find_body_capture(absent_artifact, reference)
    assert absent.value.code is ArtifactOperationErrorCode.BODY_NOT_FOUND


def test_blob_window_skips_prefix_bounds_output_and_rejects_invalid_chunks() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"ab"
        yield b"cdef"
        yield b"gh"

    assert (
        asyncio.run(artifacts_module._read_blob_window(chunks(), offset=3, maximum_output_bytes=3))
        == b"def"
    )

    async def invalid() -> AsyncIterator[bytes]:
        yield cast("bytes", object())

    with pytest.raises(TypeError, match="chunks must be bytes"):
        asyncio.run(artifacts_module._read_blob_window(invalid(), offset=0, maximum_output_bytes=1))
