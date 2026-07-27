"""Prove gate normalization is a one-way, value-free serialization boundary."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import MatchFact
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.gates.normalization import normalize_assertion_fact
from comparo.core.domain.gates.normalization import normalize_diff_fact
from comparo.core.domain.gates.summaries import DiffGateSummary
from comparo.core.domain.gates.summaries import GateEnvironmentExecution
from comparo.core.domain.gates.summaries import NormalizedFactSummary
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.lifecycle import RunComplete
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from comparo.core.gates import evaluate_gate
from tests.core.domain.gates.test_normalization import _assertion_fact
from tests.core.domain.gates.test_normalization import _diff_counts
from tests.core.domain.gates.test_normalization import _diff_coverage
from tests.core.domain.gates.test_normalization import _diff_fact
from tests.core.domain.gates.test_normalization import _predicate_rule
from tests.core.gates.test_engine import _profile
from tests.core.gates.test_engine import _threshold

_SECRET = "998877665544332211"


def _sensitive_assertion_fact() -> AssertionFact:
    counts = AssertionCounts(
        selected=Count(value=1),
        evaluated=Count(value=1),
        satisfied=Count(value=0),
        violated=Count(value=1),
        errors=Count(value=0),
    )
    fact = _assertion_fact(
        Violated(reason=ViolationReason.PREDICATE),
        counts,
        rule=_predicate_rule(),
    )
    sensitive = SensitiveValue(
        JsonInteger(value=int(_SECRET)),
        origins=(TaintOriginId(value="gate-security-test"),),
    )
    retained = MatchFact(
        fact_id=FactId(value=UUID(int=777)),
        match=SelectionMatch(
            value=sensitive,
            location=ExchangeLocation(
                environment_execution_id=fact.application.environment_execution_id,
                source=SelectorSource.RESPONSE_STATUS,
                exchange_id=ExchangeId(value=UUID(int=778)),
                field=StaticFieldPath(segments=("response", "status")),
            ),
        ),
        outcome=MatchOutcome.VIOLATED,
    )
    return msgspec.structs.replace(
        fact,
        evidence=AssertionEvidence(retained=(retained,), omitted=Count(value=0)),
    )


def _walk(value: object) -> Iterable[object]:
    yield value
    if isinstance(value, msgspec.Struct):
        for field in value.__struct_fields__:
            yield from _walk(getattr(value, field))
    elif type(value) is tuple:
        for item in value:
            yield from _walk(item)


def _assert_serializable_without_secret(value: object) -> None:
    rendered = (
        repr(value).encode(),
        msgspec.json.encode(value),
        msgspec.msgpack.encode(value),
        repr(msgspec.to_builtins(value)).encode(),
    )
    assert all(_SECRET.encode() not in item for item in rendered)


def test_sensitive_assertion_normalizes_to_a_value_free_serializable_projection() -> None:
    source = _sensitive_assertion_fact()
    normalized = normalize_assertion_fact(
        source,
        execution=GateEnvironmentExecution(
            environment=source.application.environment,
            execution_id=source.application.environment_execution_id,
            roles=(EnvironmentRole.BASELINE,),
        ),
    )

    assert source.evidence.retained
    _assert_serializable_without_secret(normalized)
    reachable = tuple(_walk(normalized))
    assert not any(type(item) is SensitiveValue for item in reachable)
    assert not any(type(item) is AssertionFact for item in reachable)
    assert not hasattr(normalized.rules[0], "fact")
    assert not hasattr(normalized.rules[0], "evidence")

    evaluation = evaluate_gate(
        _profile(
            roles=(EnvironmentRole.BASELINE,),
            assertion_coverage=True,
            thresholds=(_threshold(GateSeverity.BLOCKING, violated=1),),
        ),
        NormalizedFactSummary(
            completion=RunComplete(completed_at=UtcInstant(unix_nanoseconds=1)),
            assertions=(normalized,),
        ),
    )
    _assert_serializable_without_secret(evaluation)
    evaluated_reachable = tuple(_walk(evaluation))
    assert not any(type(item) is AssertionFact for item in evaluated_reachable)
    assert not hasattr(evaluation, "summary")


def test_diff_normalizes_to_the_same_narrow_value_free_boundary() -> None:
    source = _diff_fact(Equivalent(), _diff_counts(same=1), _diff_coverage(compared=1))
    normalized = normalize_diff_fact(source)

    _assert_serializable_without_secret(normalized)
    reachable = tuple(_walk(normalized))
    assert not any(type(item) is DiffRuleFact for item in reachable)
    assert not hasattr(normalized.rules[0], "fact")
    assert not hasattr(normalized.rules[0], "evidence")


def test_gate_summary_rejects_role_relabeling_and_disappearing_derived_values() -> None:
    assertion_source = _sensitive_assertion_fact()
    assertion = normalize_assertion_fact(
        assertion_source,
        execution=GateEnvironmentExecution(
            environment=assertion_source.application.environment,
            execution_id=assertion_source.application.environment_execution_id,
            roles=(EnvironmentRole.OTHER,),
        ),
    )
    diff = normalize_diff_fact(
        _diff_fact(Equivalent(), _diff_counts(same=1), _diff_coverage(compared=1))
    )
    binding_pair = msgspec.structs.replace(
        diff.pair,
        baseline_environment=assertion_source.application.environment,
        baseline_execution_id=assertion_source.application.environment_execution_id,
    )
    bound_diff = DiffGateSummary(
        rules=(msgspec.structs.replace(diff.rules[0], pair=binding_pair),),
    )
    with pytest.raises(ValueError, match="roles contradict directed pairs"):
        NormalizedFactSummary(
            completion=RunComplete(completed_at=UtcInstant(unix_nanoseconds=1)),
            assertions=(assertion,),
            diffs=(bound_diff,),
        )

    partial_coverage = msgspec.structs.replace(
        diff.rules[0].coverage,
        baseline_selected_values=Count(value=2),
        candidate_selected_values=Count(value=2),
        baseline_derived_values=Count(value=2),
        candidate_derived_values=Count(value=2),
    )
    with pytest.raises(ValueError, match="consume and resolve every derived value"):
        msgspec.structs.replace(diff.rules[0], coverage=partial_coverage)


def test_gate_summary_rejects_execution_id_reuse_under_an_environment_alias() -> None:
    assertion_source = _sensitive_assertion_fact()
    assertion = normalize_assertion_fact(
        assertion_source,
        execution=GateEnvironmentExecution(
            environment=assertion_source.application.environment,
            execution_id=assertion_source.application.environment_execution_id,
            roles=(EnvironmentRole.BASELINE,),
        ),
    )
    diff = normalize_diff_fact(
        _diff_fact(Equivalent(), _diff_counts(same=1), _diff_coverage(compared=1))
    )
    aliased_pair = msgspec.structs.replace(
        diff.pair,
        baseline_environment=msgspec.structs.replace(
            diff.pair.baseline_environment,
            identity=msgspec.structs.replace(
                diff.pair.baseline_environment.identity,
                resource_id=ResourceId(value="attacker-controlled-alias"),
            ),
        ),
        baseline_execution_id=assertion.execution_id,
    )
    with pytest.raises(ValueError, match="EnvironmentExecutionId cannot identify multiple"):
        NormalizedFactSummary(
            completion=RunComplete(completed_at=UtcInstant(unix_nanoseconds=1)),
            assertions=(assertion,),
            diffs=(
                DiffGateSummary(
                    rules=(msgspec.structs.replace(diff.rules[0], pair=aliased_pair),),
                ),
            ),
        )
