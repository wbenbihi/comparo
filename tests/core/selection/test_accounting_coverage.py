"""Close branch coverage for selection controls, engine checks, and match charging."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonString
from comparo.core.selection import accounting
from comparo.core.selection import engine
from comparo.core.selection import match_accounting
from comparo.core.selection.accounting import SelectionBudgets
from comparo.core.selection.accounting import SelectionCancellation
from comparo.core.selection.accounting import SelectionControl
from comparo.core.selection.accounting import SelectionEvaluation
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.engine import select
from comparo.core.selection.match_accounting import account_match_before_append
from comparo.core.selection.match_accounting import charge_work
from comparo.core.selection.snapshot import SelectionSnapshot
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.stream_factories import single_stream_execution
from tests.core.selection.json_evaluator_factories import selection_meter


def _selector() -> CompiledSelector:
    path = NoPath()
    capabilities = derive_selector_capabilities(SelectorSource.RESPONSE_STATUS, path)
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="9" * 64),
        ),
        source=SelectorSource.RESPONSE_STATUS,
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.INTEGER,),
            cardinality=capabilities.cardinality,
        ),
    )


def _control(*, cancelled: bool = False) -> SelectionControl:
    budgets = selection_meter().budgets
    return SelectionControl(
        budgets=budgets,
        cancellation=SelectionCancellation(
            cancelled=cancelled,
            code=DiagnosticCode(value="selection.cancelled"),
        ),
    )


def _match(*, sensitive: bool = False) -> SelectionMatch:
    owner = EnvironmentExecutionId(value=UUID(int=700))
    body_id = BodyId(value=UUID(int=701))
    value = JsonString(value="selected")
    selected = (
        SensitiveValue(value, origins=(TaintOriginId(value="test.secret"),)) if sensitive else value
    )
    return SelectionMatch(
        value=selected,
        location=BodyLocation(
            environment_execution_id=owner,
            source=SelectorSource.RESPONSE_BODY,
            body_id=body_id,
            body_view=BodyViewKind.HANDLE,
        ),
    )


def test_budget_validator_rejects_type_stage_operation_resource_and_unit() -> None:
    budgets = selection_meter().budgets
    work_operation = budgets.work.operation.value
    with pytest.raises(TypeError, match="ResourceBudget"):
        accounting._require_budget(
            "bad",
            BudgetResource.SELECTION_WORK,
            Count,
            work_operation,
        )
    decoding = view_exhaustion(ViewBudgetKind.JSON_NODES).budget
    with pytest.raises(ValueError, match="selection stage"):
        accounting._require_budget(
            decoding,
            decoding.resource,
            Count,
            decoding.operation.value,
        )
    with pytest.raises(TypeError, match="wrong unit"):
        accounting._require_budget(
            budgets.work,
            budgets.work.resource,
            ByteCount,
            budgets.work.operation.value,
        )

    wrong_operation = msgspec.structs.replace(
        budgets.work,
        operation=msgspec.structs.replace(
            budgets.work.operation,
            value="selector.evaluate.other",
        ),
    )
    with pytest.raises(ValueError, match="operation"):
        SelectionBudgets(
            work=wrong_operation,
            matches=budgets.matches,
            result_bytes=budgets.result_bytes,
        )


def test_control_carriers_and_meter_reject_untyped_inputs() -> None:
    budgets = selection_meter().budgets
    cancellation = SelectionCancellation(
        cancelled=False,
        code=DiagnosticCode(value="selection.cancelled"),
    )
    with pytest.raises(TypeError, match="bool"):
        msgspec.structs.replace(cancellation, cancelled=cast("bool", 1))
    with pytest.raises(TypeError, match="DiagnosticCode"):
        msgspec.structs.replace(cancellation, code=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError, match="SelectionBudgets"):
        SelectionControl(
            budgets=cast("SelectionBudgets", "bad"),
            cancellation=cancellation,
        )
    with pytest.raises(TypeError, match="SelectionCancellation"):
        SelectionControl(
            budgets=budgets,
            cancellation=cast("SelectionCancellation", "bad"),
        )
    with pytest.raises(TypeError, match="SelectionBudgets"):
        SelectionEvaluation(
            result=SelectionUnsupported(code=DiagnosticCode(value="selection.unsupported")),
            budgets=cast("SelectionBudgets", "bad"),
        )
    with pytest.raises(TypeError, match="SelectionBudgets"):
        SelectionMeter(cast("SelectionBudgets", "bad"))


def test_meter_rejects_bool_and_nonadvancing_count_or_byte_charges() -> None:
    meter = selection_meter()
    for amount in (0, -1, True):
        with pytest.raises(ValueError, match="positive"):
            meter.charge_match(amount)
    with pytest.raises(ValueError, match="non-negative"):
        meter.charge_result_bytes(cast("int", True))


def test_engine_validates_each_boundary_and_runs_non_cancelled_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = SelectionSnapshot(observation=single_stream_execution(ViewKind.SSE))
    selector = _selector()
    control = _control()
    expected = SelectionUnsupported(code=DiagnosticCode(value="selection.test"))
    monkeypatch.setattr(engine, "evaluate_source", lambda *_args: expected)

    evaluation = select(snapshot, selector, control)
    assert evaluation.result is expected
    assert evaluation.budgets == control.budgets

    with pytest.raises(TypeError, match="SelectionSnapshot"):
        select(cast("SelectionSnapshot", "bad"), selector, control)
    with pytest.raises(TypeError, match="CompiledSelector"):
        select(snapshot, cast("CompiledSelector", "bad"), control)
    with pytest.raises(TypeError, match="SelectionControl"):
        select(snapshot, selector, cast("SelectionControl", "bad"))


def test_match_accounting_covers_match_size_bytes_and_sensitive_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert type(account_match_before_append(_match(), selection_meter(matches=0))) is (
        SelectionBudgetExceeded
    )
    assert type(account_match_before_append(_match(), selection_meter(result_bytes=0))) is (
        SelectionBudgetExceeded
    )
    assert account_match_before_append(_match(sensitive=True), selection_meter()) is None

    upstream = charge_work(selection_meter(work=0))
    assert type(upstream) is SelectionBudgetExceeded
    monkeypatch.setattr(match_accounting, "selected_value_size", lambda *_args: upstream)
    assert account_match_before_append(_match(), selection_meter()) is upstream


def test_work_adapter_returns_none_or_canonical_exhaustion() -> None:
    assert charge_work(selection_meter(), 2) is None
    assert type(charge_work(selection_meter(work=1), 2)) is SelectionBudgetExceeded


def test_budget_type_alias_is_a_real_resource_budget() -> None:
    """Keep the test-only cast import honest under strict static checking."""
    assert isinstance(selection_meter().budgets.work, ResourceBudget)
