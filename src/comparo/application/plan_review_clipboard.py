"""Constant-size secret-free clipboard projection for a reviewed Plan."""

from __future__ import annotations

from hashlib import sha256
from typing import final

import msgspec

from comparo.application.count_presentation import count_label
from comparo.application.plan_review_checkpoint import PlanReviewInspection
from comparo.application.plan_review_checkpoint import validate_sha256_fingerprint


@final
class PlanClipboardSummary(msgspec.Struct, frozen=True, kw_only=True):
    """Constant-size, secret-free identity of one exact reviewed checkpoint."""

    api_version: str
    review_fingerprint: str
    run_id: str
    plan_fingerprint: str
    policy_namespace: str
    policy_id: str
    subjects: str
    environments: str
    cells: str
    trials: str
    planned_executions: str
    comparisons: str
    maximum_attempts: str
    write_capable_subjects: str
    mutation_approved: bool
    artifact_persistence: str
    tool_version: str
    execution_allowed: bool
    execution_blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require a coherent fixed-version bounded summary."""
        if self.api_version != "comparo.plan-summary/v1alpha1":
            raise ValueError("plan clipboard summary version is unsupported")
        validate_sha256_fingerprint(self.review_fingerprint)
        validate_sha256_fingerprint(self.plan_fingerprint)
        text = (self.run_id, self.policy_namespace, self.policy_id, self.tool_version)
        if any(type(item) is not str or not item or len(item) > 255 for item in text):
            raise ValueError("plan clipboard summary identities must be bounded text")
        counts = (
            self.subjects,
            self.environments,
            self.cells,
            self.trials,
            self.planned_executions,
            self.comparisons,
            self.maximum_attempts,
            self.write_capable_subjects,
        )
        if any(type(item) is not str or not item or len(item) > 64 for item in counts):
            raise ValueError("plan clipboard count labels must be bounded non-empty text")
        if type(self.mutation_approved) is not bool:
            raise TypeError("plan clipboard mutation approval must be a bool")
        if self.artifact_persistence not in {"always", "never", "on-gate-failure"}:
            raise ValueError("plan clipboard artifact persistence mode is unsupported")
        if type(self.execution_allowed) is not bool:
            raise TypeError("plan clipboard execution decision must be a bool")
        _validate_clipboard_decision(self.execution_allowed, self.execution_blockers)


def _validate_clipboard_decision(allowed: bool, blockers: tuple[str, ...]) -> None:
    allowed_blockers = {
        "mutation-approval-required",
        "artifact-persistence-denied",
        "artifact-root-required",
        "artifact-root-denied",
    }
    if type(blockers) is not tuple or any(
        type(item) is not str or item not in allowed_blockers for item in blockers
    ):
        raise ValueError("plan clipboard blockers must use closed safe identities")
    if len(blockers) != len(set(blockers)):
        raise ValueError("plan clipboard blockers must be unique")
    if allowed == bool(blockers):
        raise ValueError("plan clipboard decision contradicts its blockers")


def clipboard_summary(review: PlanReviewInspection) -> PlanClipboardSummary:
    """Reduce an arbitrary-size Plan to a constant-size clipboard-safe summary."""
    if type(review) is not PlanReviewInspection:
        raise TypeError("plan clipboard summary requires PlanReviewInspection")
    plan = review.plan
    return PlanClipboardSummary(
        api_version="comparo.plan-summary/v1alpha1",
        review_fingerprint=_review_fingerprint(review),
        run_id=plan.run_id,
        plan_fingerprint=plan.fingerprint,
        policy_namespace=plan.policy.namespace,
        policy_id=plan.policy.resource_id,
        subjects=count_label(len(plan.slices)),
        environments=count_label(plan.environment_count),
        cells=count_label(plan.cardinality.cells),
        trials=count_label(plan.cardinality.trials),
        planned_executions=count_label(plan.cardinality.environment_executions),
        comparisons=count_label(plan.cardinality.comparisons),
        maximum_attempts=count_label(plan.cardinality.maximum_attempts),
        write_capable_subjects=count_label(plan.write_capable_subjects),
        mutation_approved=review.mutation_approved,
        artifact_persistence=review.runtime.artifact_persistence,
        tool_version=review.tool_version,
        execution_allowed=review.execution_allowed,
        execution_blockers=review.execution_blockers,
    )


def _review_fingerprint(review: PlanReviewInspection) -> str:
    """Hash only fixed-size checkpoint identities, never the arbitrary-size Plan graph."""
    digest = sha256(b"comparo.plan-review/v1\0")
    values = (
        review.plan.fingerprint,
        review.execution.fingerprint,
        review.execution.artifact_root_fingerprint or "no-artifact-root",
        review.runtime.artifact_persistence,
        review.tool_version,
        "mutation-approved" if review.mutation_approved else "mutation-denied",
    )
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    for limit in review.runtime.limits:
        for value in (limit.section, limit.name, limit.unit):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(2, "big"))
            digest.update(encoded)
        for integer in (limit.value, limit.denominator):
            byte_count = max(1, (integer.bit_length() + 7) // 8)
            digest.update(byte_count.to_bytes(8, "big"))
            digest.update(integer.to_bytes(byte_count, "big"))
    return digest.hexdigest()


__all__ = ("PlanClipboardSummary", "clipboard_summary")
