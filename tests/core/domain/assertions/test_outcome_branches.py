"""Cover every assertion operator and direct-construction outcome invariant."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.operators import AssertionOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import operator_uses_match_predicate
from comparo.core.domain.assertions.regex import RegexCompilerIdentity
from comparo.core.domain.assertions.regex import RegexDialect
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.assertions.rules import PredicateCondition
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.primitives import Count
from tests.core.domain.assertions._invariant_support import _assertion_fact
from tests.core.domain.assertions._invariant_support import _compiled_rule
from tests.core.domain.assertions._invariant_support import _regex
from tests.core.domain.assertions._invariant_support import _selector


def test_assertion_result_branches_reject_nondecisive_or_contradictory_outcomes() -> None:
    rule = _compiled_rule()
    error_counts = AssertionCounts(
        selected=Count(value=1),
        evaluated=Count(value=1),
        satisfied=Count(value=0),
        violated=Count(value=0),
        errors=Count(value=1),
    )
    error = AssertionError(code=DiagnosticCode(value="assertion.operator-error"))
    assert type(_assertion_fact(rule=rule, outcome=error, counts=error_counts).outcome) is (
        AssertionError
    )
    with pytest.raises(ValueError, match="satisfied outcome"):
        _assertion_fact(rule=rule, outcome=Satisfied(), counts=error_counts)
    with pytest.raises(ValueError, match="at least one match error"):
        _assertion_fact(
            rule=rule,
            outcome=error,
            counts=AssertionCounts(
                selected=Count(value=1),
                evaluated=Count(value=0),
                satisfied=Count(value=0),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
        )
    with pytest.raises(ValueError, match="decisive"):
        _assertion_fact(
            rule=msgspec.structs.replace(rule, quantifier=Quantifier.ALL),
            outcome=error,
            counts=AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=2),
                satisfied=Count(value=0),
                violated=Count(value=1),
                errors=Count(value=1),
            ),
        )
    with pytest.raises(ValueError, match="violated outcome"):
        _assertion_fact(
            rule=msgspec.structs.replace(rule, quantifier=Quantifier.NONE),
            outcome=Violated(reason=ViolationReason.PREDICATE),
            counts=AssertionCounts(
                selected=Count(value=1),
                evaluated=Count(value=1),
                satisfied=Count(value=0),
                violated=Count(value=1),
                errors=Count(value=0),
            ),
        )


def test_decisive_match_outcomes_override_nondecisive_match_errors() -> None:
    base = _compiled_rule()
    cases = (
        (
            Quantifier.ANY,
            Satisfied(),
            AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=2),
                satisfied=Count(value=1),
                violated=Count(value=0),
                errors=Count(value=1),
            ),
        ),
        (
            Quantifier.ALL,
            Violated(reason=ViolationReason.PREDICATE),
            AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=2),
                satisfied=Count(value=0),
                violated=Count(value=1),
                errors=Count(value=1),
            ),
        ),
        (
            Quantifier.NONE,
            Violated(reason=ViolationReason.PREDICATE),
            AssertionCounts(
                selected=Count(value=2),
                evaluated=Count(value=2),
                satisfied=Count(value=1),
                violated=Count(value=0),
                errors=Count(value=1),
            ),
        ),
    )

    for quantifier, outcome, counts in cases:
        fact = _assertion_fact(
            rule=msgspec.structs.replace(base, quantifier=quantifier),
            outcome=outcome,
            counts=counts,
        )
        assert fact.outcome == outcome


def test_cardinality_result_branches_cover_exact_and_bounded_contracts() -> None:
    cardinality_identity = AssertionOperatorIdentity(
        namespace="comparo.core",
        kind=AssertionOperatorKind.CARDINALITY,
        version="1",
    )
    for operator, selected in (
        (CardinalityOperator(exact=Count(value=0)), 0),
        (CardinalityOperator(minimum=Count(value=1)), 2),
        (CardinalityOperator(maximum=Count(value=2)), 1),
        (CardinalityOperator(minimum=Count(value=1), maximum=Count(value=3)), 2),
    ):
        rule = msgspec.structs.replace(
            _compiled_rule(),
            operator_id=cardinality_identity,
            operator=operator,
            quantifier=None,
        )
        assert (
            type(
                _assertion_fact(
                    rule=rule,
                    outcome=Satisfied(),
                    counts=AssertionCounts(
                        selected=Count(value=selected),
                        evaluated=Count(value=0),
                        satisfied=Count(value=0),
                        violated=Count(value=0),
                        errors=Count(value=0),
                    ),
                ).outcome
            )
            is Satisfied
        )

    exact_one = msgspec.structs.replace(
        _compiled_rule(),
        operator_id=cardinality_identity,
        operator=CardinalityOperator(exact=Count(value=1)),
        quantifier=None,
    )
    with pytest.raises(ValueError, match="satisfied cardinality"):
        _assertion_fact(
            rule=msgspec.structs.replace(
                exact_one,
                operator=CardinalityOperator(exact=Count(value=0)),
            ),
            outcome=Violated(reason=ViolationReason.CARDINALITY),
            counts=AssertionCounts(
                selected=Count(value=0),
                evaluated=Count(value=0),
                satisfied=Count(value=0),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
        )
    with pytest.raises(ValueError, match="failed cardinality"):
        _assertion_fact(
            rule=exact_one,
            outcome=Satisfied(),
            counts=AssertionCounts(
                selected=Count(value=0),
                evaluated=Count(value=0),
                satisfied=Count(value=0),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
        )
    with pytest.raises(ValueError, match="predicate-free"):
        _assertion_fact(
            rule=exact_one,
            outcome=AssertionError(code=DiagnosticCode(value="assertion.error")),
            counts=AssertionCounts(
                selected=Count(value=1),
                evaluated=Count(value=1),
                satisfied=Count(value=0),
                violated=Count(value=0),
                errors=Count(value=1),
            ),
        )


def test_applicability_and_operator_helpers_reject_predicate_free_open_values() -> None:
    with pytest.raises(TypeError, match="unsupported"):
        operator_uses_match_predicate(cast("AssertionOperator", "bad"))
    cardinality = CardinalityOperator(exact=Count(value=0))
    with pytest.raises(ValueError, match="match predicate"):
        PredicateCondition(
            selector=_selector(),
            operator_id=AssertionOperatorIdentity(
                namespace="comparo.core",
                kind=AssertionOperatorKind.CARDINALITY,
                version="1",
            ),
            operator=cardinality,
            quantifier=Quantifier.ONE,
        )
    with pytest.raises(TypeError, match="regex dialect"):
        msgspec.structs.replace(_regex(), dialect=cast("RegexDialect", "re2"))
    with pytest.raises(TypeError, match="regex compiler"):
        msgspec.structs.replace(
            _regex(),
            compiler=cast("RegexCompilerIdentity", "re2"),
        )
