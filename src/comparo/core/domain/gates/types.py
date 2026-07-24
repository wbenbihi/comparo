"""Closed gate dimensions and exact source-lineage coordinates."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.primitives import Fingerprint


class GateSeverity(DomainStrEnum):
    """Severity dimensions normalized from exact compiled rules."""

    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class EnvironmentRole(DomainStrEnum):
    """Invocation-owned transient roles; Environment resources remain role-neutral."""

    BASELINE = "baseline"
    CANDIDATE = "candidate"
    OTHER = "other"


@final
class GateRuleLineage(msgspec.Struct, frozen=True, kw_only=True):
    """Stable source fact and exact effective rule coordinates."""

    source_fact_id: FactId
    application_id: RuleApplicationId
    rule_id: RuleId
    effective_id: EffectiveRuleId
    rule_fingerprint: Fingerprint
    severity: GateSeverity

    def __post_init__(self) -> None:
        """Require every non-reclassifiable source coordinate explicitly."""
        if type(self.source_fact_id) is not FactId:
            raise TypeError("gate lineage source fact ID must be a FactId")
        if type(self.application_id) is not RuleApplicationId:
            raise TypeError("gate lineage application ID must be a RuleApplicationId")
        if type(self.rule_id) is not RuleId:
            raise TypeError("gate lineage rule ID must be a RuleId")
        if type(self.effective_id) is not EffectiveRuleId:
            raise TypeError("gate lineage effective ID must be an EffectiveRuleId")
        if type(self.rule_fingerprint) is not Fingerprint:
            raise TypeError("gate lineage fingerprint must be a Fingerprint")
        if type(self.severity) is not GateSeverity:
            raise TypeError("gate lineage severity must be a GateSeverity")


class AssertionGateOutcome(DomainStrEnum):
    """Value-free terminal assertion outcomes retained per exact rule."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class DiffGateOutcome(DomainStrEnum):
    """Value-free terminal diff outcomes retained per exact rule."""

    EQUIVALENT = "equivalent"
    DRIFTED = "drifted"
    INCOMPLETE = "incomplete"
    ERROR = "error"
    NOT_EVALUATED = "not-evaluated"
