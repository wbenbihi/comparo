"""Cover every assertion operator and direct-construction outcome invariant."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.operators import require_operator
from comparo.core.domain.assertions.regex import RegexCompilerIdentity
from comparo.core.domain.assertions.regex import RegexDialect
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
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from tests.core.domain.assertions._invariant_support import _application
from tests.core.domain.assertions._invariant_support import _assertion_fact
from tests.core.domain.assertions._invariant_support import _compiled_rule
from tests.core.domain.assertions._invariant_support import _evidence_exhaustion
from tests.core.domain.assertions._invariant_support import _fingerprint
from tests.core.domain.assertions._invariant_support import _literal
from tests.core.domain.assertions._invariant_support import _regex


def test_builtin_operator_identity_cannot_claim_foreign_dispatch_semantics() -> None:
    operator = EqualityOperator(relation=EqualityRelation.EQUALS, expected=_literal())
    with pytest.raises(ValueError, match=r"comparo\.core"):
        require_operator(
            AssertionOperatorIdentity(
                namespace="community.example",
                kind=AssertionOperatorKind.EQUALITY,
                version="1",
            ),
            operator,
        )
    with pytest.raises(ValueError, match="version 1"):
        require_operator(
            AssertionOperatorIdentity(
                namespace="comparo.core",
                kind=AssertionOperatorKind.EQUALITY,
                version="2",
            ),
            operator,
        )


def test_regex_contract_retains_pinned_compiler_and_compiled_fingerprint() -> None:
    regex = _regex()
    assert regex.dialect is RegexDialect.RE2
    assert regex.compiler.version == "1"
    assert regex.compiled_fingerprint == _fingerprint()
    with pytest.raises(ValueError, match=r"comparo\.core/re2@1"):
        RegexCompilerIdentity(namespace="community.example", name="re2", version="1")
    with pytest.raises(TypeError, match="fingerprint"):
        msgspec.structs.replace(regex, compiled_fingerprint=cast("Fingerprint", "bad"))


def test_assertion_fact_is_bound_to_exact_effective_compiled_rule() -> None:
    rule = _compiled_rule()
    counts = AssertionCounts(
        selected=Count(value=1),
        evaluated=Count(value=1),
        satisfied=Count(value=1),
        violated=Count(value=0),
        errors=Count(value=0),
    )
    application = msgspec.structs.replace(
        _application(),
        effective_id=EffectiveRuleId(value="other"),
    )
    with pytest.raises(ValueError, match="effective compiled rule"):
        AssertionFact(
            fact_id=FactId(value=UUID(int=700)),
            application=application,
            rule=rule,
            outcome=Satisfied(),
            counts=counts,
            evidence=AssertionEvidence(
                retained=(), omitted=Count(value=1), truncation=_evidence_exhaustion()
            ),
        )


def test_quantifier_and_predicate_free_cardinality_outcome_laws() -> None:
    zero = AssertionCounts(
        selected=Count(value=0),
        evaluated=Count(value=0),
        satisfied=Count(value=0),
        violated=Count(value=0),
        errors=Count(value=0),
    )
    empty_evidence = AssertionEvidence(retained=(), omitted=Count(value=0))
    with pytest.raises(ValueError, match="quantifier"):
        AssertionFact(
            fact_id=FactId(value=UUID(int=701)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Satisfied(),
            counts=zero,
            evidence=empty_evidence,
        )

    none_rule = msgspec.structs.replace(_compiled_rule(), quantifier=Quantifier.NONE)
    none_fact = AssertionFact(
        fact_id=FactId(value=UUID(int=702)),
        application=_application(),
        rule=none_rule,
        outcome=Satisfied(),
        counts=zero,
        evidence=empty_evidence,
    )
    assert type(none_fact.outcome) is Satisfied

    cardinality_rule = msgspec.structs.replace(
        _compiled_rule(),
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.CARDINALITY,
            version="1",
        ),
        operator=CardinalityOperator(exact=Count(value=0)),
        quantifier=None,
    )
    cardinality_fact = AssertionFact(
        fact_id=FactId(value=UUID(int=703)),
        application=_application(),
        rule=cardinality_rule,
        outcome=Satisfied(),
        counts=zero,
        evidence=empty_evidence,
    )
    assert type(cardinality_fact.outcome) is Satisfied
    with pytest.raises(ValueError, match="predicate-free"):
        msgspec.structs.replace(cardinality_rule, quantifier=Quantifier.ONE)
    with pytest.raises(ValueError, match="cardinality mismatch"):
        AssertionFact(
            fact_id=FactId(value=UUID(int=707)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=AssertionError(code=DiagnosticCode(value="assertion.operator-error")),
            counts=AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=1),
                satisfied=Count(value=0),
                violated=Count(value=0),
                errors=Count(value=1),
            ),
            evidence=AssertionEvidence(
                retained=(), omitted=Count(value=1), truncation=_evidence_exhaustion()
            ),
        )


def test_assertion_evidence_ids_and_outcome_buckets_cannot_contradict_counts() -> None:
    match = SelectionMatch(
        value=JsonInteger(value=200),
        location=ExchangeLocation(
            environment_execution_id=_application().environment_execution_id,
            source=SelectorSource.RESPONSE_STATUS,
            exchange_id=ExchangeId(value=UUID(int=7041)),
            field=StaticFieldPath(segments=("response", "status")),
        ),
    )
    fact = MatchFact(
        fact_id=FactId(value=UUID(int=705)),
        match=match,
        outcome=MatchOutcome.SATISFIED,
    )
    with pytest.raises(ValueError, match="unique"):
        AssertionEvidence(retained=(fact, fact), omitted=Count(value=0))
    with pytest.raises(ValueError, match="contradicts"):
        AssertionFact(
            fact_id=FactId(value=UUID(int=706)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Violated(reason=ViolationReason.PREDICATE),
            counts=AssertionCounts(
                selected=Count(value=1),
                evaluated=Count(value=1),
                satisfied=Count(value=0),
                violated=Count(value=1),
                errors=Count(value=0),
            ),
            evidence=AssertionEvidence(retained=(fact,), omitted=Count(value=0)),
        )


def test_assertion_retained_locations_bind_execution_and_selector_source() -> None:
    application = _application()
    rule = _compiled_rule()
    location = ExchangeLocation(
        environment_execution_id=application.environment_execution_id,
        source=SelectorSource.RESPONSE_STATUS,
        exchange_id=ExchangeId(value=UUID(int=710)),
        field=StaticFieldPath(segments=("response", "status")),
    )
    retained = MatchFact(
        fact_id=FactId(value=UUID(int=711)),
        match=SelectionMatch(value=JsonInteger(value=200), location=location),
        outcome=MatchOutcome.SATISFIED,
    )
    counts = AssertionCounts(
        selected=Count(value=1),
        evaluated=Count(value=1),
        satisfied=Count(value=1),
        violated=Count(value=0),
        errors=Count(value=0),
    )

    valid = AssertionFact(
        fact_id=FactId(value=UUID(int=712)),
        application=application,
        rule=rule,
        outcome=Satisfied(),
        counts=counts,
        evidence=AssertionEvidence(retained=(retained,), omitted=Count(value=0)),
    )
    with pytest.raises(ValueError, match="parent fact ID"):
        msgspec.structs.replace(valid, fact_id=retained.fact_id)
    with pytest.raises(ValueError, match="different Environment execution"):
        msgspec.structs.replace(
            valid,
            evidence=AssertionEvidence(
                retained=(
                    msgspec.structs.replace(
                        retained,
                        match=msgspec.structs.replace(
                            retained.match,
                            location=msgspec.structs.replace(
                                location,
                                environment_execution_id=EnvironmentExecutionId(
                                    value=UUID(int=713)
                                ),
                            ),
                        ),
                    ),
                ),
                omitted=Count(value=0),
            ),
        )
    with pytest.raises(ValueError, match="source does not match"):
        msgspec.structs.replace(
            valid,
            evidence=AssertionEvidence(
                retained=(
                    msgspec.structs.replace(
                        retained,
                        match=msgspec.structs.replace(
                            retained.match,
                            location=msgspec.structs.replace(
                                location,
                                source=SelectorSource.RESPONSE_PROTOCOL,
                                field=StaticFieldPath(segments=("response", "protocol")),
                            ),
                        ),
                    ),
                ),
                omitted=Count(value=0),
            ),
        )

    false_retained = MatchFact(
        fact_id=FactId(value=UUID(int=7131)),
        match=SelectionMatch(value=JsonString(value="not-an-integer"), location=location),
        outcome=MatchOutcome.VIOLATED,
    )
    with pytest.raises(ValueError, match="value kind contradicts"):
        AssertionFact(
            fact_id=FactId(value=UUID(int=7132)),
            application=application,
            rule=rule,
            outcome=Violated(reason=ViolationReason.PREDICATE),
            counts=AssertionCounts(
                selected=Count(value=1),
                evaluated=Count(value=1),
                satisfied=Count(value=0),
                violated=Count(value=1),
                errors=Count(value=0),
            ),
            evidence=AssertionEvidence(retained=(false_retained,), omitted=Count(value=0)),
        )

    repeated = msgspec.structs.replace(
        retained,
        fact_id=FactId(value=UUID(int=714)),
    )
    with pytest.raises(ValueError, match="cannot repeat a location"):
        msgspec.structs.replace(
            valid,
            counts=AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=2),
                satisfied=Count(value=2),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
            evidence=AssertionEvidence(
                retained=(retained, repeated),
                omitted=Count(value=0),
            ),
        )


def test_assertion_result_branches_encode_each_quantifier_proof() -> None:
    rule = _compiled_rule()
    satisfied_one = _assertion_fact(
        rule=rule,
        outcome=Satisfied(),
        counts=AssertionCounts(
            selected=Count(value=1),
            evaluated=Count(value=1),
            satisfied=Count(value=1),
            violated=Count(value=0),
            errors=Count(value=0),
        ),
    )
    assert satisfied_one.severity is Severity.BLOCKING
    assert satisfied_one.selector == rule.selector

    zero = AssertionCounts(
        selected=Count(value=0),
        evaluated=Count(value=0),
        satisfied=Count(value=0),
        violated=Count(value=0),
        errors=Count(value=0),
    )
    assert type(_assertion_fact(rule=rule, outcome=Skipped(), counts=zero).outcome) is Skipped
    assert (
        type(
            _assertion_fact(
                rule=rule,
                outcome=AssertionUnavailable(code=DiagnosticCode(value="assertion.unavailable")),
                counts=zero,
            ).outcome
        )
        is AssertionUnavailable
    )

    for quantifier in (Quantifier.ALL, Quantifier.ANY):
        matching = AssertionCounts(
            selected=Count(value=2),
            evaluated=Count(value=2),
            satisfied=Count(value=2),
            violated=Count(value=0),
            errors=Count(value=0),
        )
        assert (
            type(
                _assertion_fact(
                    rule=msgspec.structs.replace(rule, quantifier=quantifier),
                    outcome=Satisfied(),
                    counts=matching,
                ).outcome
            )
            is Satisfied
        )

    violations = (
        (
            Quantifier.ONE,
            AssertionCounts(
                selected=Count(value=1),
                evaluated=Count(value=1),
                satisfied=Count(value=0),
                violated=Count(value=1),
                errors=Count(value=0),
            ),
        ),
        (
            Quantifier.ALL,
            AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=1),
                satisfied=Count(value=0),
                violated=Count(value=1),
                errors=Count(value=0),
            ),
        ),
        (
            Quantifier.ANY,
            AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=2),
                satisfied=Count(value=0),
                violated=Count(value=2),
                errors=Count(value=0),
            ),
        ),
        (
            Quantifier.NONE,
            AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=1),
                satisfied=Count(value=1),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
        ),
    )
    for quantifier, counts in violations:
        fact = _assertion_fact(
            rule=msgspec.structs.replace(rule, quantifier=quantifier),
            outcome=Violated(reason=ViolationReason.PREDICATE),
            counts=counts,
        )
        assert type(fact.outcome) is Violated

    for quantifier, selected in (
        (Quantifier.ONE, 2),
        (Quantifier.ALL, 0),
        (Quantifier.ANY, 0),
    ):
        fact = _assertion_fact(
            rule=msgspec.structs.replace(rule, quantifier=quantifier),
            outcome=Violated(reason=ViolationReason.CARDINALITY),
            counts=AssertionCounts(
                selected=Count(value=selected),
                evaluated=Count(value=0),
                satisfied=Count(value=0),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
        )
        assert type(fact.outcome) is Violated
