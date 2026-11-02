"""Generative checks for exact values, identities, and the scope lattice."""

from decimal import Decimal
from fractions import Fraction
from math import gcd
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import ScopeRelation
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.scopes import scope_contains
from comparo.core.domain.scopes import scope_relation
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import json_number


@given(st.integers(min_value=0))
def test_non_negative_primitives_preserve_arbitrary_size_exact_integers(value: int) -> None:
    assert Count(value=value).value == value
    assert ByteCount(value=value).value == value
    assert Duration(nanoseconds=value).nanoseconds == value


@given(st.integers(max_value=-1))
def test_non_negative_primitives_reject_every_negative_integer(value: int) -> None:
    for constructor in (Count, ByteCount):
        with pytest.raises(ValueError, match="non-negative"):
            constructor(value=value)
    with pytest.raises(ValueError, match="non-negative"):
        Duration(nanoseconds=value)


@given(st.integers(min_value=0), st.integers(min_value=1))
def test_ratio_has_exactly_one_reduced_representation(numerator: int, denominator: int) -> None:
    fraction = Fraction(numerator, denominator)
    ratio = Ratio(
        numerator=fraction.numerator,
        denominator=PositiveCount(value=fraction.denominator),
    )
    assert gcd(ratio.numerator, ratio.denominator.value) == 1
    if fraction.numerator != numerator or fraction.denominator != denominator:
        with pytest.raises(ValueError, match="canonical reduced"):
            Ratio(numerator=numerator, denominator=PositiveCount(value=denominator))


@given(
    coefficient=st.integers(),
    scale=st.integers(min_value=0, max_value=18),
)
def test_json_number_factory_produces_one_stable_numeric_variant(
    coefficient: int,
    scale: int,
) -> None:
    source = Decimal(coefficient).scaleb(-scale)
    first = json_number(source)
    second = json_number(source)
    assert first == second
    if isinstance(first, JsonInteger):
        assert source == first.value
    else:
        assert isinstance(first, JsonDecimal)
        canonical = json_number(first.value)
        assert isinstance(canonical, JsonDecimal)
        assert first.value.as_tuple() == canonical.value.as_tuple()


def _uuid(seed: int) -> UUID:
    return UUID(int=seed)


def _scopes(
    seed: int,
) -> tuple[
    RunScope,
    CellScope,
    TrialScope,
    EnvironmentScope,
    ExecutionScope,
    StepScope,
    AttemptScope,
]:
    run = RunScope(run_id=RunId(value=_uuid(seed)))
    cell = CellScope(run=run, cell_id=CellId(value=_uuid(seed + 1)))
    trial = TrialScope(cell=cell, trial_id=TrialId(value=_uuid(seed + 2)))
    environment = EnvironmentScope(
        run=run,
        environment=ResourceIdentity(
            namespace=NamespaceId(value="local"),
            kind=ResourceKind.ENVIRONMENT,
            resource_id=ResourceId(value="staging"),
        ),
    )
    execution = ExecutionScope(
        trial=trial,
        environment=environment,
        execution_id=EnvironmentExecutionId(value=_uuid(seed + 3)),
    )
    step_id = StepExecutionId(value=_uuid(seed + 4))
    step = StepScope(execution=execution, step_execution_id=step_id)
    attempt = AttemptScope(
        execution=execution,
        attempt_id=AttemptId(value=_uuid(seed + 5)),
        step_execution_id=step_id,
    )
    return run, cell, trial, environment, execution, step, attempt


@given(st.integers(min_value=1, max_value=(1 << 128) - 7))
def test_scope_containment_is_reflexive_antisymmetric_and_transitive(seed: int) -> None:
    run, cell, trial, environment, execution, step, attempt = _scopes(seed)
    scopes = (run, cell, trial, environment, execution, step, attempt)
    for scope in scopes:
        assert scope_contains(scope, scope)
        assert scope_relation(scope, scope) is ScopeRelation.SAME
    for left in scopes:
        for right in scopes:
            if scope_contains(left, right) and scope_contains(right, left):
                assert left == right
    assert scope_contains(run, execution)
    assert scope_contains(execution, attempt)
    assert scope_contains(run, attempt)
    assert scope_relation(cell, environment) is ScopeRelation.OVERLAPS
