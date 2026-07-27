"""Tests for bounded authenticated Report archive listings."""

from __future__ import annotations

import asyncio
import traceback
from base64 import urlsafe_b64encode
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import msgspec
import pytest

from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.application import report_archive as archive_module
from comparo.application import report_retention as retention_module
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.report_archive import ReportArchiveCi
from comparo.application.report_archive import ReportArchiveEntry
from comparo.application.report_archive import ReportArchiveEntryFailure
from comparo.application.report_archive import ReportArchiveError
from comparo.application.report_archive import ReportArchiveErrorCode
from comparo.application.report_archive import ReportArchiveLimits
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.report_archive import ReportArchivePage
from comparo.application.report_archive import ReportArchiveQuery
from comparo.application.report_archive import ReportArchiveResource
from comparo.application.report_archive import ReportArchiveVcs
from comparo.application.report_archive import list_report_archive
from comparo.application.report_archive import query_report_archive
from comparo.application.report_retention import ReportRetentionCandidate
from comparo.application.report_retention import ReportRetentionError
from comparo.application.report_retention import ReportRetentionErrorCode
from comparo.application.report_retention import ReportRetentionPlan
from comparo.application.report_retention import ReportRetentionPolicy
from comparo.application.report_retention import apply_report_retention
from comparo.application.report_retention import plan_report_retention
from comparo.application.report_retention import restore_report
from comparo.core.artifacts import ArtifactCiProvenance
from comparo.core.artifacts import ArtifactDefinition
from comparo.core.artifacts import ArtifactRunAnnotations
from comparo.core.artifacts import ArtifactVcsProvenance
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from tests.adapters.artifacts.test_local_store import _secure_root
from tests.adapters.artifacts.test_local_store import _stored_artifact
from tests.application.test_artifacts import _limits
from tests.core.artifacts.test_capture_compiler import _limits as domain_limits


def _persist(root: Path, artifact: RunArtifact, blobs: object) -> None:
    sink = LocalArtifactBundleSink(root)
    outcome = asyncio.run(sink.persist(artifact, blobs, domain_limits()))  # type: ignore[arg-type]
    sink.close()
    assert outcome.bundle_id == artifact.metadata.artifact_id


def _renamed(artifact: RunArtifact, bundle_id: str, created: int) -> RunArtifact:
    metadata = msgspec.structs.replace(
        artifact.metadata,
        artifact_id=bundle_id,
        created_unix_nanoseconds=created,
    )
    return msgspec.structs.replace(artifact, metadata=metadata)


def _archive_entry(**changes: object) -> ReportArchiveEntry:
    entry = ReportArchiveEntry(
        bundle_id="bundle",
        run_id="run",
        invocation_id="invocation",
        created_unix_nanoseconds=0,
        completion="complete",
        gate="passed",
        subjects=(),
        environments=(),
        executions=0,
        comparisons=0,
        assertions=0,
        diffs=0,
        blobs=0,
        blob_bytes=0,
        coverage_issues=0,
    )
    return msgspec.structs.replace(entry, **changes)


def test_archive_lists_authenticated_summaries_newest_first_without_blob_reads(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path / "archive")
    artifact, blobs = asyncio.run(_stored_artifact())
    older = _renamed(artifact, "older", 10)
    newer = _renamed(artifact, "newer", 20)
    _persist(root, older, blobs)
    _persist(root, newer, blobs)
    for blob in newer.blobs:
        (root / "newer" / "blobs" / blob.sha256).unlink()

    listing = list_report_archive(
        root,
        ReportArchiveLimits(maximum_entries=2, artifact=_limits()),
    )

    assert tuple(item.bundle_id for item in listing.entries) == ("newer", "older")
    assert listing.failures == ()
    entry = listing.entries[0]
    assert entry.run_id == newer.metadata.run_id
    assert entry.invocation_id == newer.metadata.invocation_id
    assert entry.created_unix_nanoseconds == 20
    assert entry.completion == newer.completion.kind.value
    assert entry.gate == newer.gate.verdict.value
    assert entry.subjects == tuple(
        ReportArchiveResource(namespace=namespace, resource_id=resource_id)
        for namespace, resource_id in sorted(
            {(item.subject.namespace, item.subject.resource_id) for item in newer.executions}
        )
    )
    assert entry.environments == tuple(
        ReportArchiveResource(namespace=namespace, resource_id=resource_id)
        for namespace, resource_id in sorted(
            {
                (item.environment.namespace, item.environment.resource_id)
                for item in newer.executions
            }
        )
    )
    assert entry.executions == len(newer.executions)
    assert entry.comparisons == len(newer.comparisons)
    assert entry.assertions == sum(len(item.assertions) for item in newer.executions)
    assert entry.diffs == sum(len(item.diffs) for item in newer.comparisons)
    assert entry.blobs == len(newer.blobs)
    assert entry.blob_bytes == sum(item.byte_count for item in newer.blobs)
    assert entry.coverage_issues == len(newer.coverage_issues)


def test_archive_retains_each_typed_invalid_entry_and_keeps_valid_ones(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    artifact, blobs = asyncio.run(_stored_artifact())
    valid = _renamed(artifact, "valid", 1)
    corrupt = _renamed(artifact, "corrupt", 2)
    _persist(root, valid, blobs)
    _persist(root, corrupt, blobs)
    (root / "corrupt" / "results.json").write_bytes(b"attacker-secret")
    (root / "missing-metadata").mkdir(mode=0o700)

    listing = list_report_archive(
        root,
        ReportArchiveLimits(maximum_entries=3, artifact=_limits()),
    )

    assert tuple(item.bundle_id for item in listing.entries) == ("valid",)
    assert tuple((item.bundle_id, item.code) for item in listing.failures) == (
        ("corrupt", ArtifactStoreErrorCode.DIGEST_MISMATCH),
        ("missing-metadata", ArtifactStoreErrorCode.IO_FAILED),
    )
    rendered = "".join(str(item) for item in listing.failures)
    assert "attacker-secret" not in rendered


def test_archive_query_pages_stably_and_filters_only_safe_metadata(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    artifact, blobs = asyncio.run(_stored_artifact())
    for bundle_id, created in (("oldest", 10), ("middle", 20), ("newest", 30)):
        _persist(root, _renamed(artifact, bundle_id, created), blobs)
    limits = ReportArchiveLimits(maximum_entries=3, artifact=_limits())

    first = query_report_archive(root, limits, ReportArchiveQuery(page_size=2))
    second = query_report_archive(
        root,
        limits,
        ReportArchiveQuery(page_size=2, cursor=first.next_cursor),
    )
    subject = first.entries[0].subjects[0]
    filtered = query_report_archive(
        root,
        limits,
        ReportArchiveQuery(
            page_size=3,
            text=subject.resource_id,
            subject=subject,
            completion=artifact.completion.kind.value,
        ),
    )

    assert tuple(item.bundle_id for item in first.entries) == ("newest", "middle")
    assert first.matched_entries == 3
    assert first.next_cursor is not None
    assert tuple(item.bundle_id for item in second.entries) == ("oldest",)
    assert second.next_cursor is None
    assert tuple(item.bundle_id for item in filtered.entries) == (
        "newest",
        "middle",
        "oldest",
    )


def test_archive_projects_optional_sanitized_run_vcs_and_ci_metadata(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    artifact, blobs = asyncio.run(_stored_artifact())
    annotated = msgspec.structs.replace(
        artifact,
        annotations=ArtifactRunAnnotations(
            title="Release candidate",
            notes="Reviewed by release engineering",
            tags=("release", "candidate"),
            vcs=ArtifactVcsProvenance(
                provider="git",
                repository="org/repo",
                revision="abc123",
                branch="main",
            ),
            ci=ArtifactCiProvenance(
                provider="github",
                run_id="42",
                job="compare",
                url="https://ci.example.test/runs/42",
            ),
        ),
    )
    _persist(root, annotated, blobs)

    entry = list_report_archive(
        root,
        ReportArchiveLimits(maximum_entries=1, artifact=_limits()),
    ).entries[0]

    assert entry.title == "Release candidate"
    assert entry.tags == ("release", "candidate")
    assert entry.vcs is not None
    assert entry.vcs.revision == "abc123"
    assert entry.ci is not None
    assert entry.ci.run_id == "42"


def test_archive_query_rejects_untrusted_or_oversized_continuations(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    limits = ReportArchiveLimits(maximum_entries=2, artifact=_limits())

    with pytest.raises(ReportArchiveError) as cursor:
        query_report_archive(
            root,
            limits,
            ReportArchiveQuery(page_size=1, cursor="not-a-cursor"),
        )
    assert cursor.value.code is ReportArchiveErrorCode.INVALID_CURSOR
    assert cursor.value.__cause__ is None
    assert cursor.value.__context__ is None

    with pytest.raises(ReportArchiveError) as page:
        query_report_archive(root, limits, ReportArchiveQuery(page_size=3))
    assert page.value.code is ReportArchiveErrorCode.INVALID_LIMIT


def test_retention_is_reviewed_recoverable_and_keeps_newest_reports(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    artifact, blobs = asyncio.run(_stored_artifact())
    _persist(root, _renamed(artifact, "older", 10), blobs)
    _persist(root, _renamed(artifact, "newer", 20), blobs)
    archive_limits = ReportArchiveLimits(maximum_entries=2, artifact=_limits())
    plan = plan_report_retention(
        root,
        archive_limits,
        ReportRetentionPolicy(keep_newest=1, maximum_actions=1),
    )

    assert tuple(item.bundle_id for item in plan.candidates) == ("older",)
    outcome = apply_report_retention(root, _limits(), plan)
    assert outcome.trashed_bundle_ids == ("older",)
    assert outcome.failures == ()
    assert tuple(item.bundle_id for item in list_report_archive(root, archive_limits).entries) == (
        "newer",
    )
    assert (root / ".trash" / "older").is_dir()

    restored = restore_report(root, "older", _limits())
    assert restored.metadata.artifact_id == "older"
    assert tuple(item.bundle_id for item in list_report_archive(root, archive_limits).entries) == (
        "newer",
        "older",
    )


def test_retention_refuses_to_silently_truncate_its_action_plan(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    artifact, blobs = asyncio.run(_stored_artifact())
    _persist(root, _renamed(artifact, "older", 10), blobs)
    _persist(root, _renamed(artifact, "newer", 20), blobs)

    with pytest.raises(ReportRetentionError) as caught:
        plan_report_retention(
            root,
            ReportArchiveLimits(maximum_entries=2, artifact=_limits()),
            ReportRetentionPolicy(keep_newest=0, maximum_actions=1),
        )

    assert caught.value.code is ReportRetentionErrorCode.BUDGET_EXCEEDED


def test_archive_whole_listing_failures_are_closed_and_value_free(tmp_path: Path) -> None:
    missing = tmp_path / "secret-project" / "archive"
    with pytest.raises(ReportArchiveError) as unavailable:
        list_report_archive(
            missing,
            ReportArchiveLimits(maximum_entries=1, artifact=_limits()),
        )
    assert unavailable.value.code is ReportArchiveErrorCode.ARCHIVE_UNAVAILABLE
    rendered = "".join(traceback.format_exception(unavailable.value))
    assert "secret-project" not in rendered
    assert unavailable.value.__cause__ is None
    assert unavailable.value.__context__ is None

    root = _secure_root(tmp_path / "bounded")
    (root / "one").mkdir(mode=0o700)
    (root / "two").mkdir(mode=0o700)
    with pytest.raises(ReportArchiveError) as exceeded:
        list_report_archive(
            root,
            ReportArchiveLimits(maximum_entries=1, artifact=_limits()),
        )
    assert exceeded.value.code is ReportArchiveErrorCode.BUDGET_EXCEEDED


def test_archive_maps_unexpected_boundary_and_entry_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "archive")

    def fail_list(_root: Path, *, maximum_entries: int) -> tuple[str, ...]:
        del maximum_entries
        raise RuntimeError("private-list-detail")

    monkeypatch.setattr(archive_module, "list_local_artifact_bundle_ids", fail_list)
    with pytest.raises(ReportArchiveError) as listing:
        list_report_archive(
            root,
            ReportArchiveLimits(maximum_entries=1, artifact=_limits()),
        )
    assert listing.value.code is ReportArchiveErrorCode.ARCHIVE_UNAVAILABLE
    assert "private-list-detail" not in "".join(traceback.format_exception(listing.value))
    monkeypatch.undo()

    (root / "candidate").mkdir(mode=0o700)

    def fail_open(_root: Path, _bundle_id: str, *, limits: object) -> object:
        del limits
        raise RuntimeError("private-entry-detail")

    monkeypatch.setattr(archive_module, "open_local_artifact_bundle", fail_open)
    result = list_report_archive(
        root,
        ReportArchiveLimits(maximum_entries=1, artifact=_limits()),
    )
    assert result.entries == ()
    assert result.failures == (
        ReportArchiveEntryFailure(
            bundle_id="candidate",
            code=ArtifactStoreErrorCode.IO_FAILED,
        ),
    )


def test_archive_contracts_reject_untyped_and_contradictory_values() -> None:
    with pytest.raises(TypeError, match="error code"):
        ReportArchiveError(cast("ReportArchiveErrorCode", object()))
    with pytest.raises(ReportArchiveError) as maximum:
        ReportArchiveLimits(maximum_entries=0, artifact=_limits())
    assert maximum.value.code is ReportArchiveErrorCode.INVALID_LIMIT
    with pytest.raises(TypeError, match="artifact limits"):
        ReportArchiveLimits(maximum_entries=1, artifact=object())  # type: ignore[arg-type]
    with pytest.raises(ReportArchiveError) as untyped:
        list_report_archive(Path.cwd(), cast("ReportArchiveLimits", object()))
    assert untyped.value.code is ReportArchiveErrorCode.INVALID_LIMIT

    entry = _archive_entry()
    failure = ReportArchiveEntryFailure(
        bundle_id="other",
        code=ArtifactStoreErrorCode.IO_FAILED,
    )
    with pytest.raises(ValueError, match="unique"):
        ReportArchiveListing(entries=(entry, entry), failures=(failure,))
    with pytest.raises(ValueError, match="disjoint"):
        ReportArchiveListing(
            entries=(entry,),
            failures=(
                ReportArchiveEntryFailure(
                    bundle_id="bundle",
                    code=ArtifactStoreErrorCode.IO_FAILED,
                ),
            ),
        )

    for changes, error in (
        ({"bundle_id": ""}, TypeError),
        ({"run_id": cast("str", 1)}, TypeError),
        ({"subjects": cast("tuple[ReportArchiveResource, ...]", [])}, TypeError),
        ({"subjects": (cast("ReportArchiveResource", 1),)}, TypeError),
        ({"environments": cast("tuple[ReportArchiveResource, ...]", [])}, TypeError),
        ({"environments": (cast("ReportArchiveResource", 1),)}, TypeError),
        ({"created_unix_nanoseconds": cast("int", "now")}, TypeError),
        ({"executions": -1}, ValueError),
        ({"executions": cast("int", True)}, ValueError),
    ):
        with pytest.raises(error):
            msgspec.structs.replace(entry, **changes)

    with pytest.raises(TypeError, match="bundle ID"):
        ReportArchiveEntryFailure(bundle_id="", code=ArtifactStoreErrorCode.IO_FAILED)
    with pytest.raises(TypeError, match="failure code"):
        ReportArchiveEntryFailure(
            bundle_id="bundle",
            code=cast("ArtifactStoreErrorCode", object()),
        )
    with pytest.raises(TypeError, match="entries"):
        ReportArchiveListing(entries=cast("tuple[ReportArchiveEntry, ...]", []), failures=())
    with pytest.raises(TypeError, match="entries"):
        ReportArchiveListing(entries=(cast("ReportArchiveEntry", object()),), failures=())
    with pytest.raises(TypeError, match="failures"):
        ReportArchiveListing(entries=(), failures=cast("tuple[ReportArchiveEntryFailure, ...]", []))
    with pytest.raises(TypeError, match="failures"):
        ReportArchiveListing(
            entries=(),
            failures=(cast("ReportArchiveEntryFailure", object()),),
        )
    with pytest.raises(ValueError, match="unique"):
        ReportArchiveListing(entries=(), failures=(failure, failure))


def test_archive_optional_contracts_queries_and_pages_are_strictly_typed() -> None:
    subject = ReportArchiveResource(namespace="default", resource_id="request")
    entry = _archive_entry(subjects=(subject,))
    for changes, error in (
        ({"duration_nanoseconds": -1}, ValueError),
        ({"duration_nanoseconds": cast("int", True)}, ValueError),
        ({"execution_profile": cast("ReportArchiveResource", object())}, TypeError),
        ({"title": cast("str", object())}, TypeError),
        ({"tags": cast("tuple[str, ...]", ["tag"])}, TypeError),
        ({"tags": (cast("str", object()),)}, TypeError),
        ({"vcs": cast("ReportArchiveVcs", object())}, TypeError),
        ({"ci": cast("ReportArchiveCi", object())}, TypeError),
    ):
        with pytest.raises(error):
            msgspec.structs.replace(entry, **changes)

    with pytest.raises(ReportArchiveError) as page_size:
        ReportArchiveQuery(page_size=0)
    assert page_size.value.code is ReportArchiveErrorCode.INVALID_LIMIT
    valid_query = ReportArchiveQuery(page_size=1)
    for changes, error in (
        ({"cursor": ""}, ValueError),
        ({"cursor": "x" * 1025}, ValueError),
        ({"text": cast("str", object())}, ValueError),
        ({"text": "x" * 257}, ValueError),
        ({"completion": "x" * 65}, ValueError),
        ({"gate": "x" * 65}, ValueError),
        ({"subject": cast("ReportArchiveResource", object())}, TypeError),
        ({"environment": cast("ReportArchiveResource", object())}, TypeError),
    ):
        with pytest.raises(error):
            msgspec.structs.replace(valid_query, **changes)

    valid_page = ReportArchivePage(
        entries=(entry,),
        failures=(),
        matched_entries=1,
        next_cursor=None,
    )
    with pytest.raises(ValueError, match="matched count"):
        msgspec.structs.replace(valid_page, matched_entries=0)
    with pytest.raises(ValueError, match="matched count"):
        msgspec.structs.replace(valid_page, matched_entries=cast("int", True))
    for cursor in ("", cast("str", object())):
        with pytest.raises(TypeError, match="continuation"):
            msgspec.structs.replace(valid_page, next_cursor=cursor)


def test_archive_query_rejects_untyped_inputs_and_all_filter_mismatches(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    limits = ReportArchiveLimits(maximum_entries=2, artifact=_limits())
    with pytest.raises(ReportArchiveError) as invalid_limits:
        query_report_archive(
            root,
            cast("ReportArchiveLimits", object()),
            ReportArchiveQuery(page_size=1),
        )
    assert invalid_limits.value.code is ReportArchiveErrorCode.INVALID_LIMIT
    with pytest.raises(ReportArchiveError) as invalid_query:
        query_report_archive(root, limits, cast("ReportArchiveQuery", object()))
    assert invalid_query.value.code is ReportArchiveErrorCode.INVALID_LIMIT

    subject = ReportArchiveResource(namespace="default", resource_id="request")
    environment = ReportArchiveResource(namespace="default", resource_id="staging")
    profile = ReportArchiveResource(namespace="default", resource_id="nightly")
    entry = _archive_entry(
        completion="complete",
        gate="passed",
        subjects=(subject,),
        environments=(environment,),
        title="Release candidate",
        tags=("smoke",),
        execution_profile=profile,
    )
    assert not archive_module._matches(
        entry,
        ReportArchiveQuery(page_size=1, completion="partial"),
    )
    assert not archive_module._matches(entry, ReportArchiveQuery(page_size=1, gate="failed"))
    assert not archive_module._matches(
        entry,
        ReportArchiveQuery(
            page_size=1,
            subject=ReportArchiveResource(namespace="default", resource_id="other"),
        ),
    )
    assert not archive_module._matches(
        entry,
        ReportArchiveQuery(
            page_size=1,
            environment=ReportArchiveResource(namespace="default", resource_id="prod"),
        ),
    )
    assert archive_module._matches(entry, ReportArchiveQuery(page_size=1))
    assert archive_module._matches(entry, ReportArchiveQuery(page_size=1, text="release"))
    assert archive_module._matches(entry, ReportArchiveQuery(page_size=1, text="nightly"))
    assert archive_module._matches(entry, ReportArchiveQuery(page_size=1, text="smoke"))
    assert not archive_module._matches(entry, ReportArchiveQuery(page_size=1, text="absent"))


def test_archive_cursor_resource_and_duration_helpers_cover_safe_optional_shapes() -> None:
    definition = ArtifactDefinition(
        namespace="default",
        kind="ExecutionProfile",
        resource_id="nightly",
        fingerprint="f" * 64,
    )
    assert archive_module._archive_resource(definition) == ReportArchiveResource(
        namespace="default",
        resource_id="nightly",
    )

    negative_payload = msgspec.json.encode((-1, "bundle"))
    negative_cursor = urlsafe_b64encode(negative_payload).decode("ascii").rstrip("=")
    with pytest.raises(ReportArchiveError) as cursor:
        archive_module._decode_cursor(negative_cursor)
    assert cursor.value.code is ReportArchiveErrorCode.INVALID_CURSOR
    assert cursor.value.__cause__ is None
    assert cursor.value.__context__ is None

    incomplete = cast(
        "RunArtifact",
        SimpleNamespace(
            metadata=SimpleNamespace(created_unix_nanoseconds=10),
            completion=SimpleNamespace(instant_unix_nanoseconds=None),
        ),
    )
    backwards = cast(
        "RunArtifact",
        SimpleNamespace(
            metadata=SimpleNamespace(created_unix_nanoseconds=10),
            completion=SimpleNamespace(instant_unix_nanoseconds=9),
        ),
    )
    complete = cast(
        "RunArtifact",
        SimpleNamespace(
            metadata=SimpleNamespace(created_unix_nanoseconds=10),
            completion=SimpleNamespace(instant_unix_nanoseconds=15),
        ),
    )
    assert archive_module._duration(incomplete) is None
    assert archive_module._duration(backwards) is None
    assert archive_module._duration(complete) == 5


def test_archive_projects_execution_profile_and_filters_it_by_text(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "archive")
    artifact, blobs = asyncio.run(_stored_artifact())
    profile = ArtifactDefinition(
        namespace="default",
        kind="ExecutionProfile",
        resource_id="nightly",
        fingerprint="f" * 64,
    )
    profiled = msgspec.structs.replace(
        artifact,
        provenance=msgspec.structs.replace(artifact.provenance, execution_profile=profile),
        resources=tuple(
            sorted(
                (*artifact.resources, profile),
                key=lambda item: (item.namespace, item.kind, item.resource_id),
            )
        ),
    )
    _persist(root, profiled, blobs)

    page = query_report_archive(
        root,
        ReportArchiveLimits(maximum_entries=1, artifact=_limits()),
        ReportArchiveQuery(page_size=1, text="nightly"),
    )

    assert page.entries[0].execution_profile == ReportArchiveResource(
        namespace="default",
        resource_id="nightly",
    )


def test_retention_contracts_and_selection_filters_are_closed(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="error code"):
        ReportRetentionError(cast("ReportRetentionErrorCode", object()))
    valid_policy = ReportRetentionPolicy(keep_newest=0, maximum_actions=1)
    for changes in (
        {"keep_newest": -1},
        {"keep_newest": cast("int", True)},
        {"maximum_actions": 0},
        {"maximum_actions": cast("int", True)},
        {"created_before_unix_nanoseconds": -1},
        {"created_before_unix_nanoseconds": cast("int", True)},
        {"preserve_gate_verdicts": cast("tuple[str, ...]", ["failed"])},
        {"preserve_gate_verdicts": ("",)},
        {"preserve_gate_verdicts": ("x" * 65,)},
        {"preserve_gate_verdicts": ("failed", "failed")},
    ):
        with pytest.raises(ReportRetentionError) as caught:
            msgspec.structs.replace(valid_policy, **changes)
        assert caught.value.code is ReportRetentionErrorCode.INVALID_POLICY

    valid_plan = ReportRetentionPlan(candidates=(), skipped_failures=(), fingerprint="0" * 64)
    with pytest.raises(TypeError, match="candidates"):
        msgspec.structs.replace(
            valid_plan,
            candidates=cast("tuple[ReportRetentionCandidate, ...]", []),
        )
    with pytest.raises(TypeError, match="candidates"):
        msgspec.structs.replace(
            valid_plan,
            candidates=(cast("ReportRetentionCandidate", object()),),
        )
    with pytest.raises(TypeError, match="failures"):
        msgspec.structs.replace(valid_plan, skipped_failures=cast("Any", []))
    with pytest.raises(TypeError, match="failures"):
        msgspec.structs.replace(
            valid_plan,
            skipped_failures=(cast("ReportArchiveEntryFailure", object()),),
        )
    for fingerprint in ("0" * 63, "G" * 64):
        with pytest.raises(ValueError, match="SHA-256"):
            msgspec.structs.replace(valid_plan, fingerprint=fingerprint)

    root = _secure_root(tmp_path / "archive")
    artifact, blobs = asyncio.run(_stored_artifact())
    _persist(root, _renamed(artifact, "oldest", 10), blobs)
    _persist(root, _renamed(artifact, "middle", 20), blobs)
    _persist(root, _renamed(artifact, "newest", 30), blobs)
    limits = ReportArchiveLimits(maximum_entries=3, artifact=_limits())
    plan = plan_report_retention(
        root,
        limits,
        ReportRetentionPolicy(
            keep_newest=1,
            maximum_actions=2,
            created_before_unix_nanoseconds=15,
        ),
    )
    assert tuple(item.bundle_id for item in plan.candidates) == ("oldest",)
    preserved = plan_report_retention(
        root,
        limits,
        ReportRetentionPolicy(
            keep_newest=1,
            maximum_actions=2,
            preserve_gate_verdicts=(artifact.gate.verdict.value,),
        ),
    )
    assert preserved.candidates == ()
    with pytest.raises(ReportRetentionError) as untyped:
        plan_report_retention(root, limits, cast("ReportRetentionPolicy", object()))
    assert untyped.value.code is ReportRetentionErrorCode.INVALID_POLICY


def test_retention_apply_detects_stale_plans_and_reports_typed_partial_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = ReportRetentionCandidate(
        bundle_id="bundle",
        run_id="run",
        created_unix_nanoseconds=1,
    )
    valid = ReportRetentionPlan(
        candidates=(candidate,),
        skipped_failures=(),
        fingerprint=retention_module._fingerprint((candidate,)),
    )
    tampered = msgspec.structs.replace(valid, fingerprint="0" * 64)
    with pytest.raises(ReportRetentionError) as mismatch:
        apply_report_retention(tmp_path, _limits(), tampered)
    assert mismatch.value.code is ReportRetentionErrorCode.PLAN_MISMATCH
    with pytest.raises(ReportRetentionError) as untyped:
        apply_report_retention(tmp_path, _limits(), cast("ReportRetentionPlan", object()))
    assert untyped.value.code is ReportRetentionErrorCode.PLAN_MISMATCH

    artifact, _blobs = asyncio.run(_stored_artifact())
    stale = msgspec.structs.replace(
        artifact,
        metadata=msgspec.structs.replace(
            artifact.metadata,
            run_id="other-run",
            created_unix_nanoseconds=1,
        ),
    )
    monkeypatch.setattr(retention_module, "verify_artifact", lambda *_args: stale)
    with pytest.raises(ReportRetentionError) as stale_plan:
        apply_report_retention(tmp_path, _limits(), valid)
    assert stale_plan.value.code is ReportRetentionErrorCode.PLAN_MISMATCH

    def artifact_failure(*_args: object) -> RunArtifact:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)

    monkeypatch.setattr(retention_module, "verify_artifact", artifact_failure)
    outcome = apply_report_retention(tmp_path, _limits(), valid)
    assert outcome.failures[0].code is ArtifactStoreErrorCode.IO_FAILED

    def verification_store_failure(*_args: object) -> RunArtifact:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)

    monkeypatch.setattr(retention_module, "verify_artifact", verification_store_failure)
    outcome = apply_report_retention(tmp_path, _limits(), valid)
    assert outcome.failures[0].code is ArtifactStoreErrorCode.DIGEST_MISMATCH

    matching = msgspec.structs.replace(
        artifact,
        metadata=msgspec.structs.replace(
            artifact.metadata,
            run_id="run",
            created_unix_nanoseconds=1,
        ),
    )
    monkeypatch.setattr(retention_module, "verify_artifact", lambda *_args: matching)

    def store_failure(*_args: object) -> None:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)

    monkeypatch.setattr(retention_module, "trash_local_artifact_bundle", store_failure)
    outcome = apply_report_retention(tmp_path, _limits(), valid)
    assert outcome.failures[0].code is ArtifactStoreErrorCode.ROOT_UNSAFE


def test_retention_preflights_every_candidate_before_moving_any_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ReportRetentionCandidate(
        bundle_id="first",
        run_id="run-first",
        created_unix_nanoseconds=1,
    )
    second = ReportRetentionCandidate(
        bundle_id="second",
        run_id="run-second",
        created_unix_nanoseconds=2,
    )
    plan = ReportRetentionPlan(
        candidates=(first, second),
        skipped_failures=(),
        fingerprint=retention_module._fingerprint((first, second)),
    )
    artifact, _blobs = asyncio.run(_stored_artifact())

    def verify(_root: Path, bundle_id: str, _limits: ArtifactReadLimits) -> RunArtifact:
        candidate = first if bundle_id == "first" else second
        run_id = candidate.run_id if bundle_id == "first" else "changed"
        return msgspec.structs.replace(
            artifact,
            metadata=msgspec.structs.replace(
                artifact.metadata,
                run_id=run_id,
                created_unix_nanoseconds=candidate.created_unix_nanoseconds,
            ),
        )

    moved: list[str] = []
    monkeypatch.setattr(retention_module, "verify_artifact", verify)
    monkeypatch.setattr(
        retention_module,
        "trash_local_artifact_bundle",
        lambda _root, bundle_id: moved.append(bundle_id),
    )

    with pytest.raises(ReportRetentionError) as mismatch:
        apply_report_retention(tmp_path, _limits(), plan)

    assert mismatch.value.code is ReportRetentionErrorCode.PLAN_MISMATCH
    assert moved == []


def test_restore_rolls_back_after_failed_authentication_even_if_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        retention_module,
        "restore_local_artifact_bundle",
        lambda *_args: calls.append("restore"),
    )

    def verification_failure(*_args: object) -> RunArtifact:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)

    monkeypatch.setattr(retention_module, "verify_artifact", verification_failure)
    monkeypatch.setattr(
        retention_module,
        "trash_local_artifact_bundle",
        lambda *_args: calls.append("trash"),
    )
    with pytest.raises(ArtifactOperationError):
        restore_report(tmp_path, "bundle", _limits())
    assert calls == ["restore", "trash"]

    def rollback_failure(*_args: object) -> None:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)

    calls.clear()
    monkeypatch.setattr(retention_module, "trash_local_artifact_bundle", rollback_failure)
    with pytest.raises(ArtifactOperationError):
        restore_report(tmp_path, "bundle", _limits())
    assert calls == ["restore"]
