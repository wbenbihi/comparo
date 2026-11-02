"""Compiled assertion rules, applicability, and runtime application identity."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.assertions.contracts import require_assertion_operator_contract
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import operator_uses_match_predicate
from comparo.core.domain.assertions.operators import require_operator
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.sources import SelectorSource


class Severity(DomainStrEnum):
    """Rule importance, independent of logical outcome and gate policy."""

    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class Quantifier(DomainStrEnum):
    """Explicit selector-match aggregation semantics."""

    ONE = "one"
    ALL = "all"
    ANY = "any"
    NONE = "none"


class CompletenessRequirement(DomainStrEnum):
    """The minimum source completeness admitted by an operator."""

    COMPLETE = "complete"
    CLOSED_WINDOW = "closed-window"
    PARTIAL_PREFIX = "partial-prefix"


@final
class PredicateCondition(msgspec.Struct, frozen=True, kw_only=True):
    """One pure selector/operator applicability predicate."""

    selector: CompiledSelector
    operator_id: AssertionOperatorIdentity
    operator: AssertionOperator
    quantifier: Quantifier

    def __post_init__(self) -> None:
        """Require a typed selector, matching operator, and explicit quantifier."""
        if type(self.selector) is not CompiledSelector:
            raise TypeError("condition selector must be a CompiledSelector")
        require_operator(self.operator_id, self.operator)
        require_assertion_operator_contract(self.selector.output, self.operator)
        if type(self.operator) is DecodeOperator:
            _require_decode_selector(self.selector, self.operator)
        if not operator_uses_match_predicate(self.operator):
            raise ValueError("applicability conditions require a match predicate operator")
        if type(self.quantifier) is not Quantifier:
            raise TypeError("condition quantifier must be a Quantifier")


@final
class AllConditions(msgspec.Struct, frozen=True, kw_only=True):
    """A non-empty pure conjunction."""

    conditions: tuple[ApplicabilityCondition, ...]

    def __post_init__(self) -> None:
        """Require immutable typed children."""
        _require_conditions(self.conditions)


@final
class AnyConditions(msgspec.Struct, frozen=True, kw_only=True):
    """A non-empty pure disjunction."""

    conditions: tuple[ApplicabilityCondition, ...]

    def __post_init__(self) -> None:
        """Require immutable typed children."""
        _require_conditions(self.conditions)


type ApplicabilityCondition = PredicateCondition | AllConditions | AnyConditions


def is_applicability_condition(value: object) -> TypeGuard[ApplicabilityCondition]:
    """Narrow to the structured condition union."""
    return type(value) in {PredicateCondition, AllConditions, AnyConditions}


@final
class AssertionRuleDefinition(msgspec.Struct, frozen=True, kw_only=True):
    """Stable rule identity in a profile or inline executable container."""

    origin: DefinitionRef | InlineDefinitionOrigin
    rule_id: RuleId

    def __post_init__(self) -> None:
        """Require an AssertionProfile for reusable definitions."""
        if type(self.origin) not in {DefinitionRef, InlineDefinitionOrigin}:
            raise TypeError("assertion rule origin has an unsupported type")
        if (
            type(self.origin) is DefinitionRef
            and self.origin.identity.kind is not ResourceKind.ASSERTION_PROFILE
        ):
            raise ValueError("reusable assertion rule must belong to an AssertionProfile")
        if type(self.rule_id) is not RuleId:
            raise TypeError("assertion rule ID must be a RuleId")


@final
class CompiledAssertionRule(msgspec.Struct, frozen=True, kw_only=True):
    """A statically valid effective rule ready for pure evaluation."""

    definition: AssertionRuleDefinition
    effective_id: EffectiveRuleId
    fingerprint: Fingerprint
    metadata: Metadata
    severity: Severity
    selector: CompiledSelector
    operator_id: AssertionOperatorIdentity
    operator: AssertionOperator
    quantifier: Quantifier | None
    completeness: CompletenessRequirement
    applicability: ApplicabilityCondition | None = None

    def __post_init__(self) -> None:
        """Reject structurally invalid compiled rule components."""
        if type(self.definition) is not AssertionRuleDefinition:
            raise TypeError("compiled assertion definition must be an AssertionRuleDefinition")
        if type(self.effective_id) is not EffectiveRuleId:
            raise TypeError("compiled assertion effective ID must be an EffectiveRuleId")
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("compiled assertion fingerprint must be a Fingerprint")
        if type(self.metadata) is not Metadata or type(self.severity) is not Severity:
            raise TypeError("compiled assertion requires typed metadata and severity")
        if type(self.selector) is not CompiledSelector:
            raise TypeError("compiled assertion selector must be a CompiledSelector")
        require_operator(self.operator_id, self.operator)
        require_assertion_operator_contract(self.selector.output, self.operator)
        if type(self.operator) is DecodeOperator:
            _require_decode_selector(self.selector, self.operator)
        _require_quantifier(self.operator, self.quantifier)
        if type(self.completeness) is not CompletenessRequirement:
            raise TypeError("compiled assertion completeness has an unsupported type")
        if self.applicability is not None and not is_applicability_condition(self.applicability):
            raise TypeError("compiled assertion applicability has an unsupported type")


@final
class AssertionApplication(msgspec.Struct, frozen=True, kw_only=True):
    """One stable effective rule application in one Environment execution."""

    application_id: RuleApplicationId
    definition: AssertionRuleDefinition
    effective_id: EffectiveRuleId
    rule_fingerprint: Fingerprint
    environment: DefinitionRef
    environment_execution_id: EnvironmentExecutionId

    def __post_init__(self) -> None:
        """Require definition, effective, and runtime identities."""
        if type(self.application_id) is not RuleApplicationId:
            raise TypeError("application ID must be a RuleApplicationId")
        if type(self.definition) is not AssertionRuleDefinition:
            raise TypeError("application definition must be an AssertionRuleDefinition")
        if type(self.effective_id) is not EffectiveRuleId:
            raise TypeError("application effective ID must be an EffectiveRuleId")
        if type(self.rule_fingerprint) is not Fingerprint:
            raise TypeError("application fingerprint must be a Fingerprint")
        if type(self.environment) is not DefinitionRef:
            raise TypeError("application environment must be a DefinitionRef")
        if self.environment.identity.kind is not ResourceKind.ENVIRONMENT:
            raise ValueError("application environment must reference an Environment")
        if type(self.environment_execution_id) is not EnvironmentExecutionId:
            raise TypeError("application requires an EnvironmentExecutionId")


def _require_conditions(conditions: tuple[ApplicabilityCondition, ...]) -> None:
    if type(conditions) is not tuple:
        raise TypeError("conditions must be an immutable tuple")
    if not conditions:
        raise ValueError("conditions must not be empty")
    if any(not is_applicability_condition(condition) for condition in conditions):
        raise TypeError("conditions contain an unsupported value")


def _require_decode_selector(selector: CompiledSelector, operator: DecodeOperator) -> None:
    """Bind decode state checks to the exact requested whole-body view."""
    if selector.source not in {SelectorSource.REQUEST_BODY, SelectorSource.RESPONSE_BODY}:
        raise ValueError("decode assertion requires a request or response body selector")
    if type(selector.path) is not NoPath:
        raise ValueError("decode assertion requires a whole-body selector")
    if selector.body_view is not operator.view:
        raise ValueError("decode assertion view must match the selector body view")


def _require_quantifier(
    operator: AssertionOperator,
    quantifier: Quantifier | None,
) -> None:
    uses_predicate = operator_uses_match_predicate(operator)
    if uses_predicate and type(quantifier) is not Quantifier:
        raise TypeError("predicate assertion operators require an explicit quantifier")
    if not uses_predicate and quantifier is not None:
        raise ValueError("predicate-free assertion operators cannot declare a quantifier")
