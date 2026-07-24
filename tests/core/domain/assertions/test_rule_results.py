"""Cover every assertion operator and direct-construction outcome invariant."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

import comparo.core.domain.assertions.model as assertion_model
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import MatchFact
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Skipped
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import AssertionRuleDefinition
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.assertions.rules import PredicateCondition
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonInteger
from tests.core.domain.assertions._invariant_support import _application
from tests.core.domain.assertions._invariant_support import _compiled_rule
from tests.core.domain.assertions._invariant_support import _fingerprint
from tests.core.domain.assertions._invariant_support import _literal
from tests.core.domain.assertions._invariant_support import _rule_definition
from tests.core.domain.assertions._invariant_support import _selector


def _environment_definition() -> DefinitionRef:
    return DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="demo"),
            kind=ResourceKind.ENVIRONMENT,
            resource_id=ResourceId(value="prod"),
        ),
        fingerprint=_fingerprint(),
    )


def test_compiled_rule_retains_static_semantics_and_validates_components() -> None:
    definition = _rule_definition()
    operator = EqualityOperator(relation=EqualityRelation.EQUALS, expected=_literal(200))
    rule = CompiledAssertionRule(
        definition=definition,
        effective_id=EffectiveRuleId(value="status"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="HTTP status"),
        severity=Severity.BLOCKING,
        selector=_selector(),
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.EQUALITY,
            version="1",
        ),
        operator=operator,
        quantifier=Quantifier.ONE,
        completeness=CompletenessRequirement.COMPLETE,
    )
    assert rule.definition == definition
    assert assertion_model.CompiledAssertionRule is CompiledAssertionRule
    with pytest.raises(TypeError, match="definition"):
        msgspec.structs.replace(rule, definition=cast("AssertionRuleDefinition", "bad"))
    with pytest.raises(TypeError, match="effective ID"):
        msgspec.structs.replace(rule, effective_id=cast("EffectiveRuleId", "bad"))
    with pytest.raises(TypeError, match="fingerprint"):
        msgspec.structs.replace(rule, fingerprint=cast("Fingerprint", "bad"))
    with pytest.raises(TypeError, match="metadata and severity"):
        msgspec.structs.replace(rule, metadata=cast("Metadata", "bad"))
    with pytest.raises(TypeError, match="selector"):
        msgspec.structs.replace(rule, selector=cast("CompiledSelector", "bad"))
    with pytest.raises(TypeError, match="quantifier"):
        msgspec.structs.replace(rule, quantifier=cast("Quantifier", "bad"))
    with pytest.raises(TypeError, match="completeness"):
        msgspec.structs.replace(
            rule,
            completeness=cast("CompletenessRequirement", "bad"),
        )
    with pytest.raises(TypeError, match="applicability"):
        msgspec.structs.replace(rule, applicability=cast("PredicateCondition", "bad"))


def test_match_fact_diagnostic_is_exactly_for_errors() -> None:
    match = SelectionMatch(
        value=JsonInteger(value=200),
        location=ExchangeLocation(
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=50)),
            source=SelectorSource.RESPONSE_STATUS,
            exchange_id=ExchangeId(value=UUID(int=501)),
            field=StaticFieldPath(segments=("response", "status")),
        ),
    )
    code = DiagnosticCode(value="assertion.operator-error")
    error = MatchFact(
        fact_id=FactId(value=UUID(int=51)),
        match=match,
        outcome=MatchOutcome.ERROR,
        diagnostic=code,
    )
    assert error.diagnostic == code
    with pytest.raises(ValueError, match="evaluator-error"):
        MatchFact(
            fact_id=FactId(value=UUID(int=52)),
            match=match,
            outcome=MatchOutcome.SATISFIED,
            diagnostic=code,
        )


def test_outcome_variants_and_empty_evidence_are_not_collapsed() -> None:
    code = DiagnosticCode(value="assertion.unavailable")
    outcomes = (
        Violated(reason=ViolationReason.CARDINALITY),
        Skipped(),
        AssertionUnavailable(code=code),
        AssertionError(code=code),
    )
    assert len({type(outcome) for outcome in outcomes}) == len(outcomes)
    evidence = AssertionEvidence(retained=(), omitted=Count(value=0))
    assert evidence.retained == ()
    with pytest.raises(TypeError):
        AssertionEvidence(retained=cast("tuple[MatchFact, ...]", []), omitted=Count(value=0))


def test_assertion_result_direct_construction_rejects_invalid_states() -> None:
    match = SelectionMatch(
        value=JsonInteger(value=200),
        location=ExchangeLocation(
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=54)),
            source=SelectorSource.RESPONSE_STATUS,
            exchange_id=ExchangeId(value=UUID(int=541)),
            field=StaticFieldPath(segments=("response", "status")),
        ),
    )
    with pytest.raises(TypeError, match="FactId"):
        MatchFact(
            fact_id=cast("FactId", "bad"),
            match=match,
            outcome=MatchOutcome.SATISFIED,
        )
    with pytest.raises(TypeError, match="MatchOutcome"):
        MatchFact(
            fact_id=FactId(value=UUID(int=55)),
            match=match,
            outcome=cast("MatchOutcome", "satisfied"),
        )
    with pytest.raises(TypeError, match="DiagnosticCode"):
        MatchFact(
            fact_id=FactId(value=UUID(int=56)),
            match=match,
            outcome=MatchOutcome.ERROR,
            diagnostic=cast("DiagnosticCode", "bad"),
        )
    with pytest.raises(ValueError, match="evaluator-error"):
        MatchFact(
            fact_id=FactId(value=UUID(int=57)),
            match=match,
            outcome=MatchOutcome.ERROR,
        )
    with pytest.raises(TypeError, match="Count"):
        AssertionCounts(
            selected=cast("Count", 1),
            evaluated=Count(value=0),
            satisfied=Count(value=0),
            violated=Count(value=0),
            errors=Count(value=0),
        )
    with pytest.raises(ValueError, match="exceed"):
        AssertionCounts(
            selected=Count(value=0),
            evaluated=Count(value=1),
            satisfied=Count(value=1),
            violated=Count(value=0),
            errors=Count(value=0),
        )
    with pytest.raises(TypeError, match="unsupported fact"):
        AssertionEvidence(
            retained=(cast("MatchFact", "bad"),),
            omitted=Count(value=0),
        )
    with pytest.raises(TypeError, match="omitted"):
        AssertionEvidence(retained=(), omitted=cast("Count", 0))
    with pytest.raises(TypeError, match="ViolationReason"):
        Violated(reason=cast("ViolationReason", "predicate"))
    with pytest.raises(TypeError, match="DiagnosticCode"):
        AssertionUnavailable(code=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError, match="DiagnosticCode"):
        AssertionError(code=cast("DiagnosticCode", "bad"))


def test_assertion_fact_rejects_invalid_identity_coverage_and_skip_evaluation() -> None:
    counts = AssertionCounts(
        selected=Count(value=1),
        evaluated=Count(value=1),
        satisfied=Count(value=1),
        violated=Count(value=0),
        errors=Count(value=0),
    )
    evidence = AssertionEvidence(retained=(), omitted=Count(value=1))
    with pytest.raises(TypeError, match="FactId"):
        AssertionFact(
            fact_id=cast("FactId", "bad"),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Satisfied(),
            counts=counts,
            evidence=evidence,
        )
    valid = AssertionFact(
        fact_id=FactId(value=UUID(int=581)),
        application=_application(),
        rule=_compiled_rule(),
        outcome=Satisfied(),
        counts=counts,
        evidence=evidence,
    )
    with pytest.raises(TypeError, match="AssertionApplication"):
        msgspec.structs.replace(valid, application=cast("AssertionApplication", "bad"))
    with pytest.raises(TypeError, match="CompiledAssertionRule"):
        msgspec.structs.replace(valid, rule=cast("CompiledAssertionRule", "bad"))
    with pytest.raises(TypeError, match="counts and evidence"):
        msgspec.structs.replace(valid, counts=cast("AssertionCounts", "bad"))
    with pytest.raises(TypeError, match="outcome"):
        AssertionFact(
            fact_id=FactId(value=UUID(int=58)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=cast("Satisfied", "bad"),
            counts=counts,
            evidence=evidence,
        )
    with pytest.raises(ValueError, match="cover"):
        AssertionFact(
            fact_id=FactId(value=UUID(int=59)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Satisfied(),
            counts=AssertionCounts(
                selected=Count(value=1),
                evaluated=Count(value=1),
                satisfied=Count(value=1),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
            evidence=AssertionEvidence(retained=(), omitted=Count(value=0)),
        )
    with pytest.raises(ValueError, match="cannot contain"):
        AssertionFact(
            fact_id=FactId(value=UUID(int=60)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Skipped(),
            counts=AssertionCounts(
                selected=Count(value=1),
                evaluated=Count(value=1),
                satisfied=Count(value=1),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
            evidence=AssertionEvidence(retained=(), omitted=Count(value=1)),
        )


def test_application_rejects_untyped_runtime_identity() -> None:
    definition = AssertionRuleDefinition(
        origin=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="demo"),
                kind=ResourceKind.ASSERTION_PROFILE,
                resource_id=ResourceId(value="api"),
            ),
            fingerprint=_fingerprint(),
        ),
        rule_id=RuleId(value="status"),
    )
    with pytest.raises(TypeError):
        AssertionApplication(
            application_id=RuleApplicationId(value=UUID(int=53)),
            definition=definition,
            effective_id=EffectiveRuleId(value="status"),
            rule_fingerprint=_fingerprint(),
            environment=_environment_definition(),
            environment_execution_id=cast("EnvironmentExecutionId", "bad"),
        )
    with pytest.raises(TypeError, match="origin"):
        AssertionRuleDefinition(
            origin=cast("DefinitionRef", "bad"),
            rule_id=RuleId(value="status"),
        )
    with pytest.raises(TypeError, match="RuleId"):
        AssertionRuleDefinition(
            origin=definition.origin,
            rule_id=cast("RuleId", "bad"),
        )
    with pytest.raises(TypeError, match="application ID"):
        AssertionApplication(
            application_id=cast("RuleApplicationId", "bad"),
            definition=definition,
            effective_id=EffectiveRuleId(value="status"),
            rule_fingerprint=_fingerprint(),
            environment=_environment_definition(),
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=531)),
        )
    with pytest.raises(TypeError, match="definition"):
        AssertionApplication(
            application_id=RuleApplicationId(value=UUID(int=536)),
            definition=cast("AssertionRuleDefinition", "bad"),
            effective_id=EffectiveRuleId(value="status"),
            rule_fingerprint=_fingerprint(),
            environment=_environment_definition(),
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=537)),
        )
    with pytest.raises(TypeError, match="effective ID"):
        AssertionApplication(
            application_id=RuleApplicationId(value=UUID(int=532)),
            definition=definition,
            effective_id=cast("EffectiveRuleId", "bad"),
            rule_fingerprint=_fingerprint(),
            environment=_environment_definition(),
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=533)),
        )
    with pytest.raises(TypeError, match="fingerprint"):
        AssertionApplication(
            application_id=RuleApplicationId(value=UUID(int=534)),
            definition=definition,
            effective_id=EffectiveRuleId(value="status"),
            rule_fingerprint=cast("Fingerprint", "bad"),
            environment=_environment_definition(),
            environment_execution_id=EnvironmentExecutionId(value=UUID(int=535)),
        )
    with pytest.raises(TypeError, match="environment"):
        msgspec.structs.replace(
            _application(),
            environment=cast("DefinitionRef", "bad"),
        )
    with pytest.raises(ValueError, match="Environment"):
        msgspec.structs.replace(
            _application(),
            environment=definition.origin,
        )


@pytest.mark.parametrize(
    ("selected", "evaluated", "satisfied", "violated", "errors"),
    [
        (0, 0, 0, 0, 0),
        (5, 5, 3, 1, 1),
        (5, 3, 1, 2, 0),
    ],
)
def test_assertion_count_conservation(
    selected: int,
    evaluated: int,
    satisfied: int,
    violated: int,
    errors: int,
) -> None:
    counts = AssertionCounts(
        selected=Count(value=selected),
        evaluated=Count(value=evaluated),
        satisfied=Count(value=satisfied),
        violated=Count(value=violated),
        errors=Count(value=errors),
    )
    assert counts.evaluated.value == (
        counts.satisfied.value + counts.violated.value + counts.errors.value
    )
