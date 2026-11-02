"""Shared projection of explicit timing outcomes into selector values."""

from __future__ import annotations

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.primitives import Duration
from comparo.core.domain.selectors.results import SelectionFactUnavailable


def select_timing_value(metric: TimingMetric) -> Duration | SelectionFactUnavailable:
    """Preserve measured duration or a stable reason for an unavailable timing fact."""
    if type(metric) is Measured:
        return metric.duration
    if type(metric) is NotPerformed:
        reason = metric.reason.value.replace("-", ".")
        return SelectionFactUnavailable(
            code=DiagnosticCode(value=f"selection.timing.not-performed.{reason}")
        )
    reason = metric.reason.value.replace("-", ".")
    return SelectionFactUnavailable(
        code=DiagnosticCode(value=f"selection.timing.unavailable.{reason}")
    )


__all__ = ("select_timing_value",)
