"""Authorized external-analysis contracts kept outside pure diff evaluation."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Protocol
from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.capabilities import CapabilityCategory
from comparo.core.domain.capabilities import CapabilityDenied
from comparo.core.domain.capabilities import CapabilityGranted
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.identity import AnalysisId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity

_PROVIDER_TOKEN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


@final
class AnalysisProvider(msgspec.Struct, frozen=True, kw_only=True):
    """A versioned provider/model and its declared data destination."""

    provider: str
    provider_version: str
    model: str
    model_version: str
    destination: NamedCapabilityTarget

    def __post_init__(self) -> None:
        """Require explicit stable identities and a destination target."""
        if type(self.provider) is not str or _PROVIDER_TOKEN.fullmatch(self.provider) is None:
            raise ValueError("analysis provider must be a lowercase namespaced token")
        for label, value in (
            ("provider version", self.provider_version),
            ("model", self.model),
            ("model version", self.model_version),
        ):
            if type(value) is not str or not value.strip():
                raise ValueError(f"analysis {label} must be non-blank text")
        if type(self.destination) is not NamedCapabilityTarget:
            raise TypeError("analysis destination must be a NamedCapabilityTarget")
        if self.destination.kind is not CapabilityTargetKind.DESTINATION:
            raise ValueError("analysis destination must use the destination target kind")


type AnalysisInputSource = BodyId | ViewId | FactId


@final
class AnalysisInput(msgspec.Struct, frozen=True, kw_only=True):
    """A bounded data descriptor; payload access remains an authorized runtime concern."""

    source: AnalysisInputSource
    digest: Fingerprint | SensitiveValue[Fingerprint]
    bytes: ByteCount
    sensitivity: Sensitivity
    media_type: str | None = None

    def __post_init__(self) -> None:
        """Require typed source identity and non-payload metadata."""
        if type(self.source) not in {BodyId, ViewId, FactId}:
            raise TypeError("analysis input source must be BodyId, ViewId, or FactId")
        if type(self.digest) is not Fingerprint and not (
            isinstance(self.digest, SensitiveValue) and self.digest.payload_type is Fingerprint
        ):
            raise TypeError("analysis input digest must contain a Fingerprint")
        if type(self.bytes) is not ByteCount:
            raise TypeError("analysis input bytes must be a ByteCount")
        if type(self.sensitivity) is not Sensitivity:
            raise TypeError("analysis input sensitivity must be a Sensitivity")
        digest_is_sensitive = isinstance(self.digest, SensitiveValue)
        if digest_is_sensitive != (self.sensitivity is Sensitivity.SENSITIVE):
            raise ValueError("analysis input digest containment must match input sensitivity")
        if self.media_type is not None and (
            type(self.media_type) is not str or not self.media_type.strip()
        ):
            raise ValueError("analysis input media type must be absent or non-blank text")


@final
class AnalysisRequest(msgspec.Struct, frozen=True, kw_only=True):
    """An immutable analysis recipe with explicit authority, cost, and timeout bounds."""

    analysis_id: AnalysisId
    provider: AnalysisProvider
    inputs: tuple[AnalysisInput, ...]
    authorization: CapabilityGranted
    budgets: tuple[ResourceBudget, ...]
    timeout: Duration
    fingerprint: Fingerprint

    def __post_init__(self) -> None:
        """Bind the request to matching external-analysis authority and finite bounds."""
        if type(self.analysis_id) is not AnalysisId:
            raise TypeError("analysis request requires an AnalysisId")
        if type(self.provider) is not AnalysisProvider:
            raise TypeError("analysis request requires an AnalysisProvider")
        _validate_inputs(self.inputs)
        _validate_authorization(self.authorization, self.provider)
        _validate_budgets(self.budgets)
        if type(self.timeout) is not Duration:
            raise TypeError("analysis timeout must be a Duration")
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("analysis fingerprint must be a Fingerprint")


def _validate_inputs(inputs: tuple[AnalysisInput, ...]) -> None:
    if type(inputs) is not tuple:
        raise TypeError("analysis inputs must be an immutable tuple")
    if not inputs:
        raise ValueError("analysis request requires at least one input")
    if any(type(item) is not AnalysisInput for item in inputs):
        raise TypeError("analysis inputs must contain AnalysisInput values")


def _validate_authorization(
    authorization: CapabilityGranted,
    provider: AnalysisProvider,
) -> None:
    if type(authorization) is not CapabilityGranted:
        raise TypeError("analysis authorization must be a CapabilityGranted")
    if authorization.request.category is not CapabilityCategory.EXTERNAL_ANALYSIS:
        raise ValueError("analysis authorization must grant external analysis")
    if authorization.request.target != provider.destination:
        raise ValueError("analysis authorization target must match the provider destination")


def _validate_budgets(budgets: tuple[ResourceBudget, ...]) -> None:
    if type(budgets) is not tuple:
        raise TypeError("analysis budgets must be an immutable tuple")
    if not budgets:
        raise ValueError("analysis request requires finite external-capability budgets")
    if any(type(budget) is not ResourceBudget for budget in budgets):
        raise TypeError("analysis budgets must contain ResourceBudget values")
    if any(budget.stage is not BudgetStage.EXTERNAL_CAPABILITY for budget in budgets):
        raise ValueError("analysis budgets must belong to the external-capability stage")


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
class AnalysisDenied(msgspec.Struct, frozen=True, kw_only=True):
    """External analysis was denied before provider invocation."""

    decision: CapabilityDenied

    def __post_init__(self) -> None:
        """Require a matching external-analysis denial."""
        if type(self.decision) is not CapabilityDenied:
            raise TypeError("analysis denial requires a CapabilityDenied")
        if self.decision.request.category is not CapabilityCategory.EXTERNAL_ANALYSIS:
            raise ValueError("analysis denial must concern external analysis")


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


type AnalysisResult = (
    AnalysisSucceeded
    | AnalysisDenied
    | AnalysisTimedOut
    | AnalysisProviderFailed
    | AnalysisMalformed
    | AnalysisCancelled
    | AnalysisBudgetExhausted
)


class AnalysisExecutor(Protocol):
    """Narrow runtime port for an authorized, bounded external analysis."""

    def execute(self, request: AnalysisRequest) -> AnalysisResult:
        """Execute one already authorized request and return a typed terminal result."""
        ...


def _require_error_diagnostic(diagnostic: Diagnostic, *, label: str) -> None:
    if type(diagnostic) is not Diagnostic:
        raise TypeError(f"{label} requires a Diagnostic")
    if diagnostic.severity is not DiagnosticSeverity.ERROR:
        raise ValueError(f"{label} diagnostic must have error severity")
