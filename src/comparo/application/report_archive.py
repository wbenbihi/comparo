"""Bounded authenticated summaries for the local Report archive."""

from __future__ import annotations

from base64 import urlsafe_b64decode
from base64 import urlsafe_b64encode
from binascii import Error as Base64Error
from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec

from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.adapters.artifacts import list_local_artifact_bundle_ids
from comparo.adapters.artifacts import open_local_artifact_bundle
from comparo.application.artifacts import ArtifactReadLimits
from comparo.core.artifacts.common import require_stable_token
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount


class ReportArchiveErrorCode(StrEnum):
    """Closed whole-archive failure classes safe for frontend presentation."""

    INVALID_LIMIT = "invalid-limit"
    INVALID_CURSOR = "invalid-cursor"
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
class ReportArchiveResource(msgspec.Struct, frozen=True, kw_only=True):
    """One namespace-preserving safe definition identity for archive summaries."""

    namespace: str
    resource_id: str

    def __post_init__(self) -> None:
        """Require route- and terminal-safe logical identity tokens."""
        require_stable_token(self.namespace, label="report resource namespace", maximum=63)
        require_stable_token(self.resource_id, label="report resource ID", maximum=255)


@final
class ReportArchiveEntry(msgspec.Struct, frozen=True, kw_only=True):
    """Small authenticated report-list row derived without reading blob bodies."""

    bundle_id: str
    run_id: str
    invocation_id: str
    created_unix_nanoseconds: int
    completion: str
    gate: str
    subjects: tuple[ReportArchiveResource, ...]
    environments: tuple[ReportArchiveResource, ...]
    executions: int
    comparisons: int
    assertions: int
    diffs: int
    blobs: int
    blob_bytes: int
    coverage_issues: int
    duration_nanoseconds: int | None = None
    execution_profile: ReportArchiveResource | None = None
    adverse_assertions: int = 0
    adverse_diffs: int = 0

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
        require_stable_token(self.bundle_id, label="report bundle ID", maximum=255)
        require_stable_token(self.run_id, label="report run ID", maximum=255)
        require_stable_token(self.invocation_id, label="report invocation ID", maximum=255)
        if type(self.subjects) is not tuple or any(
            type(item) is not ReportArchiveResource for item in self.subjects
        ):
            raise TypeError("report archive subjects must be immutable typed coordinates")
        if type(self.environments) is not tuple or any(
            type(item) is not ReportArchiveResource for item in self.environments
        ):
            raise TypeError("report archive environments must be immutable typed coordinates")
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
            self.adverse_assertions,
            self.adverse_diffs,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("report archive entry counts must be non-negative integers")
        if self.duration_nanoseconds is not None and (
            type(self.duration_nanoseconds) is not int or self.duration_nanoseconds < 0
        ):
            raise ValueError("report archive duration must be non-negative or absent")
        if self.execution_profile is not None and (
            type(self.execution_profile) is not ReportArchiveResource
        ):
            raise TypeError("report archive execution profile must be a resource or absent")


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


@final
class ReportArchiveQuery(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned filters and stable continuation for one archive page."""

    page_size: int
    cursor: str | None = None
    text: str | None = None
    completion: str | None = None
    gate: str | None = None
    subject: ReportArchiveResource | None = None
    environment: ReportArchiveResource | None = None

    def __post_init__(self) -> None:
        """Require bounded passive filters without embedding filesystem coordinates."""
        if type(self.page_size) is not int or self.page_size < 1:
            raise ReportArchiveError(ReportArchiveErrorCode.INVALID_LIMIT)
        for value, label, maximum in (
            (self.cursor, "cursor", 1024),
            (self.text, "query text", 256),
            (self.completion, "completion", 64),
            (self.gate, "gate", 64),
        ):
            if value is not None and (type(value) is not str or not value or len(value) > maximum):
                raise ValueError(f"report archive {label} must be bounded text or absent")
        for resource in (self.subject, self.environment):
            if resource is not None and type(resource) is not ReportArchiveResource:
                raise TypeError("report archive resource filters must be typed or absent")


@final
class ReportArchivePage(msgspec.Struct, frozen=True, kw_only=True):
    """One authenticated result window and continuation over its stable sort key."""

    entries: tuple[ReportArchiveEntry, ...]
    failures: tuple[ReportArchiveEntryFailure, ...]
    matched_entries: int
    next_cursor: str | None

    def __post_init__(self) -> None:
        """Reuse listing invariants and require truthful page metadata."""
        ReportArchiveListing(entries=self.entries, failures=self.failures)
        if type(self.matched_entries) is not int or self.matched_entries < len(self.entries):
            raise ValueError("report archive matched count contradicts the page")
        if self.next_cursor is not None and (
            type(self.next_cursor) is not str or not self.next_cursor
        ):
            raise TypeError("report archive continuation must be text or absent")


def list_report_archive(root: Path, limits: ReportArchiveLimits) -> ReportArchiveListing:
    """Authenticate every bounded bundle candidate and return compact safe summaries."""
    if type(limits) is not ReportArchiveLimits:
        raise ReportArchiveError(ReportArchiveErrorCode.INVALID_LIMIT)
    page = query_report_archive(
        root,
        limits,
        ReportArchiveQuery(page_size=limits.maximum_entries),
    )
    return ReportArchiveListing(entries=page.entries, failures=page.failures)


def query_report_archive(
    root: Path,
    limits: ReportArchiveLimits,
    query: ReportArchiveQuery,
) -> ReportArchivePage:
    """Authenticate a bounded archive, filter safe rows, and return one stable page."""
    if type(limits) is not ReportArchiveLimits:
        raise ReportArchiveError(ReportArchiveErrorCode.INVALID_LIMIT)
    if type(query) is not ReportArchiveQuery or query.page_size > limits.maximum_entries:
        raise ReportArchiveError(ReportArchiveErrorCode.INVALID_LIMIT)
    after = _decode_cursor(query.cursor)
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
    matched = tuple(item for item in entries if _matches(item, query))
    remaining = matched
    if after is not None:
        remaining = tuple(item for item in matched if _sort_key(item) > after)
    page = remaining[: query.page_size]
    next_cursor = _encode_cursor(page[-1]) if len(remaining) > len(page) and page else None
    return ReportArchivePage(
        entries=page,
        failures=tuple(failures),
        matched_entries=len(matched),
        next_cursor=next_cursor,
    )


def _entry(bundle_id: str, artifact: RunArtifact) -> ReportArchiveEntry:
    """Project one authenticated safe artifact into a bounded archive row."""
    return ReportArchiveEntry(
        bundle_id=bundle_id,
        run_id=artifact.metadata.run_id,
        invocation_id=artifact.metadata.invocation_id,
        created_unix_nanoseconds=artifact.metadata.created_unix_nanoseconds,
        completion=artifact.completion.kind.value,
        gate=artifact.gate.verdict.value,
        subjects=tuple(
            ReportArchiveResource(namespace=namespace, resource_id=resource_id)
            for namespace, resource_id in sorted(
                {(item.subject.namespace, item.subject.resource_id) for item in artifact.executions}
            )
        ),
        environments=tuple(
            ReportArchiveResource(namespace=namespace, resource_id=resource_id)
            for namespace, resource_id in sorted(
                {
                    (item.environment.namespace, item.environment.resource_id)
                    for item in artifact.executions
                }
            )
        ),
        executions=len(artifact.executions),
        comparisons=len(artifact.comparisons),
        assertions=sum(len(item.assertions) for item in artifact.executions),
        diffs=sum(len(item.diffs) for item in artifact.comparisons),
        blobs=len(artifact.blobs),
        blob_bytes=sum(item.byte_count for item in artifact.blobs),
        coverage_issues=len(artifact.coverage_issues),
        duration_nanoseconds=_duration(artifact),
        execution_profile=_archive_resource(artifact.provenance.execution_profile),
        adverse_assertions=sum(
            item.outcome.value not in {"satisfied", "skipped"}
            for execution in artifact.executions
            for item in execution.assertions
        ),
        adverse_diffs=sum(
            item.outcome.value not in {"equivalent", "skipped"}
            for comparison in artifact.comparisons
            for item in comparison.diffs
        ),
    )


def _duration(artifact: RunArtifact) -> int | None:
    finished = artifact.completion.instant_unix_nanoseconds
    started = artifact.metadata.created_unix_nanoseconds
    if finished is None or finished < started:
        return None
    return finished - started


def _archive_resource(value: object) -> ReportArchiveResource | None:
    from comparo.core.artifacts.common import ArtifactDefinition

    if value is None:
        return None
    if type(value) is not ArtifactDefinition:  # pragma: no cover - artifact invariant
        raise TypeError("archive resource projection requires ArtifactDefinition")
    return ReportArchiveResource(namespace=value.namespace, resource_id=value.resource_id)


def _matches(entry: ReportArchiveEntry, query: ReportArchiveQuery) -> bool:
    if query.completion is not None and entry.completion != query.completion:
        return False
    if query.gate is not None and entry.gate != query.gate:
        return False
    if query.subject is not None and query.subject not in entry.subjects:
        return False
    if query.environment is not None and query.environment not in entry.environments:
        return False
    if query.text is None:
        return True
    needle = query.text.casefold()
    values = (
        entry.bundle_id,
        entry.run_id,
        entry.invocation_id,
        *(f"{item.namespace}/{item.resource_id}" for item in entry.subjects),
        *(f"{item.namespace}/{item.resource_id}" for item in entry.environments),
    )
    if entry.execution_profile is not None:
        profile = entry.execution_profile
        values = (*values, f"{profile.namespace}/{profile.resource_id}")
    return any(needle in value.casefold() for value in values)


def _sort_key(entry: ReportArchiveEntry) -> tuple[int, str]:
    return (-entry.created_unix_nanoseconds, entry.bundle_id)


def _encode_cursor(entry: ReportArchiveEntry) -> str:
    payload = msgspec.json.encode((entry.created_unix_nanoseconds, entry.bundle_id))
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[int, str] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        created, bundle_id = msgspec.json.decode(
            urlsafe_b64decode(padded.encode("ascii")),
            type=tuple[int, str],
        )
        require_stable_token(bundle_id, label="report cursor bundle ID", maximum=255)
        if created < 0:
            raise ValueError
        return (-created, bundle_id)
    except (Base64Error, ValueError, TypeError, UnicodeError, msgspec.DecodeError):
        raise ReportArchiveError(ReportArchiveErrorCode.INVALID_CURSOR) from None


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
    "ReportArchivePage",
    "ReportArchiveQuery",
    "ReportArchiveResource",
    "list_report_archive",
    "query_report_archive",
)
