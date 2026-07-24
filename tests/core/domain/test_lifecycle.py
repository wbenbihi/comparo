"""Tests for run lifecycle outcomes independent from gates."""

from typing import cast

import pytest

from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.lifecycle import PartialRunReason
from comparo.core.domain.lifecycle import RunCancelled
from comparo.core.domain.lifecycle import RunComplete
from comparo.core.domain.lifecycle import RunInternalError
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.lifecycle import RunRejected
from comparo.core.domain.primitives import UtcInstant


def _diagnostic(severity: DiagnosticSeverity = DiagnosticSeverity.ERROR) -> Diagnostic:
    return Diagnostic(
        code=DiagnosticCode(value="run.admission-failed"),
        severity=severity,
        stage=DiagnosticStage.PLANNING,
    )


def test_complete_requires_an_exact_instant() -> None:
    instant = UtcInstant(unix_nanoseconds=10)
    assert RunComplete(completed_at=instant).completed_at == instant
    with pytest.raises(TypeError, match="UtcInstant"):
        RunComplete(completed_at=10)  # type: ignore[arg-type]


def test_partial_run_requires_unique_typed_reasons() -> None:
    instant = UtcInstant(unix_nanoseconds=10)
    partial = RunPartial(
        reasons=(PartialRunReason.EXECUTION_INCOMPLETE,),
        completed_at=instant,
    )
    assert partial.reasons == (PartialRunReason.EXECUTION_INCOMPLETE,)
    with pytest.raises(ValueError, match="at least one reason"):
        RunPartial(reasons=(), completed_at=instant)
    with pytest.raises(ValueError, match="must be unique"):
        RunPartial(
            reasons=(
                PartialRunReason.ARTIFACT_INCOMPLETE,
                PartialRunReason.ARTIFACT_INCOMPLETE,
            ),
            completed_at=instant,
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        RunPartial(
            reasons=cast("tuple[PartialRunReason, ...]", [PartialRunReason.EXECUTION_INCOMPLETE]),
            completed_at=instant,
        )
    with pytest.raises(TypeError, match="PartialRunReason"):
        RunPartial(
            reasons=cast("tuple[PartialRunReason, ...]", ("execution-incomplete",)),
            completed_at=instant,
        )
    with pytest.raises(TypeError, match="UtcInstant"):
        RunPartial(
            reasons=(PartialRunReason.EXECUTION_INCOMPLETE,),
            completed_at=cast("UtcInstant", object()),
        )


def test_cancelled_run_preserves_source_stage_and_instant() -> None:
    cancelled = RunCancelled(
        source=CancellationSource.DEADLINE,
        stage=DiagnosticStage.TRANSPORT,
        cancelled_at=UtcInstant(unix_nanoseconds=20),
    )
    assert cancelled.source is CancellationSource.DEADLINE
    with pytest.raises(TypeError, match="CancellationSource"):
        RunCancelled(
            source="deadline",  # type: ignore[arg-type]
            stage=cancelled.stage,
            cancelled_at=cancelled.cancelled_at,
        )
    with pytest.raises(TypeError, match="DiagnosticStage"):
        RunCancelled(
            source=cancelled.source,
            stage=cast("DiagnosticStage", object()),
            cancelled_at=cancelled.cancelled_at,
        )
    with pytest.raises(TypeError, match="UtcInstant"):
        RunCancelled(
            source=cancelled.source,
            stage=cancelled.stage,
            cancelled_at=cast("UtcInstant", object()),
        )


def test_rejection_requires_non_empty_error_diagnostics() -> None:
    assert (
        RunRejected(diagnostics=(_diagnostic(),)).diagnostics[0].stage is DiagnosticStage.PLANNING
    )
    with pytest.raises(TypeError, match="immutable tuple"):
        RunRejected(diagnostics=[_diagnostic()])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one diagnostic"):
        RunRejected(diagnostics=())
    with pytest.raises(ValueError, match="error severity"):
        RunRejected(diagnostics=(_diagnostic(DiagnosticSeverity.WARNING),))
    with pytest.raises(TypeError, match="Diagnostic values"):
        RunRejected(diagnostics=cast("tuple[Diagnostic, ...]", (object(),)))


def test_internal_error_requires_one_structured_error() -> None:
    diagnostic = _diagnostic()
    assert RunInternalError(diagnostic=diagnostic).diagnostic == diagnostic
    with pytest.raises(TypeError, match="Diagnostic"):
        RunInternalError(diagnostic="traceback")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="error severity"):
        RunInternalError(diagnostic=_diagnostic(DiagnosticSeverity.INFO))
