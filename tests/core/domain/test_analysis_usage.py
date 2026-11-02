"""Validate terminal external-analysis usage snapshots and conservation."""

from typing import cast

import msgspec
import pytest

from comparo.core.domain.analysis import AnalysisBudgetUsageReport
from comparo.core.domain.analysis_usage import validate_analysis_usage_report
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.primitives import ByteCount
from tests.core.domain.analysis_factories import _request
from tests.core.domain.analysis_factories import _usage_report


def test_analysis_usage_report_requires_closed_unique_snapshots() -> None:
    request = _request()
    report = _usage_report(request)
    with pytest.raises(TypeError, match="immutable tuple"):
        AnalysisBudgetUsageReport(
            snapshots=cast("tuple[ResourceBudget, ...]", list(report.snapshots)),
        )
    with pytest.raises(TypeError, match="ResourceBudget"):
        AnalysisBudgetUsageReport(
            snapshots=cast("tuple[ResourceBudget, ...]", (object(),)),
        )
    with pytest.raises(ValueError, match="each resource at most once"):
        AnalysisBudgetUsageReport(snapshots=(report.snapshots[0], report.snapshots[0]))
    live_output = msgspec.structs.replace(
        report.snapshots[1],
        usage=msgspec.structs.replace(
            report.snapshots[1].usage,
            in_use=ByteCount(value=1),
        ),
    )
    with pytest.raises(ValueError, match="live reservations"):
        AnalysisBudgetUsageReport(
            snapshots=(report.snapshots[0], live_output, *report.snapshots[2:]),
        )


def test_analysis_usage_reports_every_exact_authorized_contract() -> None:
    request = _request()
    report = _usage_report(request)
    with pytest.raises(TypeError, match="AnalysisBudgetUsageReport"):
        validate_analysis_usage_report(
            request.budgets,
            cast("AnalysisBudgetUsageReport", object()),
            expected_input_bytes=ByteCount(value=12),
        )
    with pytest.raises(ValueError, match="every authorized budget"):
        validate_analysis_usage_report(
            request.budgets,
            AnalysisBudgetUsageReport(snapshots=report.snapshots[:-1]),
            expected_input_bytes=ByteCount(value=12),
        )
    changed = msgspec.structs.replace(
        report.snapshots[0],
        operation=BudgetOperation(value="analysis.replayed-input"),
    )
    with pytest.raises(ValueError, match="authorized budget contract"):
        validate_analysis_usage_report(
            request.budgets,
            AnalysisBudgetUsageReport(snapshots=(changed, *report.snapshots[1:])),
            expected_input_bytes=ByteCount(value=12),
        )
    with pytest.raises(TypeError, match="expected analysis input extent"):
        validate_analysis_usage_report(
            request.budgets,
            report,
            expected_input_bytes=cast("ByteCount", object()),
        )
    with pytest.raises(ValueError, match="equal declared analysis input bytes"):
        validate_analysis_usage_report(
            request.budgets,
            _usage_report(request, input_bytes=0),
            expected_input_bytes=ByteCount(value=12),
        )


@pytest.mark.parametrize("field", ["consumed", "high_water"])
def test_analysis_usage_cumulative_and_high_water_values_are_monotonic(field: str) -> None:
    request = _request()
    initial_usage = BudgetUsage(
        consumed=ByteCount(value=1 if field == "consumed" else 0),
        in_use=ByteCount(value=0),
        high_water=ByteCount(value=1 if field == "high_water" else 0),
    )
    initial = msgspec.structs.replace(request.budgets[0], usage=initial_usage)
    terminal = msgspec.structs.replace(
        request.budgets[0],
        usage=BudgetUsage(
            consumed=ByteCount(value=0),
            in_use=ByteCount(value=0),
            high_water=ByteCount(value=0),
        ),
    )
    with pytest.raises(ValueError, match="cannot decrease"):
        validate_analysis_usage_report(
            (initial,),
            AnalysisBudgetUsageReport(snapshots=(terminal,)),
            expected_input_bytes=ByteCount(value=12),
        )
