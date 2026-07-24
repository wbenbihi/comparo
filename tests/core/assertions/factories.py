"""Shared factories for pure assertion engine tests."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec

from comparo.core.assertions import AssertionBudgets
from comparo.core.assertions import AssertionControl
from comparo.core.assertions import PreparedAssertionInputs
from comparo.core.assertions import PreparedExpression
from comparo.core.assertions import PreparedOperandReady
from comparo.core.assertions import assertion_budget_operations
from comparo.core.assertions import required_expressions
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.scopes import RunScope
from comparo.core.selection import SelectionBudgets
from comparo.core.selection import SelectionCancellation
from comparo.core.selection import SelectionControl
from comparo.core.selection import SelectionSnapshot
from comparo.core.selection import selection_budget_operations
from tests.core.domain.assertions._invariant_support import _application
from tests.core.domain.assertions._invariant_support import _compiled_rule
from tests.core.domain.budget_origin_factories import default_budget_origin
from tests.core.domain.http.stream_factories import stream_execution


def snapshot() -> SelectionSnapshot:
    """Build one status-200 terminal observation."""
    return SelectionSnapshot(observation=stream_execution(ViewKind.SSE))


def rule() -> CompiledAssertionRule:
    """Build the canonical status-equals-200 rule."""
    return _compiled_rule()


def application(
    compiled: CompiledAssertionRule | None = None,
    prepared_snapshot: SelectionSnapshot | None = None,
) -> AssertionApplication:
    """Bind the support application to the chosen rule and observation."""
    actual_rule = rule() if compiled is None else compiled
    actual_snapshot = snapshot() if prepared_snapshot is None else prepared_snapshot
    return msgspec.structs.replace(
        _application(),
        definition=actual_rule.definition,
        effective_id=actual_rule.effective_id,
        rule_fingerprint=actual_rule.fingerprint,
        environment=actual_snapshot.observation.environment,
        environment_execution_id=actual_snapshot.observation.execution_id,
    )


def inputs(
    compiled: CompiledAssertionRule | None = None,
    prepared_snapshot: SelectionSnapshot | None = None,
) -> PreparedAssertionInputs:
    """Resolve every literal expression exactly once in stable rule order."""
    actual_rule = rule() if compiled is None else compiled
    actual_snapshot = snapshot() if prepared_snapshot is None else prepared_snapshot
    expressions = tuple(
        PreparedExpression(
            expression=expression,
            result=PreparedOperandReady(value=cast("LiteralExpr", expression).value),
        )
        for expression in required_expressions(actual_rule)
    )
    return PreparedAssertionInputs(
        rule_fingerprint=actual_rule.fingerprint,
        environment_execution_id=actual_snapshot.observation.execution_id,
        expressions=expressions,
    )


def control(
    *,
    selection_limit: int = 10_000,
    work: int = 10_000,
    facts: int = 10_000,
    evidence: int = 10_000,
    evidence_bytes: int = 10_000,
    cancelled: bool = False,
) -> AssertionControl:
    """Build cumulative selection, evaluation, and evidence controls."""
    selection_operations = selection_budget_operations()
    assertion_operations = assertion_budget_operations()
    selection = SelectionControl(
        budgets=SelectionBudgets(
            work=_budget(
                BudgetResource.SELECTION_WORK,
                BudgetStage.SELECTION,
                selection_operations[0],
                Count(value=selection_limit),
            ),
            matches=_budget(
                BudgetResource.SELECTION_MATCHES,
                BudgetStage.SELECTION,
                selection_operations[1],
                Count(value=selection_limit),
            ),
            result_bytes=_budget(
                BudgetResource.SELECTED_RESULT_BYTES,
                BudgetStage.SELECTION,
                selection_operations[2],
                ByteCount(value=selection_limit),
            ),
        ),
        cancellation=SelectionCancellation(
            cancelled=cancelled,
            code=DiagnosticCode(value="assertion.cancelled"),
        ),
    )
    return AssertionControl(
        selection=selection,
        budgets=AssertionBudgets(
            work=_budget(
                BudgetResource.TRANSFORM_WORK,
                BudgetStage.EVALUATION,
                assertion_operations[0],
                Count(value=work),
            ),
            facts=_budget(
                BudgetResource.FACTS,
                BudgetStage.EVALUATION,
                assertion_operations[1],
                Count(value=facts),
            ),
            evidence_examples=_budget(
                BudgetResource.EVIDENCE_EXAMPLES,
                BudgetStage.EVIDENCE,
                assertion_operations[2],
                Count(value=evidence),
            ),
            evidence_bytes=_budget(
                BudgetResource.EVIDENCE_BYTES,
                BudgetStage.EVIDENCE,
                assertion_operations[3],
                ByteCount(value=evidence_bytes),
            ),
        ),
    )


def _budget(
    resource: BudgetResource,
    stage: BudgetStage,
    operation: str,
    limit: Count | ByteCount,
) -> ResourceBudget:
    zero = type(limit)(value=0)
    return ResourceBudget(
        resource=resource,
        scope_kind=BudgetScopeKind.RUN,
        stage=stage,
        operation=BudgetOperation(value=operation),
        requested=limit,
        effective=limit,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=limit,
                origin=default_budget_origin(),
            ),
        ),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
        scope=RunScope(run_id=RunId(value=UUID(int=91_000))),
    )
