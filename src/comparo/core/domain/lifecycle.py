"""Run lifecycle outcomes kept independent from gate verdicts."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import UtcInstant


@final
class RunComplete(msgspec.Struct, frozen=True, kw_only=True):
    """All admitted execution and evaluation work reached a terminal result."""

    completed_at: UtcInstant

    def __post_init__(self) -> None:
        """Require an exact completion instant."""
        if type(self.completed_at) is not UtcInstant:
            raise TypeError("run completion requires a UtcInstant")


class PartialRunReason(DomainStrEnum):
    """Stable categories explaining why admitted work is incomplete."""

    EXECUTION_INCOMPLETE = "execution-incomplete"
    EVALUATION_INCOMPLETE = "evaluation-incomplete"
    EVIDENCE_INCOMPLETE = "evidence-incomplete"
    ARTIFACT_INCOMPLETE = "artifact-incomplete"
    EXTERNAL_CAPABILITY_UNAVAILABLE = "external-capability-unavailable"


@final
class RunPartial(msgspec.Struct, frozen=True, kw_only=True):
    """The run terminated normally but some admitted work is incomplete."""

    reasons: tuple[PartialRunReason, ...]
    completed_at: UtcInstant

    def __post_init__(self) -> None:
        """Require non-empty unique typed reasons and an exact instant."""
        if type(self.reasons) is not tuple:
            raise TypeError("partial run reasons must be an immutable tuple")
        if not self.reasons:
            raise ValueError("partial run requires at least one reason")
        if any(type(reason) is not PartialRunReason for reason in self.reasons):
            raise TypeError("partial run reasons must contain PartialRunReason values")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("partial run reasons must be unique")
        if type(self.completed_at) is not UtcInstant:
            raise TypeError("partial run completion requires a UtcInstant")


class CancellationSource(DomainStrEnum):
    """The authority or outer condition that requested cancellation."""

    USER = "user"
    PROCESS_SIGNAL = "process-signal"
    DEADLINE = "deadline"
    PARENT_SCOPE = "parent-scope"
    EXECUTION_POLICY = "execution-policy"


@final
class RunCancelled(msgspec.Struct, frozen=True, kw_only=True):
    """A run stopped through an explicit cancellation path."""

    source: CancellationSource
    stage: DiagnosticStage
    cancelled_at: UtcInstant

    def __post_init__(self) -> None:
        """Require typed cancellation origin, stage, and instant."""
        if type(self.source) is not CancellationSource:
            raise TypeError("cancellation source must be a CancellationSource")
        if type(self.stage) is not DiagnosticStage:
            raise TypeError("cancellation stage must be a DiagnosticStage")
        if type(self.cancelled_at) is not UtcInstant:
            raise TypeError("cancellation instant must be a UtcInstant")


@final
class RunRejected(msgspec.Struct, frozen=True, kw_only=True):
    """Admission failed before external execution began."""

    diagnostics: tuple[Diagnostic, ...]

    def __post_init__(self) -> None:
        """Require at least one typed error diagnostic."""
        _require_error_diagnostics(self.diagnostics, label="run rejection")


@final
class RunInternalError(msgspec.Struct, frozen=True, kw_only=True):
    """An engine defect represented without embedding an unsafe exception message."""

    diagnostic: Diagnostic

    def __post_init__(self) -> None:
        """Require one structured error diagnostic."""
        if type(self.diagnostic) is not Diagnostic:
            raise TypeError("internal error requires a Diagnostic")
        if self.diagnostic.severity is not DiagnosticSeverity.ERROR:
            raise ValueError("internal error diagnostic must have error severity")


type RunCompletion = RunComplete | RunPartial | RunCancelled | RunRejected | RunInternalError


def _require_error_diagnostics(diagnostics: tuple[Diagnostic, ...], *, label: str) -> None:
    if type(diagnostics) is not tuple:
        raise TypeError(f"{label} diagnostics must be an immutable tuple")
    if not diagnostics:
        raise ValueError(f"{label} requires at least one diagnostic")
    if any(type(diagnostic) is not Diagnostic for diagnostic in diagnostics):
        raise TypeError(f"{label} diagnostics must contain Diagnostic values")
    if any(diagnostic.severity is not DiagnosticSeverity.ERROR for diagnostic in diagnostics):
        raise ValueError(f"{label} diagnostics must all have error severity")
