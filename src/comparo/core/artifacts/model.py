"""Top-level independently versioned RunArtifact manifest contract."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.artifacts.common import ARTIFACT_API_VERSION
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.common import ArtifactBudget
from comparo.core.artifacts.common import ArtifactCapture
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactComparisonOutcome
from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactExecutionOutcome
from comparo.core.artifacts.common import ArtifactMetadata
from comparo.core.artifacts.common import ArtifactMetric
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
        _require_unique_result_ids(self)
        _require_comparison_integrity(self)
        _require_summary_conservation(self)
        _require_resource_closure(self)
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


def _require_unique_result_ids(artifact: RunArtifact) -> None:
    execution_ids = tuple(item.execution_id for item in artifact.executions)
    if len(set(execution_ids)) != len(execution_ids):
        raise ValueError("artifact execution IDs must be unique")
    comparison_ids = tuple(item.comparison_id for item in artifact.comparisons)
    if len(set(comparison_ids)) != len(comparison_ids):
        raise ValueError("artifact comparison IDs must be unique")


def _require_comparison_integrity(artifact: RunArtifact) -> None:
    executions = {item.execution_id: item for item in artifact.executions}
    for comparison in artifact.comparisons:
        side_ids = (
            comparison.baseline_execution_id,
            comparison.candidate_execution_id,
        )
        if side_ids[0] == side_ids[1]:
            raise ValueError("artifact comparison sides must identify distinct executions")
        if any(execution_id not in executions for execution_id in side_ids):
            raise ValueError("artifact comparison references an unknown execution")
        sides = tuple(executions[execution_id] for execution_id in side_ids)
        if any(
            side.subject != comparison.subject
            or side.cell_id != comparison.cell_id
            or side.trial_id != comparison.trial_id
            for side in sides
        ):
            raise ValueError("artifact comparison sides contradict its subject, cell, or trial")
        unavailable = tuple(
            side.execution_id
            for side in sides
            if side.outcome is not ArtifactExecutionOutcome.OBSERVED
        )
        if comparison.outcome is ArtifactComparisonOutcome.EVALUATED:
            if unavailable:
                raise ValueError("evaluated artifact comparison requires observed executions")
        elif comparison.unavailable_execution_ids != unavailable:
            raise ValueError(
                "incomplete artifact comparison must list exactly its unavailable sides"
            )


def _require_summary_conservation(artifact: RunArtifact) -> None:
    expected = ArtifactSummary(
        executions=_tallies(item.outcome.value for item in artifact.executions),
        comparisons=_tallies(item.outcome.value for item in artifact.comparisons),
        assertions=_tallies(
            fact.outcome.value for item in artifact.executions for fact in item.assertions
        ),
        diffs=_tallies(fact.outcome.value for item in artifact.comparisons for fact in item.diffs),
    )
    if artifact.summary != expected:
        raise ValueError("artifact summary must exactly conserve retained result outcomes")


def _tallies(values: Iterable[str]) -> tuple[ArtifactMetric, ...]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return tuple(ArtifactMetric(name=name, value=counts[name]) for name in sorted(counts))


def _require_resource_closure(artifact: RunArtifact) -> None:
    identities = tuple(_definition_identity(item) for item in artifact.resources)
    if len(set(identities)) != len(identities):
        raise ValueError("artifact resource stable identities must be unique")
    if identities != tuple(sorted(identities)):
        raise ValueError("artifact resources must use canonical stable identity order")
    declared = set(artifact.resources)
    referenced = _referenced_definitions(artifact)
    if not referenced.issubset(declared):
        raise ValueError("artifact resources must contain every referenced definition")


def _definition_identity(value: ArtifactDefinition) -> tuple[str, str, str]:
    return value.namespace, value.kind, value.resource_id


def _referenced_definitions(artifact: RunArtifact) -> set[ArtifactDefinition]:
    optional = (
        artifact.provenance.artifact_profile,
        artifact.provenance.redaction_profile,
        artifact.gate.profile,
    )
    return {
        artifact.provenance.project,
        artifact.provenance.execution_policy,
        *(item for item in optional if item is not None),
        *(item.subject for item in artifact.executions),
        *(item.environment for item in artifact.executions),
        *(item.subject for item in artifact.comparisons),
        *(fact.rule for item in artifact.executions for fact in item.assertions),
        *(fact.rule for item in artifact.comparisons for fact in item.diffs),
    }


def _artifact_captures(artifact: RunArtifact) -> tuple[ArtifactCapture, ...]:
    captures: list[ArtifactCapture] = []
    for execution in artifact.executions:
        for attempt in execution.attempts:
            for exchange in attempt.exchanges:
                if exchange.request is not None and exchange.request.body is not None:
                    captures.append(exchange.request.body)
                if exchange.response is not None and exchange.response.body is not None:
                    captures.append(exchange.response.body)
            request = attempt.terminal_pre_send_request
            if request is not None and request.body is not None:
                captures.append(request.body)
    return tuple(captures)


def is_artifact_fact(value: object) -> TypeGuard[ArtifactFact]:
    """Narrow arbitrary values to the exact independently versioned fact record."""
    return type(value) is ArtifactFact
