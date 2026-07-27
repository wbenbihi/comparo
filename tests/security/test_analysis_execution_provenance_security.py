"""Adversarial ownership checks for external-analysis terminal facts."""

from typing import cast
from typing import get_type_hints

import msgspec
import pytest

from comparo.core.domain.analysis import AnalysisBudgetExhausted
from comparo.core.domain.analysis import AnalysisBudgetUsageReport
from comparo.core.domain.analysis import AnalysisCancelled
from comparo.core.domain.analysis import AnalysisExecutionFact
from comparo.core.domain.analysis import AnalysisExecutor
from comparo.core.domain.analysis import AnalysisSucceeded
from comparo.core.domain.analysis import AnalysisTimedOut
from comparo.core.domain.analysis import AnalysisVerdict
from comparo.core.domain.analysis import AuthorizedAnalysisRequest
from comparo.core.domain.analysis import validate_analysis_execution
from comparo.core.domain.analysis_results import AnalysisExecutionResult
from comparo.core.domain.analysis_types import AnalysisContractVersion
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetExhaustionEffect
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from tests.core.domain.analysis_factories import _analysis_id
from tests.core.domain.analysis_factories import _analysis_scope
from tests.core.domain.analysis_factories import _budget
from tests.core.domain.analysis_factories import _grant
from tests.core.domain.analysis_factories import _request
from tests.core.domain.analysis_factories import _usage_report


def _authorized() -> AuthorizedAnalysisRequest:
    request = _request()
    return AuthorizedAnalysisRequest(grant=_grant(request))


def _success() -> AnalysisSucceeded:
    return AnalysisSucceeded(
        verdict=AnalysisVerdict.EQUIVALENT,
        score=None,
        evidence=(),
    )


def test_executor_contract_returns_a_request_bound_fact() -> None:
    hints = get_type_hints(AnalysisExecutor.execute)
    assert hints["request"] is AuthorizedAnalysisRequest
    assert hints["return"] is AnalysisExecutionFact


def test_terminal_fact_cannot_be_replayed_for_another_authorized_request() -> None:
    original = _authorized()
    changed_analysis = msgspec.structs.replace(
        original.analysis,
        provider=msgspec.structs.replace(
            original.analysis.provider,
            model_version=AnalysisContractVersion(value="other"),
        ),
    )
    changed = AuthorizedAnalysisRequest(grant=_grant(changed_analysis))
    fact = AnalysisExecutionFact(
        request=original,
        result=_success(),
        usage=_usage_report(original.analysis),
    )

    validate_analysis_execution(original, fact)
    with pytest.raises(ValueError, match="different authorized request"):
        validate_analysis_execution(changed, fact)


@pytest.mark.parametrize(
    "result",
    [_success(), AnalysisCancelled(source=CancellationSource.EXECUTION_POLICY)],
)
def test_success_and_failure_usage_cannot_be_replayed_across_analysis_coordinates(
    result: AnalysisExecutionResult,
) -> None:
    authorized = _authorized()
    foreign_id = _analysis_id(99)
    foreign_analysis = msgspec.structs.replace(
        authorized.analysis,
        analysis_id=foreign_id,
        budgets=tuple(
            msgspec.structs.replace(
                budget,
                scope=_analysis_scope(analysis_id=foreign_id),
            )
            for budget in authorized.analysis.budgets
        ),
    )
    with pytest.raises(ValueError, match="authorized budget contract"):
        AnalysisExecutionFact(
            request=authorized,
            result=result,
            usage=_usage_report(foreign_analysis),
        )


def test_budget_exhaustion_must_use_the_authorized_analysis_coordinate() -> None:
    authorized = _authorized()
    foreign_budget = _budget(
        BudgetResource.EXTERNAL_WORK,
        Count(value=4),
        "analysis.work",
        scope=_analysis_scope(analysis_id=_analysis_id(99)),
    )
    foreign_exhaustion = AnalysisBudgetExhausted(
        exhaustion=BudgetExceeded(
            budget=foreign_budget,
            attempted=Count(value=5),
            effect=BudgetExhaustionEffect.EXTERNAL_CAPABILITY_UNAVAILABLE,
        )
    )

    with pytest.raises(ValueError, match="authorized request budget"):
        AnalysisExecutionFact(
            request=authorized,
            result=foreign_exhaustion,
            usage=_usage_report(authorized.analysis),
        )


def test_budget_exhaustion_must_equal_the_terminal_usage_snapshot() -> None:
    authorized = _authorized()
    report = _usage_report(authorized.analysis)
    terminal_work = report.snapshots[3]
    exhausted = AnalysisBudgetExhausted(
        exhaustion=BudgetExceeded(
            budget=terminal_work,
            attempted=Count(value=5),
            effect=BudgetExhaustionEffect.EXTERNAL_CAPABILITY_UNAVAILABLE,
        )
    )
    fact = AnalysisExecutionFact(request=authorized, result=exhausted, usage=report)
    assert fact.usage.snapshots[3] == terminal_work

    stale = AnalysisBudgetExhausted(
        exhaustion=msgspec.structs.replace(
            exhausted.exhaustion,
            budget=authorized.analysis.budgets[3],
        )
    )
    with pytest.raises(ValueError, match="equal its terminal usage snapshot"):
        AnalysisExecutionFact(request=authorized, result=stale, usage=report)


def test_timeout_fact_must_reach_the_authorized_deadline() -> None:
    authorized = _authorized()
    assert authorized.analysis.timeout.nanoseconds == 100
    with pytest.raises(ValueError, match="reach the authorized timeout"):
        AnalysisExecutionFact(
            request=authorized,
            result=AnalysisTimedOut(elapsed=Duration(nanoseconds=99)),
            usage=_usage_report(authorized.analysis, deadline_nanoseconds=100),
        )
    assert (
        AnalysisExecutionFact(
            request=authorized,
            result=AnalysisTimedOut(elapsed=Duration(nanoseconds=100)),
            usage=_usage_report(authorized.analysis, deadline_nanoseconds=100),
        ).request
        is authorized
    )
    with pytest.raises(ValueError, match="backed by terminal deadline usage"):
        AnalysisExecutionFact(
            request=authorized,
            result=AnalysisTimedOut(elapsed=Duration(nanoseconds=100)),
            usage=_usage_report(authorized.analysis, deadline_nanoseconds=99),
        )


def test_success_usage_cannot_hide_input_output_or_external_work() -> None:
    authorized = _authorized()
    with pytest.raises(ValueError, match="equal declared analysis input bytes"):
        AnalysisExecutionFact(
            request=authorized,
            result=_success(),
            usage=_usage_report(authorized.analysis, input_bytes=0),
        )
    with pytest.raises(ValueError, match="nonzero work"):
        AnalysisExecutionFact(
            request=authorized,
            result=_success(),
            usage=_usage_report(authorized.analysis, work=0),
        )
    with pytest.raises(ValueError, match="nonempty output"):
        AnalysisExecutionFact(
            request=authorized,
            result=_success(),
            usage=_usage_report(authorized.analysis, output_bytes=0),
        )

    empty_allowed_analysis = msgspec.structs.replace(
        authorized.analysis,
        output_schema=msgspec.structs.replace(
            authorized.analysis.output_schema,
            allows_empty_output=True,
        ),
    )
    empty_allowed = AuthorizedAnalysisRequest(grant=_grant(empty_allowed_analysis))
    assert (
        AnalysisExecutionFact(
            request=empty_allowed,
            result=_success(),
            usage=_usage_report(empty_allowed.analysis, output_bytes=0),
        ).result
        == _success()
    )


def test_analysis_execution_fact_rejects_untyped_components() -> None:
    authorized = _authorized()
    with pytest.raises(TypeError, match="AuthorizedAnalysisRequest"):
        AnalysisExecutionFact(
            request=cast("AuthorizedAnalysisRequest", object()),
            result=_success(),
            usage=_usage_report(authorized.analysis),
        )
    with pytest.raises(TypeError, match="typed terminal result"):
        AnalysisExecutionFact(
            request=authorized,
            result=cast("AnalysisSucceeded", object()),
            usage=_usage_report(authorized.analysis),
        )
    with pytest.raises(TypeError, match="AnalysisBudgetUsageReport"):
        AnalysisExecutionFact(
            request=authorized,
            result=_success(),
            usage=cast("AnalysisBudgetUsageReport", object()),
        )
    with pytest.raises(TypeError, match="AuthorizedAnalysisRequest"):
        validate_analysis_execution(
            cast("AuthorizedAnalysisRequest", object()),
            AnalysisExecutionFact(
                request=authorized,
                result=_success(),
                usage=_usage_report(authorized.analysis),
            ),
        )
    with pytest.raises(TypeError, match="AnalysisExecutionFact"):
        validate_analysis_execution(
            authorized,
            cast("AnalysisExecutionFact", object()),
        )
