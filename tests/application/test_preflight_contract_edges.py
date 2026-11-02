"""Fail-closed runtime and evaluation preflight branch coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from comparo.application.evaluation_binding import EvaluationPreflightResult
from comparo.application.preflight import inspect_project_plan
from comparo.application.runtime_capabilities import RuntimeCapabilityPreflightResult
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from tests.application.test_preflight import _request
from tests.application.test_preflight import _write_project


def _diagnostic(code: str) -> Diagnostic:
    return Diagnostic(
        code=DiagnosticCode(value=code),
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.EVALUATION,
    )


def test_project_inspection_returns_runtime_capability_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = _diagnostic("runtime.capability.unsupported")
    monkeypatch.setattr(
        "comparo.application.runtime_capabilities.preflight_runtime_capabilities",
        lambda _project: RuntimeCapabilityPreflightResult(diagnostics=(diagnostic,)),
    )
    result = inspect_project_plan(_request(_write_project(tmp_path)))
    assert result.inspection is None
    assert tuple(item.code for item in result.diagnostics) == (diagnostic.code.value,)


def test_project_inspection_returns_evaluation_preflight_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = _diagnostic("evaluation.capability.unsupported")
    rejected = EvaluationPreflightResult(
        diagnostics=(diagnostic,),
        rejected=(),
        assertion_rules=0,
        diff_rules=0,
        gate=None,
    )
    monkeypatch.setattr(
        "comparo.application.preflight.preflight_configuration_evaluation",
        lambda *_args, **_kwargs: rejected,
    )
    result = inspect_project_plan(_request(_write_project(tmp_path)))
    assert result.inspection is None
    assert tuple(item.code for item in result.diagnostics) == (diagnostic.code.value,)
