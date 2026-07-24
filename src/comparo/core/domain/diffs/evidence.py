"""Directed value provenance, concrete facts, and bounded evidence."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import require_operation_family
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.sensitivity import TaintOriginId


class DiffFactOutcome(DomainStrEnum):
    """Concrete directed field facts."""

    SAME = "same"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"
    IGNORED = "ignored"
    INCOMPARABLE = "incomparable"


@final
class DiffValueProvenance(msgspec.Struct, frozen=True, kw_only=True):
    """Side-specific source, operation, and sensitivity lineage for one derived value."""

    selector: CompiledSelector
    locations: tuple[ConcreteLocation, ...]
    operations: tuple[DiffOperationIdentity, ...]
    taint_origins: tuple[TaintOriginId, ...]
    empty_reducer_identity: bool = False

    def __post_init__(self) -> None:
        """Allow originless values only for an explicit reducer identity."""
        if type(self.selector) is not CompiledSelector:
            raise TypeError("diff value provenance requires a CompiledSelector")
        if type(self.locations) is not tuple or any(
            not is_concrete_location(location) for location in self.locations
        ):
            raise TypeError("diff provenance locations must be concrete locations")
        if len(set(self.locations)) != len(self.locations):
            raise ValueError("diff provenance locations must be unique")
        if type(self.operations) is not tuple or any(
            type(operation) is not DiffOperationIdentity for operation in self.operations
        ):
            raise TypeError("diff provenance operations must be immutable and typed")
        if type(self.taint_origins) is not tuple or any(
            type(origin) is not TaintOriginId for origin in self.taint_origins
        ):
            raise TypeError("diff provenance taint origins must be immutable and typed")
        if len(set(self.taint_origins)) != len(self.taint_origins):
            raise ValueError("diff provenance taint origins must be unique")
        if type(self.empty_reducer_identity) is not bool:
            raise TypeError("empty reducer identity marker must be bool")
        if bool(self.locations) == self.empty_reducer_identity:
            raise ValueError(
                "exactly originless diff values require an empty reducer identity marker"
            )
        if self.empty_reducer_identity and (
            not self.operations or self.operations[-1].family is not DiffOperationFamily.REDUCER
        ):
            raise ValueError("originless diff values require a terminal reducer operation")


@final
class DiffFact(msgspec.Struct, frozen=True, kw_only=True):
    """One directed field fact with independent side provenance."""

    fact_id: FactId
    outcome: DiffFactOutcome
    comparator: DiffOperationIdentity
    baseline: DiffValueProvenance | None = None
    candidate: DiffValueProvenance | None = None
    diagnostic: DiagnosticCode | None = None
    dependency_taint_origins: tuple[TaintOriginId, ...] = ()

    def __post_init__(self) -> None:
        """Enforce added/removed direction and incomparable diagnostics."""
        if type(self.fact_id) is not FactId or type(self.outcome) is not DiffFactOutcome:
            raise TypeError("diff fact requires typed ID and outcome")
        require_operation_family(self.comparator, DiffOperationFamily.COMPARATOR)
        for provenance in (self.baseline, self.candidate):
            if provenance is not None and type(provenance) is not DiffValueProvenance:
                raise TypeError("diff fact side provenance has an unsupported type")
        if self.diagnostic is not None and type(self.diagnostic) is not DiagnosticCode:
            raise TypeError("diff fact diagnostic must be a DiagnosticCode")
        _require_dependency_taint(self.dependency_taint_origins)
        baseline = self.baseline is not None
        candidate = self.candidate is not None
        if self.outcome is DiffFactOutcome.ADDED and (baseline or not candidate):
            raise ValueError("added fact requires only a candidate location")
        if self.outcome is DiffFactOutcome.REMOVED and (not baseline or candidate):
            raise ValueError("removed fact requires only a baseline location")
        if self.outcome in {DiffFactOutcome.SAME, DiffFactOutcome.CHANGED} and (
            not baseline or not candidate
        ):
            raise ValueError("same and changed facts require both locations")
        if (self.outcome is DiffFactOutcome.INCOMPARABLE) != (self.diagnostic is not None):
            raise ValueError("exactly incomparable facts require diagnostics")
        if self.outcome in {DiffFactOutcome.IGNORED, DiffFactOutcome.INCOMPARABLE} and not (
            baseline or candidate
        ):
            raise ValueError("ignored or incomparable fact requires at least one side provenance")


def _require_dependency_taint(origins: tuple[TaintOriginId, ...]) -> None:
    """Require safe, unique identities without carrying dependency payloads."""
    if type(origins) is not tuple or any(type(origin) is not TaintOriginId for origin in origins):
        raise TypeError("diff dependency taint origins must be immutable and typed")
    if len(set(origins)) != len(origins):
        raise ValueError("diff dependency taint origins must be unique")


@final
class DiffEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """Bounded evidence whose omission never changes exact counts."""

    retained: tuple[DiffFact, ...]
    omitted: Count
    exhaustion: BudgetExceeded | None = None

    def __post_init__(self) -> None:
        """Require immutable typed facts and exact omission count."""
        if type(self.retained) is not tuple:
            raise TypeError("diff evidence must be an immutable tuple")
        if any(type(fact) is not DiffFact for fact in self.retained):
            raise TypeError("diff evidence contains an unsupported fact")
        if type(self.omitted) is not Count:
            raise TypeError("omitted evidence must be a Count")
        if self.exhaustion is not None and type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("diff evidence exhaustion must be BudgetExceeded")
        if self.exhaustion is not None and self.exhaustion.budget.stage is not BudgetStage.EVIDENCE:
            raise ValueError("diff evidence exhaustion requires evidence-stage exhaustion")
        if (self.omitted.value > 0) != (self.exhaustion is not None):
            raise ValueError("diff evidence exhaustion must exist exactly when facts were omitted")
        if self.exhaustion is not None and (
            self.exhaustion.budget.resource
            not in {BudgetResource.EVIDENCE_EXAMPLES, BudgetResource.EVIDENCE_BYTES}
            or self.exhaustion.effect is not BudgetExhaustionEffect.EVIDENCE_TRUNCATED
        ):
            raise ValueError("diff evidence requires exact evidence budget exhaustion")
        fact_ids = tuple(fact.fact_id for fact in self.retained)
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("diff evidence fact IDs must be unique")


__all__ = ("DiffEvidence", "DiffFact", "DiffFactOutcome", "DiffValueProvenance")
