"""Typed terminal results returned by an external analysis executor."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.sensitivity import SensitiveValue


class AnalysisVerdict(StrEnum):
    """Semantic judgments returned by an authorized external analysis."""

    EQUIVALENT = "equivalent"
    DRIFTED = "drifted"
    INCONCLUSIVE = "inconclusive"


@final
class AnalysisEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free evidence identity and exact count from an external analysis."""

    code: DiagnosticCode
    digest: SensitiveValue[Fingerprint]
    items: Count

    def __post_init__(self) -> None:
        """Require stable structured evidence without arbitrary provider prose."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("analysis evidence code must be a DiagnosticCode")
        if (
            not isinstance(self.digest, SensitiveValue)
            or self.digest.payload_type is not Fingerprint
        ):
            raise TypeError("analysis evidence digest must be a sensitive Fingerprint")
        if type(self.items) is not Count:
            raise TypeError("analysis evidence items must be a Count")


@final
class AnalysisSucceeded(msgspec.Struct, frozen=True, kw_only=True):
    """A completed semantic analysis with optional exact normalized score."""

    verdict: AnalysisVerdict
    score: Ratio | None
    evidence: tuple[AnalysisEvidence, ...]

    def __post_init__(self) -> None:
        """Require a typed verdict, score in [0, 1], and immutable evidence."""
        if type(self.verdict) is not AnalysisVerdict:
            raise TypeError("analysis verdict must be an AnalysisVerdict")
        if self.score is not None:
            if type(self.score) is not Ratio:
                raise TypeError("analysis score must be a Ratio")
            if self.score.numerator > self.score.denominator.value:
                raise ValueError("analysis score must be between zero and one")
        if type(self.evidence) is not tuple:
            raise TypeError("analysis evidence must be an immutable tuple")
        if any(type(item) is not AnalysisEvidence for item in self.evidence):
            raise TypeError("analysis evidence must contain AnalysisEvidence values")


@final
class AnalysisTimedOut(msgspec.Struct, frozen=True, kw_only=True):
    """The authorized analysis exceeded its explicit deadline."""

    elapsed: Duration

    def __post_init__(self) -> None:
        """Require an exact elapsed duration."""
        if type(self.elapsed) is not Duration:
            raise TypeError("analysis timeout elapsed value must be a Duration")


@final
class AnalysisProviderFailed(msgspec.Struct, frozen=True, kw_only=True):
    """The provider failed with a value-free structured diagnostic."""

    diagnostic: Diagnostic

    def __post_init__(self) -> None:
        """Require an error diagnostic."""
        _require_error_diagnostic(self.diagnostic, label="analysis provider failure")


@final
class AnalysisMalformed(msgspec.Struct, frozen=True, kw_only=True):
    """The provider returned an unusable response."""

    diagnostic: Diagnostic

    def __post_init__(self) -> None:
        """Require an error diagnostic."""
        _require_error_diagnostic(self.diagnostic, label="malformed analysis")


@final
class AnalysisCancelled(msgspec.Struct, frozen=True, kw_only=True):
    """The analysis was cancelled by an explicit outer source."""

    source: CancellationSource

    def __post_init__(self) -> None:
        """Require a typed cancellation source."""
        if type(self.source) is not CancellationSource:
            raise TypeError("analysis cancellation source must be a CancellationSource")


@final
class AnalysisBudgetExhausted(msgspec.Struct, frozen=True, kw_only=True):
    """Analysis could not complete within an effective resource budget."""

    exhaustion: BudgetExceeded

    def __post_init__(self) -> None:
        """Require a typed external-capability exhaustion."""
        if type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("analysis budget exhaustion requires BudgetExceeded")
        if self.exhaustion.budget.stage is not BudgetStage.EXTERNAL_CAPABILITY:
            raise ValueError("analysis exhaustion budget must be external-capability scoped")


type AnalysisExecutionResult = (
    AnalysisSucceeded
    | AnalysisTimedOut
    | AnalysisProviderFailed
    | AnalysisMalformed
    | AnalysisCancelled
    | AnalysisBudgetExhausted
)


def _require_error_diagnostic(diagnostic: Diagnostic, *, label: str) -> None:
    if type(diagnostic) is not Diagnostic:
        raise TypeError(f"{label} requires a Diagnostic")
    if diagnostic.severity is not DiagnosticSeverity.ERROR:
        raise ValueError(f"{label} diagnostic must have error severity")
