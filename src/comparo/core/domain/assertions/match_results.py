"""Per-match assertion outcomes and independently bounded retained evidence."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.assertions.evidence import AssertionOperatorEvidenceValue
from comparo.core.domain.assertions.evidence import is_assertion_operator_evidence
from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionMatch


class MatchOutcome(DomainStrEnum):
    """Concrete predicate outcomes for one selected value."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    ERROR = "error"


@final
class MatchFact(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded child fact at a concrete selector location."""

    fact_id: FactId
    match: SelectionMatch
    outcome: MatchOutcome
    diagnostic: DiagnosticCode | None = None
    operator_evidence: AssertionOperatorEvidenceValue | None = None

    def __post_init__(self) -> None:
        """Require diagnostics exactly for evaluator errors."""
        if type(self.fact_id) is not FactId or type(self.match) is not SelectionMatch:
            raise TypeError("match fact requires a FactId and SelectionMatch")
        if type(self.outcome) is not MatchOutcome:
            raise TypeError("match outcome must be a MatchOutcome")
        if self.diagnostic is not None and type(self.diagnostic) is not DiagnosticCode:
            raise TypeError("match diagnostic must be a DiagnosticCode")
        if (self.outcome is MatchOutcome.ERROR) != (self.diagnostic is not None):
            raise ValueError("exactly evaluator-error match facts require diagnostics")
        if self.operator_evidence is not None and not is_assertion_operator_evidence(
            self.operator_evidence
        ):
            raise TypeError("match operator evidence has an unsupported type")
        if self.outcome is MatchOutcome.ERROR and self.operator_evidence is not None:
            raise ValueError("evaluator-error match facts cannot carry operator evidence")


@final
class AssertionEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """Bounded evidence whose omission never changes logical counts."""

    retained: tuple[MatchFact, ...]
    omitted: Count
    truncation: BudgetExceeded | None = None

    def __post_init__(self) -> None:
        """Require immutable typed evidence and exact omission count."""
        if type(self.retained) is not tuple:
            raise TypeError("assertion evidence must be an immutable tuple")
        if any(type(fact) is not MatchFact for fact in self.retained):
            raise TypeError("assertion evidence contains an unsupported fact")
        if type(self.omitted) is not Count:
            raise TypeError("omitted evidence must be a Count")
        if self.truncation is not None and type(self.truncation) is not BudgetExceeded:
            raise TypeError("evidence truncation must be BudgetExceeded")
        if self.truncation is not None and self.truncation.budget.stage is not BudgetStage.EVIDENCE:
            raise ValueError("evidence truncation requires evidence-stage exhaustion")
        if (self.omitted.value > 0) != (self.truncation is not None):
            raise ValueError("evidence truncation must exist exactly when facts were omitted")
        if self.truncation is not None and (
            self.truncation.budget.resource
            not in {BudgetResource.EVIDENCE_EXAMPLES, BudgetResource.EVIDENCE_BYTES}
            or self.truncation.effect is not BudgetExhaustionEffect.EVIDENCE_TRUNCATED
        ):
            raise ValueError("evidence truncation requires exact evidence budget exhaustion")
        fact_ids = tuple(fact.fact_id for fact in self.retained)
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("assertion evidence fact IDs must be unique")
