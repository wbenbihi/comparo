"""Validate exact cumulative diff budget contracts and atomic accounting."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.diffs.accounting import DiffBudgets
from comparo.core.diffs.accounting import DiffCancellation
from comparo.core.diffs.accounting import DiffMeter
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.scopes import RunScope
from comparo.core.selection import SelectionControl
from tests.core.diffs.factories import control
from tests.core.diffs.factories import meter


def _changed_budget(field: str, value: object) -> DiffBudgets:
    return msgspec.structs.replace(control().budgets, **{field: value})


def _forged_budget(field: str, value: object) -> ResourceBudget:
    budget = msgspec.structs.replace(control().budgets.derived_values)
    msgspec.structs.force_setattr(budget, field, value)
    return budget


def test_diff_budget_contract_rejects_wrong_field_resource_stage_operation_and_unit() -> None:
    with pytest.raises(TypeError, match="ResourceBudget"):
        _changed_budget("derived_values", cast("ResourceBudget", object()))
    with pytest.raises(ValueError, match="resource or stage"):
        _changed_budget(
            "derived_values",
            _forged_budget("resource", BudgetResource.DERIVED_BYTES),
        )
    with pytest.raises(ValueError, match="resource or stage"):
        _changed_budget(
            "derived_values",
            _forged_budget("stage", BudgetStage.EVIDENCE),
        )
    with pytest.raises(ValueError, match="operation"):
        _changed_budget(
            "derived_values",
            _forged_budget("operation", BudgetOperation(value="diff.wrong")),
        )
    with pytest.raises(TypeError, match="wrong unit"):
        _changed_budget(
            "derived_values",
            _forged_budget("effective", ByteCount(value=1)),
        )


def test_diff_budgets_require_one_exact_scope() -> None:
    changed = _forged_budget(
        "scope",
        RunScope(run_id=RunId(value=UUID(int=999_001))),
    )
    with pytest.raises(ValueError, match="one exact scope"):
        _changed_budget("derived_values", changed)


def test_cancellation_and_control_reject_open_boundary_values() -> None:
    cancellation = DiffCancellation(
        cancelled=False,
        code=DiagnosticCode(value="diff.cancelled"),
    )
    assert not cancellation.cancelled
    with pytest.raises(TypeError, match="state"):
        DiffCancellation(
            cancelled=cast("bool", 0),
            code=DiagnosticCode(value="diff.cancelled"),
        )
    with pytest.raises(TypeError, match="DiagnosticCode"):
        DiffCancellation(cancelled=False, code=cast("DiagnosticCode", "cancelled"))

    valid = control()
    replacements = (
        ("baseline_selection", cast("SelectionControl", object()), "baseline"),
        ("candidate_selection", cast("SelectionControl", object()), "candidate"),
        ("budgets", cast("DiffBudgets", object()), "DiffBudgets"),
        ("cancellation", cast("DiffCancellation", object()), "DiffCancellation"),
    )
    for field, value, message in replacements:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(valid, **{field: value})


def test_count_charges_are_cumulative_atomic_and_typed() -> None:
    diff_meter = meter(work=2, pairs=1, facts=1)
    assert diff_meter.charge_work(2) is None
    work_before = diff_meter.budgets
    assert diff_meter.charge_work() is not None
    assert diff_meter.budgets == work_before
    assert diff_meter.charge_pair() is None
    assert diff_meter.charge_pair() is not None
    assert diff_meter.charge_fact() is None
    assert diff_meter.charge_fact() is not None
    for invalid in (0, -1, True):
        with pytest.raises(ValueError, match="positive"):
            diff_meter.charge_work(invalid)


def test_fact_capacity_preview_reports_exact_failure_without_mutation() -> None:
    available = meter(facts=1)
    before = available.budgets
    assert available.fact_exhaustion() is None
    assert available.budgets == before

    exhausted = meter(facts=0)
    before = exhausted.budgets
    failure = exhausted.fact_exhaustion()
    assert failure is not None
    assert failure.budget.resource is BudgetResource.FACTS
    assert exhausted.budgets == before


def test_derived_charge_rolls_back_when_either_dimension_exhausts() -> None:
    values_full = meter(derived_limit=0)
    before = values_full.budgets
    failure = values_full.charge_derived(1)
    assert failure is not None
    assert failure.budget.resource is BudgetResource.DERIVED_VALUES
    assert values_full.budgets == before

    bytes_full = meter(derived_limit=1, derived_bytes=0)
    before = bytes_full.budgets
    failure = bytes_full.charge_derived(1)
    assert failure is not None
    assert failure.budget.resource is BudgetResource.DERIVED_BYTES
    assert bytes_full.budgets == before
    assert bytes_full.charge_derived(0) is None
    for invalid in (-1, True):
        with pytest.raises(ValueError, match="non-negative"):
            bytes_full.charge_derived(invalid)


def test_derived_capacity_preview_reports_failure_without_mutation() -> None:
    available = meter(derived_limit=1, derived_bytes=1)
    before = available.budgets
    assert available.derived_exhaustion(1) is None
    assert available.budgets == before

    exhausted = meter(derived_limit=0)
    before = exhausted.budgets
    failure = exhausted.derived_exhaustion(1)
    assert failure is not None
    assert failure.budget.resource is BudgetResource.DERIVED_VALUES
    assert exhausted.budgets == before


def test_evidence_capacity_preview_and_charge_are_exact_and_nonmutating() -> None:
    diff_meter = meter(evidence=1, evidence_bytes=2)
    assert diff_meter.evidence_capacity() == (1, 2)
    before = diff_meter.budgets
    assert diff_meter.evidence_exhaustion(3) is not None
    assert diff_meter.budgets == before
    assert diff_meter.charge_evidence(2) is None
    assert diff_meter.evidence_capacity() == (0, 0)
    before = diff_meter.budgets
    failure = diff_meter.charge_evidence(0)
    assert failure is not None
    assert failure.budget.resource is BudgetResource.EVIDENCE_EXAMPLES
    assert diff_meter.budgets == before
    for invalid in (-1, True):
        with pytest.raises(ValueError, match="non-negative"):
            diff_meter.charge_evidence(invalid)


def test_evidence_byte_exhaustion_rolls_back_example_usage() -> None:
    diff_meter = meter(evidence=1, evidence_bytes=0)
    before = diff_meter.budgets
    failure = diff_meter.charge_evidence(1)
    assert failure is not None
    assert failure.budget.resource is BudgetResource.EVIDENCE_BYTES
    assert diff_meter.budgets == before


def test_meter_exposes_the_exact_caller_owned_budget_snapshot() -> None:
    budgets = control().budgets
    assert DiffMeter(budgets).budgets is budgets
    assert budgets.derived_values.scope_kind is BudgetScopeKind.RUN
