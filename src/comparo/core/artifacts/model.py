"""Top-level independently versioned RunArtifact manifest contract."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.artifacts.common import ARTIFACT_API_VERSION
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.common import ArtifactBudget
from comparo.core.artifacts.common import ArtifactCapture
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactMetadata
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import ArtifactProvenance
from comparo.core.artifacts.common import require_text
from comparo.core.artifacts.facts import ArtifactComparison
from comparo.core.artifacts.facts import ArtifactExecution
from comparo.core.artifacts.facts import ArtifactFact
from comparo.core.artifacts.facts import ArtifactSummary
from comparo.core.artifacts.lifecycle import ArtifactCompletion
from comparo.core.artifacts.lifecycle import ArtifactGate


@final
class ArtifactCoverageIssue(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One source that was omitted or only partially persisted."""

    source: str
    extent: ArtifactCaptureExtent
    reason: ArtifactOmissionReason
    execution_id: str | None = None

    def __post_init__(self) -> None:
        """Require safe structural location and explicit incompleteness."""
        require_text(self.source, label="artifact coverage source", maximum=2048)
        if self.extent not in {ArtifactCaptureExtent.PARTIAL, ArtifactCaptureExtent.OMITTED}:
            raise ValueError("artifact coverage issues require incomplete capture")
        if type(self.reason) is not ArtifactOmissionReason:
            raise TypeError("artifact coverage reason must be typed")
        if self.execution_id is not None:
            require_text(self.execution_id, label="artifact coverage execution ID", maximum=64)


@final
class RunArtifact(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """A complete safe manifest from which persistence and all reporters derive."""

    api_version: str
    metadata: ArtifactMetadata
    provenance: ArtifactProvenance
    completion: ArtifactCompletion
    gate: ArtifactGate
    summary: ArtifactSummary
    executions: tuple[ArtifactExecution, ...]
    comparisons: tuple[ArtifactComparison, ...]
    resources: tuple[ArtifactDefinition, ...]
    budgets: tuple[ArtifactBudget, ...]
    blobs: tuple[ArtifactBlob, ...]
    coverage_issues: tuple[ArtifactCoverageIssue, ...] = ()

    def __post_init__(self) -> None:
        """Require canonical order, ownership, blob closure, and independent versioning."""
        if self.api_version != ARTIFACT_API_VERSION:
            raise ValueError("unsupported RunArtifact API version")
        for value, expected, label in (
            (self.metadata, ArtifactMetadata, "metadata"),
            (self.provenance, ArtifactProvenance, "provenance"),
            (self.completion, ArtifactCompletion, "completion"),
            (self.gate, ArtifactGate, "gate"),
            (self.summary, ArtifactSummary, "summary"),
        ):
            if type(value) is not expected:
                raise TypeError(f"RunArtifact {label} has an unsupported type")
        _require_artifact_tuple(self.executions, ArtifactExecution, "executions")
        _require_artifact_tuple(self.comparisons, ArtifactComparison, "comparisons")
        _require_artifact_tuple(self.resources, ArtifactDefinition, "resources")
        _require_artifact_tuple(self.budgets, ArtifactBudget, "budgets")
        _require_artifact_tuple(self.blobs, ArtifactBlob, "blobs")
        _require_artifact_tuple(self.coverage_issues, ArtifactCoverageIssue, "coverage issues")
        if tuple(item.ordinal for item in self.executions) != tuple(range(len(self.executions))):
            raise ValueError("artifact executions must use contiguous canonical ordinals")
        if tuple(item.ordinal for item in self.comparisons) != tuple(range(len(self.comparisons))):
            raise ValueError("artifact comparisons must use contiguous canonical ordinals")
        resource_keys = tuple(
            (item.namespace, item.kind, item.resource_id, item.fingerprint)
            for item in self.resources
        )
        if len(set(resource_keys)) != len(resource_keys) or resource_keys != tuple(
            sorted(resource_keys)
        ):
            raise ValueError("artifact resources must be unique and canonical")
        digests = tuple(item.sha256 for item in self.blobs)
        if len(set(digests)) != len(digests) or digests != tuple(sorted(digests)):
            raise ValueError("artifact blobs must be unique and digest ordered")
        declared = set(self.blobs)
        referenced = {
            capture.blob for capture in _artifact_captures(self) if capture.blob is not None
        }
        if referenced != declared:
            raise ValueError("artifact blob manifest must exactly match capture references")


def _require_artifact_tuple(values: object, expected: type[object], label: str) -> None:
    if type(values) is not tuple or any(type(item) is not expected for item in values):
        raise TypeError(f"RunArtifact {label} must be immutable and typed")


def _artifact_captures(artifact: RunArtifact) -> tuple[ArtifactCapture, ...]:
    captures: list[ArtifactCapture] = []
    for execution in artifact.executions:
        for attempt in execution.attempts:
            for exchange in attempt.exchanges:
                if exchange.request is not None and exchange.request.body is not None:
                    captures.append(exchange.request.body)
                if exchange.response is not None and exchange.response.body is not None:
                    captures.append(exchange.response.body)
    return tuple(captures)


def is_artifact_fact(value: object) -> TypeGuard[ArtifactFact]:
    """Narrow arbitrary values to the exact independently versioned fact record."""
    return type(value) is ArtifactFact
