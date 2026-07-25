"""Bounded authenticated summaries for the local Report archive."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec

from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.adapters.artifacts import list_local_artifact_bundle_ids
from comparo.adapters.artifacts import open_local_artifact_bundle
from comparo.application.artifacts import ArtifactReadLimits
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount


class ReportArchiveErrorCode(StrEnum):
    """Closed whole-archive failure classes safe for frontend presentation."""

    INVALID_LIMIT = "invalid-limit"
    ARCHIVE_UNAVAILABLE = "archive-unavailable"
    BUDGET_EXCEEDED = "budget-exceeded"


class ReportArchiveError(RuntimeError):
    """Value-free whole-archive failure that never retains adapter exception text."""

    __slots__ = ("code",)

    def __init__(self, code: ReportArchiveErrorCode) -> None:
        """Retain only a closed application error code."""
        if type(code) is not ReportArchiveErrorCode:
            raise TypeError("report archive error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class ReportArchiveLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned directory and authenticated-results limits for one listing."""

    maximum_entries: int
    artifact: ArtifactReadLimits

    def __post_init__(self) -> None:
        """Reject non-positive enumeration budgets before opening the archive."""
        if type(self.maximum_entries) is not int or self.maximum_entries < 1:
            raise ReportArchiveError(ReportArchiveErrorCode.INVALID_LIMIT)
        if type(self.artifact) is not ArtifactReadLimits:
            raise TypeError("report archive artifact limits must be ArtifactReadLimits")


@final
class ReportArchiveEntry(msgspec.Struct, frozen=True, kw_only=True):
    """Small authenticated report-list row derived without reading blob bodies."""

    bundle_id: str
    run_id: str
    invocation_id: str
    created_unix_nanoseconds: int
    completion: str
    gate: str
    subjects: tuple[str, ...]
    environments: tuple[str, ...]
    executions: int
    comparisons: int
    assertions: int
    diffs: int
    blobs: int
    blob_bytes: int
    coverage_issues: int

    def __post_init__(self) -> None:
        """Require an immutable, scalar-only, non-negative listing product."""
        if any(
            type(value) is not str or not value
            for value in (
                self.bundle_id,
                self.run_id,
                self.invocation_id,
                self.completion,
                self.gate,
            )
        ):
            raise TypeError("report archive entry identifiers and outcomes must be text")
        if type(self.subjects) is not tuple or any(type(item) is not str for item in self.subjects):
            raise TypeError("report archive subjects must be an immutable text tuple")
        if type(self.environments) is not tuple or any(
            type(item) is not str for item in self.environments
        ):
            raise TypeError("report archive environments must be an immutable text tuple")
        if type(self.created_unix_nanoseconds) is not int:
            raise TypeError("report archive creation instant must be integer nanoseconds")
        counts = (
            self.executions,
            self.comparisons,
            self.assertions,
            self.diffs,
            self.blobs,
            self.blob_bytes,
            self.coverage_issues,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("report archive entry counts must be non-negative integers")


@final
class ReportArchiveEntryFailure(msgspec.Struct, frozen=True, kw_only=True):
    """One safe candidate identifier whose authenticated bundle could not be opened."""

    bundle_id: str
    code: ArtifactStoreErrorCode

    def __post_init__(self) -> None:
        """Retain only the safe candidate name and closed store failure category."""
        if type(self.bundle_id) is not str or not self.bundle_id:
            raise TypeError("report archive failed bundle ID must be non-empty text")
        if type(self.code) is not ArtifactStoreErrorCode:
            raise TypeError("report archive entry failure code must be typed")


@final
class ReportArchiveListing(msgspec.Struct, frozen=True, kw_only=True):
    """Authenticated valid entries plus every typed invalid-entry failure."""

    entries: tuple[ReportArchiveEntry, ...]
    failures: tuple[ReportArchiveEntryFailure, ...]

    def __post_init__(self) -> None:
        """Require canonical disjoint immutable collections."""
        if type(self.entries) is not tuple or any(
            type(item) is not ReportArchiveEntry for item in self.entries
        ):
            raise TypeError("report archive entries must be immutable and typed")
        if type(self.failures) is not tuple or any(
            type(item) is not ReportArchiveEntryFailure for item in self.failures
        ):
            raise TypeError("report archive failures must be immutable and typed")
        entry_ids = tuple(item.bundle_id for item in self.entries)
        failure_ids = tuple(item.bundle_id for item in self.failures)
        if len(set(entry_ids)) != len(entry_ids) or len(set(failure_ids)) != len(failure_ids):
            raise ValueError("report archive bundle identifiers must be unique")
        if set(entry_ids) & set(failure_ids):
            raise ValueError("report archive valid and invalid entries must be disjoint")


def list_report_archive(root: Path, limits: ReportArchiveLimits) -> ReportArchiveListing:
    """Authenticate every bounded bundle candidate and return compact safe summaries."""
    if type(limits) is not ReportArchiveLimits:
        raise ReportArchiveError(ReportArchiveErrorCode.INVALID_LIMIT)
    bundle_ids: tuple[str, ...] = ()
    archive_failure: ReportArchiveErrorCode | None = None
    try:
        bundle_ids = list_local_artifact_bundle_ids(
            root,
            maximum_entries=limits.maximum_entries,
        )
    except LocalArtifactStoreError as error:
        archive_failure = _archive_error_code(error.code)
    except Exception:  # noqa: BLE001 - external failures never cross the application boundary
        archive_failure = ReportArchiveErrorCode.ARCHIVE_UNAVAILABLE
    if archive_failure is not None:
        raise ReportArchiveError(archive_failure)

    artifact_limits = _artifact_limits(limits.artifact)
    entries: list[ReportArchiveEntry] = []
    failures: list[ReportArchiveEntryFailure] = []
    for bundle_id in bundle_ids:
        try:
            with open_local_artifact_bundle(root, bundle_id, limits=artifact_limits) as bundle:
                entries.append(_entry(bundle_id, bundle.artifact))
        except LocalArtifactStoreError as error:
            failures.append(ReportArchiveEntryFailure(bundle_id=bundle_id, code=error.code))
        except Exception:  # noqa: BLE001 - never retain unsafe exception text from one entry
            failures.append(
                ReportArchiveEntryFailure(
                    bundle_id=bundle_id,
                    code=ArtifactStoreErrorCode.IO_FAILED,
                )
            )
    entries.sort(key=lambda item: (-item.created_unix_nanoseconds, item.bundle_id))
    return ReportArchiveListing(entries=tuple(entries), failures=tuple(failures))


def _entry(bundle_id: str, artifact: RunArtifact) -> ReportArchiveEntry:
    """Project one authenticated safe artifact into a bounded archive row."""
    return ReportArchiveEntry(
        bundle_id=bundle_id,
        run_id=artifact.metadata.run_id,
        invocation_id=artifact.metadata.invocation_id,
        created_unix_nanoseconds=artifact.metadata.created_unix_nanoseconds,
        completion=artifact.completion.kind.value,
        gate=artifact.gate.verdict.value,
        subjects=tuple(sorted({item.subject.resource_id for item in artifact.executions})),
        environments=tuple(sorted({item.environment.resource_id for item in artifact.executions})),
        executions=len(artifact.executions),
        comparisons=len(artifact.comparisons),
        assertions=sum(len(item.assertions) for item in artifact.executions),
        diffs=sum(len(item.diffs) for item in artifact.comparisons),
        blobs=len(artifact.blobs),
        blob_bytes=sum(item.byte_count for item in artifact.blobs),
        coverage_issues=len(artifact.coverage_issues),
    )


def _archive_error_code(code: ArtifactStoreErrorCode) -> ReportArchiveErrorCode:
    """Collapse store implementation detail into one whole-archive error vocabulary."""
    if code is ArtifactStoreErrorCode.BUDGET_EXCEEDED:
        return ReportArchiveErrorCode.BUDGET_EXCEEDED
    return ReportArchiveErrorCode.ARCHIVE_UNAVAILABLE


def _artifact_limits(value: ArtifactReadLimits) -> ArtifactLimits:
    """Translate caller-owned read ceilings without weakening artifact validation."""
    return ArtifactLimits(
        maximum_blobs=PositiveCount(value=value.maximum_blobs),
        maximum_blob_bytes=ByteCount(value=value.maximum_blob_bytes),
        maximum_total_blob_bytes=ByteCount(value=value.maximum_total_blob_bytes),
        maximum_manifest_bytes=ByteCount(value=value.maximum_manifest_bytes),
        maximum_report_bytes=ByteCount(value=value.maximum_report_bytes),
        sanitization=SanitizationLimits(
            maximum_patterns=PositiveCount(value=value.maximum_redaction_patterns),
            maximum_pattern_bytes=ByteCount(value=value.maximum_redaction_pattern_bytes),
            maximum_value_bytes=ByteCount(value=value.maximum_redaction_value_bytes),
            maximum_value_nodes=PositiveCount(value=value.maximum_redaction_value_nodes),
        ),
    )


__all__ = (
    "ReportArchiveEntry",
    "ReportArchiveEntryFailure",
    "ReportArchiveError",
    "ReportArchiveErrorCode",
    "ReportArchiveLimits",
    "ReportArchiveListing",
    "list_report_archive",
)
