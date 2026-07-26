"""Exact execution controls and start-decision checkpoint for one Plan."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import final

import msgspec

from comparo.application.inspection import PlanInspection
from comparo.application.plan_review_limits import RuntimeLimitsInspection
from comparo.application.production_execution import ProductionExecutionSettings


@final
class ExecutionSettingsInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Complete safe identity plus human-relevant non-limit execution controls."""

    fingerprint: str
    response_body_sensitivity: str
    sensitive_response_headers: tuple[str, ...]
    artifact_unsafe_source: str
    capture_requests: bool
    capture_responses: bool
    capture_bodies: bool
    capture_evidence: bool
    artifact_root_fingerprint: str | None

    def __post_init__(self) -> None:
        """Require exact closed settings without retaining an artifact path."""
        validate_sha256_fingerprint(self.fingerprint)
        if self.artifact_root_fingerprint is not None:
            validate_sha256_fingerprint(self.artifact_root_fingerprint)
        if self.response_body_sensitivity not in {"public", "sensitive"}:
            raise ValueError("response body sensitivity inspection is unsupported")
        if self.artifact_unsafe_source not in {"omit", "reject-artifact"}:
            raise ValueError("artifact unsafe-source inspection is unsupported")
        if type(self.sensitive_response_headers) is not tuple or any(
            type(item) is not str or not item for item in self.sensitive_response_headers
        ):
            raise ValueError("sensitive response header identities must be typed text")
        if len(self.sensitive_response_headers) != len(set(self.sensitive_response_headers)):
            raise ValueError("sensitive response header identities must be unique")
        flags = (
            self.capture_requests,
            self.capture_responses,
            self.capture_bodies,
            self.capture_evidence,
        )
        if any(type(item) is not bool for item in flags):
            raise TypeError("artifact capture inspection flags must be bool")


@final
class PlanReviewInspection(msgspec.Struct, frozen=True, kw_only=True):
    """The complete frontend-owned checkpoint consumed by Plan presentation and copy."""

    plan: PlanInspection
    runtime: RuntimeLimitsInspection
    execution: ExecutionSettingsInspection
    mutation_approved: bool
    tool_version: str

    def __post_init__(self) -> None:
        """Require the exact typed plan, limits, and explicit approval decision."""
        if type(self.plan) is not PlanInspection:
            raise TypeError("plan review requires PlanInspection")
        if type(self.runtime) is not RuntimeLimitsInspection:
            raise TypeError("plan review requires RuntimeLimitsInspection")
        if type(self.execution) is not ExecutionSettingsInspection:
            raise TypeError("plan review requires ExecutionSettingsInspection")
        if type(self.mutation_approved) is not bool:
            raise TypeError("plan review mutation approval must be bool")
        if (
            type(self.tool_version) is not str
            or not self.tool_version
            or len(self.tool_version) > 64
        ):
            raise ValueError("plan review tool version must be bounded non-empty text")
        _validate_review_dependencies(self.plan, self.execution)

    @property
    def execution_blockers(self) -> tuple[str, ...]:
        """Return closed reasons why this checkpoint cannot consume its prepared handle."""
        authority = self.plan.http_authority
        approval_required = (
            authority is not None
            and authority.require_mutation_approval
            and any(item.unsafe_method is True for item in self.plan.slices)
        )
        blockers: list[str] = []
        if approval_required and not self.mutation_approved:
            blockers.append("mutation-approval-required")
        if self.runtime.artifact_persistence != "never":
            artifacts = self.plan.artifact_authority
            if artifacts is None or not artifacts.allow_sanitized_persistence:
                blockers.append("artifact-persistence-denied")
            elif not artifacts.selected_root_present:
                blockers.append("artifact-root-required")
            elif not artifacts.selected_root_authorized:
                blockers.append("artifact-root-denied")
        return tuple(blockers)

    @property
    def execution_allowed(self) -> bool:
        """Return whether the displayed checkpoint may consume its prepared handle."""
        return not self.execution_blockers

    @property
    def policy_advisories(self) -> tuple[str, ...]:
        """Return closed broad-authority risks that require operator attention."""
        http = self.plan.http_authority
        values = self.plan.value_authority
        if http is None or values is None:  # guarded by __post_init__, retained for typing
            return ()
        advisories: list[str] = []
        if not any(item.profiles.safety for item in self.plan.slices):
            advisories.append("no-safety-profile")
        for enabled, code in (
            (http.allow_any_host, "any-host"),
            (http.allow_any_port, "any-port"),
            (http.allow_proxy_dns, "proxy-dns"),
            (values.allow_any_ambient_environment, "any-ambient-environment"),
            (values.allow_project_files, "project-files"),
            (values.allow_any_remote_schemas, "any-remote-schemas"),
            (values.allow_any_secret_egress, "any-secret-egress"),
        ):
            if enabled:
                advisories.append(code)
        return tuple(advisories)


def _validate_review_dependencies(
    plan: PlanInspection,
    execution: ExecutionSettingsInspection,
) -> None:
    if plan.http_authority is None:
        raise ValueError("plan review requires exact HTTP policy authority")
    if plan.value_authority is None:
        raise ValueError("plan review requires exact value-source policy authority")
    if plan.artifact_authority is None:
        raise ValueError("plan review requires exact artifact policy authority")
    if plan.validation is None:
        raise ValueError("plan review requires exact evaluation preflight")
    root_selected = execution.artifact_root_fingerprint is not None
    if root_selected is not plan.artifact_authority.selected_root_present:
        raise ValueError("plan review artifact-root selection facts are contradictory")


def inspect_execution_settings(
    settings: ProductionExecutionSettings,
    artifact_root: Path | None,
) -> ExecutionSettingsInspection:
    """Project every non-limit execution setting and an opaque root selection identity."""
    if type(settings) is not ProductionExecutionSettings:
        raise TypeError("execution settings inspection requires ProductionExecutionSettings")
    if artifact_root is not None and not isinstance(artifact_root, Path):
        raise TypeError("execution settings artifact root must be a Path or absent")
    if artifact_root is not None and not artifact_root.is_absolute():
        raise ValueError("execution settings artifact root must be resolved and absolute")
    capture = settings.artifact.capture
    root_fingerprint = None
    if artifact_root is not None:
        root_fingerprint = sha256(
            b"comparo.artifact-root/v1\0" + str(artifact_root).encode("utf-8")
        ).hexdigest()
    return ExecutionSettingsInspection(
        fingerprint=sha256(msgspec.json.encode(settings)).hexdigest(),
        response_body_sensitivity=settings.transport.classification.body.value,
        sensitive_response_headers=tuple(
            item.value for item in settings.transport.classification.sensitive_headers
        ),
        artifact_unsafe_source=settings.artifact.unsafe_source.value,
        capture_requests=capture.requests,
        capture_responses=capture.responses,
        capture_bodies=capture.bodies,
        capture_evidence=capture.evidence,
        artifact_root_fingerprint=root_fingerprint,
    )


def validate_sha256_fingerprint(value: str) -> None:
    """Reject values that are not canonical lowercase SHA-256 digests."""
    if type(value) is not str or (
        len(value) != 64 or any(item not in "0123456789abcdef" for item in value)
    ):
        raise ValueError("plan clipboard summary requires SHA-256 fingerprints")


__all__ = (
    "ExecutionSettingsInspection",
    "PlanReviewInspection",
    "inspect_execution_settings",
    "validate_sha256_fingerprint",
)
