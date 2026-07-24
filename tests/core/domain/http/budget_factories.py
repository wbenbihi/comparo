"""Canonical budget-exhaustion factories for HTTP domain tests."""

from __future__ import annotations

from decimal import Decimal
from math import gcd
from uuid import UUID

from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetAmount
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import CaptureBudgetKind
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import TrialScope
from tests.core.domain.budget_origin_factories import default_budget_origin


def attempt_scope(
    *,
    attempt_id: AttemptId | None = None,
) -> AttemptScope:
    """Build one stable, fully addressed transport-attempt coordinate."""
    run = RunScope(run_id=RunId(value=UUID(int=101)))
    cell = CellScope(run=run, cell_id=CellId(value=UUID(int=102)))
    trial = TrialScope(cell=cell, trial_id=TrialId(value=UUID(int=103)))
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
        execution_id=EnvironmentExecutionId(value=UUID(int=104)),
    )
    return AttemptScope(
        execution=execution,
        attempt_id=attempt_id or AttemptId(value=UUID(int=105)),
    )


def _zero_like(amount: BudgetAmount) -> BudgetAmount:
    if type(amount) is Count:
        return Count(value=0)
    if type(amount) is ByteCount:
        return ByteCount(value=0)
    if type(amount) is Duration:
        return Duration(nanoseconds=0)
    return Ratio.from_decimal(Decimal(0))


def exhaustion(
    *,
    resource: BudgetResource,
    stage: BudgetStage,
    effective: BudgetAmount,
    attempted: BudgetAmount,
    body_id: BodyId,
    operation: str,
) -> BudgetExceeded:
    """Build canonical body-scoped exhaustion with complete derivation evidence."""
    zero = _zero_like(effective)
    budget = ResourceBudget(
        resource=resource,
        scope_kind=BudgetScopeKind.BODY,
        stage=stage,
        operation=BudgetOperation(value=operation),
        requested=effective,
        effective=effective,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=effective,
                origin=default_budget_origin(),
            ),
        ),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
        scope=BodyBudgetScope(attempt=attempt_scope(), body_id=body_id),
    )
    return BudgetExceeded(
        budget=budget,
        attempted=attempted,
        effect=BudgetExhaustionEffect.OBSERVATION_PARTIAL
        if resource
        in {
            BudgetResource.ENCODED_BODY_BYTES,
            BudgetResource.ENTITY_BODY_BYTES,
            BudgetResource.DECOMPRESSION_RATIO,
        }
        else BudgetExhaustionEffect.EVALUATION_INCOMPLETE,
    )


def capture_exhaustion(
    *,
    handle: BodyHandle,
    kind: CaptureBudgetKind,
    encoded_byte_count: ByteCount | None,
) -> BudgetExceeded:
    """Build canonical body-capture evidence reconciled to retained byte facts."""
    if kind is CaptureBudgetKind.ENCODED_BYTES:
        if encoded_byte_count is None:
            raise ValueError("encoded-byte test exhaustion requires a known encoded extent")
        return exhaustion(
            resource=BudgetResource.ENCODED_BODY_BYTES,
            stage=BudgetStage.CAPTURE,
            effective=encoded_byte_count,
            attempted=ByteCount(value=encoded_byte_count.value + 1),
            body_id=handle.body_id,
            operation="capture.encoded-body",
        )
    if kind is CaptureBudgetKind.ENTITY_BYTES:
        return exhaustion(
            resource=BudgetResource.ENTITY_BODY_BYTES,
            stage=BudgetStage.DECODING,
            effective=handle.byte_count,
            attempted=ByteCount(value=handle.byte_count.value + 1),
            body_id=handle.body_id,
            operation="decode.entity-body",
        )
    if encoded_byte_count is None or encoded_byte_count.value == 0:
        raise ValueError("ratio test exhaustion requires a non-zero encoded extent")
    divisor = gcd(handle.byte_count.value, encoded_byte_count.value)
    attempted = Ratio(
        numerator=handle.byte_count.value // divisor,
        denominator=PositiveCount(value=encoded_byte_count.value // divisor),
    )
    effective = Ratio.from_decimal(Decimal(1))
    if attempted.numerator <= attempted.denominator.value:
        raise ValueError("ratio test exhaustion requires entity bytes above encoded bytes")
    return exhaustion(
        resource=BudgetResource.DECOMPRESSION_RATIO,
        stage=BudgetStage.DECODING,
        effective=effective,
        attempted=attempted,
        body_id=handle.body_id,
        operation="decode.expansion-ratio",
    )


def view_exhaustion(
    kind: ViewBudgetKind,
    *,
    body_id: BodyId | None = None,
) -> BudgetExceeded:
    """Build canonical body-scoped semantic-view exhaustion."""
    resources: dict[ViewBudgetKind, tuple[BudgetResource, Count | ByteCount]] = {
        ViewBudgetKind.TEXT_CHARACTERS: (BudgetResource.DECODED_CHARACTERS, Count(value=10)),
        ViewBudgetKind.JSON_DEPTH: (BudgetResource.JSON_DEPTH, Count(value=10)),
        ViewBudgetKind.JSON_NODES: (BudgetResource.JSON_NODES, Count(value=10)),
        ViewBudgetKind.JSON_TOKENS: (BudgetResource.JSON_TOKENS, Count(value=10)),
        ViewBudgetKind.INDEX_STORAGE: (BudgetResource.VIEW_INDEX_BYTES, ByteCount(value=10)),
        ViewBudgetKind.MEMORY: (BudgetResource.MEMORY_BYTES, ByteCount(value=10)),
    }
    resource, amount = resources[kind]
    attempted: Count | ByteCount
    if type(amount) is Count:
        attempted = Count(value=amount.value + 1)
    else:
        attempted = ByteCount(value=amount.value + 1)
    return exhaustion(
        resource=resource,
        stage=BudgetStage.DECODING,
        effective=amount,
        attempted=attempted,
        body_id=body_id or BodyId(value=UUID(int=106)),
        operation="decode.semantic-view",
    )
