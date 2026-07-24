"""Finite typed resource budgets with complete derivation and usage evidence."""

from __future__ import annotations

import re
from enum import StrEnum
from fractions import Fraction
from typing import final

import msgspec

from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope

_OPERATION_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")

type BudgetAmount = Count | ByteCount | Duration | Ratio


class BudgetResource(StrEnum):
    """Independently configurable resource dimensions."""

    REQUESTS = "requests"
    MATRIX_CELLS = "matrix-cells"
    TRIALS = "trials"
    SCENARIO_STEPS = "scenario-steps"
    ENVIRONMENT_EXECUTIONS = "environment-executions"
    ATTEMPTS = "attempts"
    COMPARISONS = "comparisons"
    QUEUED_WORK = "queued-work"
    CONCURRENCY = "concurrency"
    OPEN_CONNECTIONS = "open-connections"
    REQUEST_BYTES = "request-bytes"
    HEADER_BYTES = "header-bytes"
    HEADER_COUNT = "header-count"
    ENCODED_BODY_BYTES = "encoded-body-bytes"
    ENTITY_BODY_BYTES = "entity-body-bytes"
    DECOMPRESSION_RATIO = "decompression-ratio"
    REDIRECTS = "redirects"
    RETRIES = "retries"
    DEADLINE = "deadline"
    STREAM_RECONNECTS = "stream-reconnects"
    STREAM_EVENTS = "stream-events"
    STREAM_BYTES = "stream-bytes"
    STREAM_DURATION = "stream-duration"
    STREAM_IDLE_DURATION = "stream-idle-duration"
    DECODED_CHARACTERS = "decoded-characters"
    JSON_DEPTH = "json-depth"
    JSON_NODES = "json-nodes"
    JSON_TOKENS = "json-tokens"
    VIEW_INDEX_BYTES = "view-index-bytes"
    SELECTION_WORK = "selection-work"
    SELECTION_MATCHES = "selection-matches"
    SELECTED_RESULT_BYTES = "selected-result-bytes"
    DERIVED_VALUES = "derived-values"
    DERIVED_BYTES = "derived-bytes"
    TRANSFORM_WORK = "transform-work"
    PAIRS = "pairs"
    FACTS = "facts"
    TRIAL_SAMPLES = "trial-samples"
    STATISTICAL_WORK = "statistical-work"
    EVIDENCE_EXAMPLES = "evidence-examples"
    EVIDENCE_BYTES = "evidence-bytes"
    MEMORY_BYTES = "memory-bytes"
    TRANSIENT_STORAGE_BYTES = "transient-storage-bytes"
    STORE_QUOTA_BYTES = "store-quota-bytes"
    ARTIFACT_BODY_BYTES = "artifact-body-bytes"
    ARTIFACT_BLOBS = "artifact-blobs"
    ARTIFACT_BYTES = "artifact-bytes"
    EXTERNAL_INPUT_BYTES = "external-input-bytes"
    EXTERNAL_OUTPUT_BYTES = "external-output-bytes"
    EXTERNAL_WORK = "external-work"


class BudgetScopeKind(StrEnum):
    """The accounting boundary for a resource budget."""

    PLAN = "plan"
    RUN = "run"
    CELL = "cell"
    TRIAL = "trial"
    ENVIRONMENT = "environment"
    EXECUTION = "execution"
    STEP = "step"
    ATTEMPT = "attempt"
    BODY = "body"
    STREAM = "stream"
    COMPARISON = "comparison"
    ARTIFACT = "artifact"
    EXTERNAL_CAPABILITY = "external-capability"


class BudgetStage(StrEnum):
    """The stage responsible for enforcing and reporting a budget."""

    PLANNING = "planning"
    SCHEDULING = "scheduling"
    HTTP = "http"
    CAPTURE = "capture"
    STREAM = "stream"
    DECODING = "decoding"
    SELECTION = "selection"
    EVALUATION = "evaluation"
    EVIDENCE = "evidence"
    STORAGE = "storage"
    ARTIFACT = "artifact"
    EXTERNAL_CAPABILITY = "external-capability"


@final
class BudgetOperation(msgspec.Struct, frozen=True, kw_only=True):
    """A stable operation identity at the exact enforcement point."""

    value: str

    def __post_init__(self) -> None:
        """Require a deterministic, machine-readable operation name."""
        if type(self.value) is not str or _OPERATION_PATTERN.fullmatch(self.value) is None:
            raise ValueError("budget operation must be a lowercase dotted token")


class BudgetContributionKind(StrEnum):
    """How one configuration origin participates in budget derivation."""

    DEFAULT = "default"
    REQUEST = "request"
    CEILING = "ceiling"


@final
class BudgetContribution(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free provenance entry paired with its exact budget amount."""

    kind: BudgetContributionKind
    amount: BudgetAmount
    provenance: ProvenanceEntry

    def __post_init__(self) -> None:
        """Require typed contribution components."""
        if type(self.kind) is not BudgetContributionKind:
            raise TypeError("budget contribution kind must be a BudgetContributionKind")
        if not _is_budget_amount(self.amount):
            raise TypeError("budget contribution amount must be a supported exact amount")
        if type(self.provenance) is not ProvenanceEntry:
            raise TypeError("budget contribution provenance must be a ProvenanceEntry")


@final
class BudgetUsage(msgspec.Struct, frozen=True, kw_only=True):
    """Actual cumulative use, live reservation, and simultaneous high-water mark."""

    consumed: BudgetAmount
    in_use: BudgetAmount
    high_water: BudgetAmount

    def __post_init__(self) -> None:
        """Require one exact unit type for all usage measurements."""
        for amount in (self.consumed, self.in_use, self.high_water):
            if not _is_budget_amount(amount):
                raise TypeError("budget usage values must be supported exact amounts")
        _require_same_amount_type(self.consumed, self.in_use, self.high_water)


@final
class ResourceBudget(msgspec.Struct, frozen=True, kw_only=True):
    """One finite effective budget with its full request and ceiling derivation."""

    resource: BudgetResource
    scope_kind: BudgetScopeKind
    stage: BudgetStage
    operation: BudgetOperation
    requested: BudgetAmount
    effective: BudgetAmount
    cap: BudgetAmount | None
    contributions: tuple[BudgetContribution, ...]
    usage: BudgetUsage
    scope: RuntimeScope | None = None

    def __post_init__(self) -> None:
        """Validate units, derivation evidence, and effective minimum semantics."""
        _validate_budget_identity(self)
        _validate_budget_derivation(self)


def _validate_budget_identity(budget: ResourceBudget) -> None:
    """Validate the typed resource, enforcement point, usage, and runtime scope."""
    if type(budget.resource) is not BudgetResource:
        raise TypeError("budget resource must be a BudgetResource")
    if type(budget.scope_kind) is not BudgetScopeKind:
        raise TypeError("budget scope kind must be a BudgetScopeKind")
    if type(budget.stage) is not BudgetStage:
        raise TypeError("budget stage must be a BudgetStage")
    if type(budget.operation) is not BudgetOperation:
        raise TypeError("budget operation must be a BudgetOperation")
    if type(budget.usage) is not BudgetUsage:
        raise TypeError("budget usage must be a BudgetUsage")
    if budget.scope is not None and type(budget.scope) not in {
        RunScope,
        CellScope,
        TrialScope,
        EnvironmentScope,
        ExecutionScope,
        StepScope,
        AttemptScope,
    }:
        raise TypeError("budget scope must be a supported RuntimeScope")


def _validate_budget_derivation(budget: ResourceBudget) -> None:
    """Validate exact units and recompute requested, cap, and effective values."""
    _validate_budget_inputs(budget)
    ordinary = tuple(
        item for item in budget.contributions if item.kind is not BudgetContributionKind.CEILING
    )
    ceilings = tuple(
        item for item in budget.contributions if item.kind is BudgetContributionKind.CEILING
    )
    _validate_ordinary_contributions(budget.requested, ordinary)
    expected_cap = _minimum_amount(tuple(item.amount for item in ceilings))
    if budget.cap != expected_cap:
        raise ValueError("budget cap must equal the lowest ceiling contribution")
    expected_effective = (
        budget.requested
        if expected_cap is None or _amount_less_equal(budget.requested, expected_cap)
        else expected_cap
    )
    if budget.effective != expected_effective:
        raise ValueError("effective budget must equal the requested value constrained by its cap")


def _validate_budget_inputs(budget: ResourceBudget) -> None:
    """Validate the closed amount union, immutable evidence, and consistent units."""
    if not _is_budget_amount(budget.requested) or not _is_budget_amount(budget.effective):
        raise TypeError("requested and effective budgets must be supported exact amounts")
    if budget.cap is not None and not _is_budget_amount(budget.cap):
        raise TypeError("budget cap must be a supported exact amount")
    if type(budget.contributions) is not tuple:
        raise TypeError("budget contributions must be an immutable tuple")
    if not budget.contributions:
        raise ValueError("budget contributions must not be empty")
    if any(type(item) is not BudgetContribution for item in budget.contributions):
        raise TypeError("budget contributions must contain BudgetContribution values")

    amounts = tuple(item.amount for item in budget.contributions)
    optional_cap: tuple[BudgetAmount, ...] = () if budget.cap is None else (budget.cap,)
    _require_same_amount_type(
        budget.requested,
        budget.effective,
        budget.usage.consumed,
        budget.usage.in_use,
        budget.usage.high_water,
        *optional_cap,
        *amounts,
    )


def _validate_ordinary_contributions(
    requested: BudgetAmount,
    ordinary: tuple[BudgetContribution, ...],
) -> None:
    """Require one initial default followed by zero or more ordinary overrides."""
    if not ordinary:
        raise ValueError("budget derivation requires a default or requested value")
    if ordinary[0].kind is not BudgetContributionKind.DEFAULT:
        raise ValueError("budget derivation must begin with a versioned default")
    if any(item.kind is BudgetContributionKind.DEFAULT for item in ordinary[1:]):
        raise ValueError("budget derivation may contain only one default")
    if ordinary[-1].amount != requested:
        raise ValueError("requested budget must equal the last ordinary contribution")


class BudgetExhaustionEffect(StrEnum):
    """The typed consequence of exhaustion at the responsible stage."""

    PLAN_REJECTED = "plan-rejected"
    OBSERVATION_PARTIAL = "observation-partial"
    OBSERVATION_UNAVAILABLE = "observation-unavailable"
    EVALUATION_INCOMPLETE = "evaluation-incomplete"
    EVIDENCE_TRUNCATED = "evidence-truncated"
    ARTIFACT_INCOMPLETE = "artifact-incomplete"
    ARTIFACT_REJECTED = "artifact-rejected"
    EXTERNAL_CAPABILITY_UNAVAILABLE = "external-capability-unavailable"


@final
class BudgetExceeded(msgspec.Struct, frozen=True, kw_only=True):
    """A typed exhaustion outcome that can never be mistaken for drift or success."""

    budget: ResourceBudget
    attempted: BudgetAmount
    effect: BudgetExhaustionEffect

    def __post_init__(self) -> None:
        """Require a same-unit amount that strictly exceeds the effective budget."""
        if type(self.budget) is not ResourceBudget:
            raise TypeError("budget exhaustion requires a ResourceBudget")
        if not _is_budget_amount(self.attempted):
            raise TypeError("attempted budget amount must be a supported exact amount")
        if type(self.effect) is not BudgetExhaustionEffect:
            raise TypeError("budget exhaustion effect must be a BudgetExhaustionEffect")
        _require_same_amount_type(self.budget.effective, self.attempted)
        if _amount_less_equal(self.attempted, self.budget.effective):
            raise ValueError("attempted amount must exceed the effective budget")


def _is_budget_amount(amount: object) -> bool:
    """Return whether an object is one of the closed exact budget amount types."""
    return type(amount) in {Count, ByteCount, Duration, Ratio}


def _amount_value(amount: BudgetAmount) -> Fraction:
    """Return an exact rational scalar without floating-point conversion."""
    if type(amount) is Duration:
        return Fraction(amount.nanoseconds)
    if type(amount) is Ratio:
        return Fraction(amount.numerator, amount.denominator.value)
    return Fraction(amount.value)


def _amount_less_equal(left: BudgetAmount, right: BudgetAmount) -> bool:
    """Compare two same-unit amounts without floating-point conversion."""
    _require_same_amount_type(left, right)
    return _amount_value(left) <= _amount_value(right)


def _minimum_amount(amounts: tuple[BudgetAmount, ...]) -> BudgetAmount | None:
    """Return the exact minimum of a same-unit tuple, or no cap."""
    if not amounts:
        return None
    result = amounts[0]
    for amount in amounts[1:]:
        if _amount_less_equal(amount, result):
            result = amount
    return result


def _require_same_amount_type(first: BudgetAmount, *remaining: BudgetAmount) -> None:
    """Reject attempts to compare or aggregate unlike budget units."""
    if any(type(amount) is not type(first) for amount in remaining):
        raise TypeError("budget amounts must use one exact unit type")
