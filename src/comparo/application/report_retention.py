"""Explicit recoverable retention planning for authenticated local reports."""

from __future__ import annotations

from contextlib import suppress
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import final

import msgspec

from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.adapters.artifacts import restore_local_artifact_bundle
from comparo.adapters.artifacts import trash_local_artifact_bundle
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.artifacts import verify_artifact
from comparo.application.report_archive import ReportArchiveEntryFailure
from comparo.application.report_archive import ReportArchiveLimits
from comparo.application.report_archive import ReportArchiveQuery
from comparo.application.report_archive import query_report_archive
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.ports import ArtifactStoreErrorCode


class ReportRetentionErrorCode(StrEnum):
    """Closed planning and application failures safe for frontend presentation."""

    INVALID_POLICY = "invalid-policy"
    BUDGET_EXCEEDED = "budget-exceeded"
    PLAN_MISMATCH = "plan-mismatch"


class ReportRetentionError(RuntimeError):
    """Value-free retention failure that never retains archive paths."""

    __slots__ = ("code",)

    def __init__(self, code: ReportRetentionErrorCode) -> None:
        """Retain only the closed failure code."""
        if type(code) is not ReportRetentionErrorCode:
            raise TypeError("report retention error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class ReportRetentionPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned rule selecting old reports while preserving a newest floor."""

    keep_newest: int
    maximum_actions: int
    created_before_unix_nanoseconds: int | None = None
    preserve_gate_verdicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require explicit finite retention and action boundaries."""
        if type(self.keep_newest) is not int or self.keep_newest < 0:
            raise ReportRetentionError(ReportRetentionErrorCode.INVALID_POLICY)
        if type(self.maximum_actions) is not int or self.maximum_actions < 1:
            raise ReportRetentionError(ReportRetentionErrorCode.INVALID_POLICY)
        if self.created_before_unix_nanoseconds is not None and (
            type(self.created_before_unix_nanoseconds) is not int
            or self.created_before_unix_nanoseconds < 0
        ):
            raise ReportRetentionError(ReportRetentionErrorCode.INVALID_POLICY)
        if type(self.preserve_gate_verdicts) is not tuple or any(
            type(item) is not str or not item or len(item) > 64
            for item in self.preserve_gate_verdicts
        ):
            raise ReportRetentionError(ReportRetentionErrorCode.INVALID_POLICY)
        if len(set(self.preserve_gate_verdicts)) != len(self.preserve_gate_verdicts):
            raise ReportRetentionError(ReportRetentionErrorCode.INVALID_POLICY)


@final
class ReportRetentionCandidate(msgspec.Struct, frozen=True, kw_only=True):
    """One authenticated report identity pinned by the retention plan."""

    bundle_id: str
    run_id: str
    created_unix_nanoseconds: int


@final
class ReportRetentionPlan(msgspec.Struct, frozen=True, kw_only=True):
    """Reviewable immutable set of bundles eligible for recoverable trashing."""

    candidates: tuple[ReportRetentionCandidate, ...]
    skipped_failures: tuple[ReportArchiveEntryFailure, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        """Require immutable typed candidates and one exact fingerprint."""
        if type(self.candidates) is not tuple or any(
            type(item) is not ReportRetentionCandidate for item in self.candidates
        ):
            raise TypeError("report retention candidates must be immutable and typed")
        if type(self.skipped_failures) is not tuple or any(
            type(item) is not ReportArchiveEntryFailure for item in self.skipped_failures
        ):
            raise TypeError("report retention failures must be immutable and typed")
        if len(self.fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in self.fingerprint
        ):
            raise ValueError("report retention fingerprint must be lowercase SHA-256")


@final
class ReportRetentionFailure(msgspec.Struct, frozen=True, kw_only=True):
    """One planned bundle that could not be verified or moved."""

    bundle_id: str
    code: ArtifactStoreErrorCode


@final
class ReportRetentionOutcome(msgspec.Struct, frozen=True, kw_only=True):
    """Exact partial-success result of applying one reviewed plan."""

    trashed_bundle_ids: tuple[str, ...]
    failures: tuple[ReportRetentionFailure, ...]


def plan_report_retention(
    root: Path,
    limits: ReportArchiveLimits,
    policy: ReportRetentionPolicy,
) -> ReportRetentionPlan:
    """Authenticate the bounded archive and derive a deterministic reviewable plan."""
    if type(policy) is not ReportRetentionPolicy:
        raise ReportRetentionError(ReportRetentionErrorCode.INVALID_POLICY)
    page = query_report_archive(
        root,
        limits,
        ReportArchiveQuery(page_size=limits.maximum_entries),
    )
    candidates = tuple(
        ReportRetentionCandidate(
            bundle_id=entry.bundle_id,
            run_id=entry.run_id,
            created_unix_nanoseconds=entry.created_unix_nanoseconds,
        )
        for entry in page.entries[policy.keep_newest :]
        if (
            policy.created_before_unix_nanoseconds is None
            or entry.created_unix_nanoseconds < policy.created_before_unix_nanoseconds
        )
        and entry.gate not in policy.preserve_gate_verdicts
    )
    if len(candidates) > policy.maximum_actions:
        raise ReportRetentionError(ReportRetentionErrorCode.BUDGET_EXCEEDED)
    return ReportRetentionPlan(
        candidates=candidates,
        skipped_failures=page.failures,
        fingerprint=_fingerprint(candidates),
    )


def apply_report_retention(
    root: Path,
    limits: ArtifactReadLimits,
    plan: ReportRetentionPlan,
) -> ReportRetentionOutcome:
    """Re-verify every plan coordinate and move matching bundles to private trash."""
    if type(plan) is not ReportRetentionPlan or plan.fingerprint != _fingerprint(plan.candidates):
        raise ReportRetentionError(ReportRetentionErrorCode.PLAN_MISMATCH)
    trashed: list[str] = []
    failures: list[ReportRetentionFailure] = []
    for candidate in plan.candidates:
        try:
            artifact = verify_artifact(root, candidate.bundle_id, limits)
            if (
                artifact.metadata.run_id != candidate.run_id
                or artifact.metadata.created_unix_nanoseconds != candidate.created_unix_nanoseconds
            ):
                raise ReportRetentionError(ReportRetentionErrorCode.PLAN_MISMATCH)
            trash_local_artifact_bundle(root, candidate.bundle_id)
            trashed.append(candidate.bundle_id)
        except ReportRetentionError:
            raise
        except ArtifactOperationError:
            failures.append(
                ReportRetentionFailure(
                    bundle_id=candidate.bundle_id,
                    code=ArtifactStoreErrorCode.IO_FAILED,
                )
            )
        except LocalArtifactStoreError as error:
            failures.append(ReportRetentionFailure(bundle_id=candidate.bundle_id, code=error.code))
    return ReportRetentionOutcome(
        trashed_bundle_ids=tuple(trashed),
        failures=tuple(failures),
    )


def restore_report(
    root: Path,
    bundle_id: str,
    limits: ArtifactReadLimits,
) -> RunArtifact:
    """Restore one trashed bundle, authenticate it, and roll back if verification fails."""
    restore_local_artifact_bundle(root, bundle_id)
    try:
        return verify_artifact(root, bundle_id, limits)
    except Exception:
        with suppress(LocalArtifactStoreError):
            trash_local_artifact_bundle(root, bundle_id)
        raise


def _fingerprint(candidates: tuple[ReportRetentionCandidate, ...]) -> str:
    payload = msgspec.json.encode(
        tuple((item.bundle_id, item.run_id, item.created_unix_nanoseconds) for item in candidates)
    )
    return sha256(payload).hexdigest()


__all__ = (
    "ReportRetentionCandidate",
    "ReportRetentionError",
    "ReportRetentionErrorCode",
    "ReportRetentionFailure",
    "ReportRetentionOutcome",
    "ReportRetentionPlan",
    "ReportRetentionPolicy",
    "apply_report_retention",
    "plan_report_retention",
    "restore_report",
)
