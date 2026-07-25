"""Narrow ports that can carry only sanitized blobs and safe artifact models."""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Protocol
from typing import final

import msgspec

from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.common import require_text
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.resolution.models import SanitizationSnapshot


class ArtifactStoreErrorCode(DomainStrEnum):
    """Closed value-free artifact storage failure categories."""

    ROOT_UNSAFE = "root-unsafe"
    ALREADY_EXISTS = "already-exists"
    BUDGET_EXCEEDED = "budget-exceeded"
    DIGEST_MISMATCH = "digest-mismatch"
    IO_FAILED = "io-failed"
    COMMIT_UNCERTAIN = "commit-uncertain"
    UNSUPPORTED_PLATFORM = "unsupported-platform"
    CLOSED = "closed"


class ArtifactPersistenceStatus(DomainStrEnum):
    """Terminal safe persistence states."""

    PERSISTED = "persisted"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    COMMIT_UNCERTAIN = "commit-uncertain"


@final
class ArtifactStoreFailure(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free storage failure safe for diagnostics and reports."""

    code: ArtifactStoreErrorCode

    def __post_init__(self) -> None:
        """Require the closed failure vocabulary."""
        if type(self.code) is not ArtifactStoreErrorCode:
            raise TypeError("artifact store failure code must be typed")


@final
class ArtifactPersistenceOutcome(msgspec.Struct, frozen=True, kw_only=True):
    """Persistence result containing an opaque identifier rather than a path."""

    status: ArtifactPersistenceStatus
    bundle_id: str | None = None
    failure: ArtifactStoreFailure | None = None

    def __post_init__(self) -> None:
        """Keep successful and unsuccessful persistence shapes disjoint."""
        if type(self.status) is not ArtifactPersistenceStatus:
            raise TypeError("artifact persistence status must be typed")
        if self.bundle_id is not None:
            require_text(self.bundle_id, label="artifact bundle ID", maximum=127)
        if self.failure is not None and type(self.failure) is not ArtifactStoreFailure:
            raise TypeError("artifact persistence failure must be typed")
        persisted = self.status is ArtifactPersistenceStatus.PERSISTED
        if persisted != (self.bundle_id is not None and self.failure is None):
            raise ValueError("artifact persistence status contradicts its outcome shape")
        if not persisted and self.failure is None:
            raise ValueError("unsuccessful artifact persistence requires a failure")


class ArtifactBodyReader(Protocol):
    """Stream a live request or response body without exposing a storage locator."""

    def read(self, source: RepeatableByteSource | BodyHandle) -> AsyncIterable[bytes]:
        """Return exact transient source chunks during the source store's lifetime."""
        ...


class SanitizationSnapshotProvider(Protocol):
    """Expose the final same-run registry snapshot before the RunContext closes."""

    async def sanitization_snapshot(self) -> SanitizationSnapshot:
        """Return all sealed values registered by this run so far."""
        ...


class SanitizedBlobWriter(Protocol):
    """Incrementally stage one already-sanitized content-addressed blob."""

    async def write(self, chunk: bytes) -> None:
        """Append one safe byte chunk while enforcing caller-owned limits."""
        ...

    async def commit(self, *, media_type: str | None) -> ArtifactBlob:
        """Seal the blob and return its sanitized descriptor."""
        ...

    async def abort(self) -> None:
        """Discard this incomplete sanitized blob without publishing it."""
        ...


class SanitizedBlobTarget(Protocol):
    """Create sanitized-only blob writers for one artifact compilation."""

    async def begin(self) -> SanitizedBlobWriter:
        """Create one independent sanitized blob transaction."""
        ...


class SanitizedBlobSource(Protocol):
    """Read only blobs addressed by independently safe ArtifactBlob descriptors."""

    def read(self, blob: ArtifactBlob) -> AsyncIterable[bytes]:
        """Return the exact sanitized bytes for a declared artifact blob."""
        ...


class SanitizedArtifactSink(Protocol):
    """Persist a safe artifact and safe blob source without accepting live results."""

    async def persist(
        self,
        artifact: RunArtifact,
        blobs: SanitizedBlobSource,
        limits: ArtifactLimits,
    ) -> ArtifactPersistenceOutcome:
        """Atomically persist one validated artifact bundle."""
        ...
