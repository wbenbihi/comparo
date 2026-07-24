"""Cover value-free gate evaluation assessment invariants."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.gates.model import GateCoverageAssessment
from comparo.core.domain.gates.model import GateEvaluation
from comparo.core.domain.gates.model import GateFailureReason
from comparo.core.domain.gates.model import GateResult
from comparo.core.domain.gates.model import GateRuleCounts
from comparo.core.domain.gates.model import GateSeverityAssessment
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.gates.model import derive_gate_failure_reasons
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.primitives import Count
from tests.core.domain.gates.test_invariants import _definition


def _counts(value: int = 0) -> GateRuleCounts:
    count = Count(value=value)
    return GateRuleCounts(
        violated_rules=count,
        drifted_rules=count,
        unavailable_rules=count,
        incomparable_rules=count,
        incomplete_rules=count,
        error_rules=count,
    )


def _coverage() -> GateCoverageAssessment:
    return GateCoverageAssessment(
        run_complete=True,
        complete_run_required=True,
        assertion_coverage_required=True,
        diff_coverage_required=True,
        eligible_assertion_rules=Count(value=1),
        covered_assertion_rules=Count(value=1),
        eligible_diff_rules=Count(value=1),
        covered_diff_rules=Count(value=1),
    )


def _severity(severity: GateSeverity = GateSeverity.BLOCKING) -> GateSeverityAssessment:
    return GateSeverityAssessment(severity=severity, actual=_counts(), allowed=_counts())


def test_assessment_contracts_are_closed_and_conserved() -> None:
    counts = _counts()
    for field in (
        "violated_rules",
        "drifted_rules",
        "unavailable_rules",
        "incomparable_rules",
        "incomplete_rules",
        "error_rules",
    ):
        with pytest.raises(TypeError, match="Count"):
            msgspec.structs.replace(counts, **{field: cast("Count", 0)})
    with pytest.raises(TypeError, match="GateSeverity"):
        msgspec.structs.replace(_severity(), severity=cast("GateSeverity", "blocking"))
    with pytest.raises(TypeError, match="GateRuleCounts"):
        msgspec.structs.replace(_severity(), actual=cast("GateRuleCounts", "bad"))
    with pytest.raises(TypeError, match="GateRuleCounts"):
        msgspec.structs.replace(_severity(), allowed=cast("GateRuleCounts", "bad"))

    coverage = _coverage()
    for field in (
        "run_complete",
        "complete_run_required",
        "assertion_coverage_required",
        "diff_coverage_required",
    ):
        with pytest.raises(TypeError, match="bool"):
            msgspec.structs.replace(coverage, **{field: cast("bool", 1)})
    for field in (
        "eligible_assertion_rules",
        "covered_assertion_rules",
        "eligible_diff_rules",
        "covered_diff_rules",
    ):
        with pytest.raises(TypeError, match="Count"):
            msgspec.structs.replace(coverage, **{field: cast("Count", 1)})
    with pytest.raises(ValueError, match="covered assertion"):
        msgspec.structs.replace(coverage, eligible_assertion_rules=Count(value=0))
    with pytest.raises(ValueError, match="covered diff"):
        msgspec.structs.replace(coverage, eligible_diff_rules=Count(value=0))


def test_not_requested_and_requested_evaluation_shapes_are_disjoint() -> None:
    not_requested = GateEvaluation(
        result=GateResult(verdict=GateVerdict.NOT_REQUESTED, profile=None),
        coverage=None,
    )
    with pytest.raises(ValueError, match="cannot retain"):
        msgspec.structs.replace(not_requested, coverage=_coverage())
    with pytest.raises(ValueError, match="cannot retain"):
        msgspec.structs.replace(not_requested, severities=(_severity(),))

    requested = GateEvaluation(
        result=GateResult(verdict=GateVerdict.PASSED, profile=_definition()),
        coverage=_coverage(),
        severities=(_severity(),),
    )
    with pytest.raises(ValueError, match="requires coverage"):
        msgspec.structs.replace(requested, coverage=None)
    with pytest.raises(ValueError, match="requires coverage"):
        msgspec.structs.replace(requested, severities=())
    with pytest.raises(TypeError, match="GateResult"):
        msgspec.structs.replace(requested, result=cast("GateResult", "bad"))
    with pytest.raises(TypeError, match="GateCoverageAssessment"):
        msgspec.structs.replace(
            requested,
            coverage=cast("GateCoverageAssessment", "bad"),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        msgspec.structs.replace(
            requested,
            severities=cast("tuple[GateSeverityAssessment, ...]", [_severity()]),
        )
    with pytest.raises(TypeError, match="unsupported severity"):
        msgspec.structs.replace(
            requested,
            severities=(cast("GateSeverityAssessment", "bad"),),
        )


def test_severity_assessments_are_unique_and_canonical() -> None:
    requested = GateEvaluation(
        result=GateResult(verdict=GateVerdict.PASSED, profile=_definition()),
        coverage=_coverage(),
        severities=(_severity(),),
    )
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(requested, severities=(_severity(), _severity()))
    with pytest.raises(ValueError, match="canonical"):
        msgspec.structs.replace(
            requested,
            severities=(
                _severity(GateSeverity.INFO),
                _severity(GateSeverity.BLOCKING),
            ),
        )


def test_gate_result_rejects_noncanonical_failure_reason_permutations() -> None:
    with pytest.raises(ValueError, match="canonical order"):
        GateResult(
            verdict=GateVerdict.FAILED,
            profile=_definition(),
            reasons=(
                GateFailureReason.ERROR_LIMIT,
                GateFailureReason.RUN_INCOMPLETE,
            ),
        )


def test_gate_evaluation_rejects_verdicts_that_contradict_assessments() -> None:
    adverse = GateSeverityAssessment(
        severity=GateSeverity.BLOCKING,
        actual=msgspec.structs.replace(_counts(), violated_rules=Count(value=1)),
        allowed=_counts(),
    )
    failed = GateEvaluation(
        result=GateResult(
            verdict=GateVerdict.FAILED,
            profile=_definition(),
            reasons=(GateFailureReason.ASSERTION_VIOLATION_LIMIT,),
        ),
        coverage=_coverage(),
        severities=(adverse,),
    )

    with pytest.raises(ValueError, match="must match"):
        msgspec.structs.replace(
            failed,
            result=GateResult(verdict=GateVerdict.PASSED, profile=_definition()),
        )
    with pytest.raises(ValueError, match="must match"):
        msgspec.structs.replace(failed, severities=(_severity(),))
    with pytest.raises(ValueError, match="must match"):
        GateEvaluation(
            result=GateResult(verdict=GateVerdict.PASSED, profile=_definition()),
            coverage=msgspec.structs.replace(_coverage(), run_complete=False),
            severities=(_severity(),),
        )

    serialized = msgspec.to_builtins(failed)
    assert isinstance(serialized, dict)
    serialized_result = serialized["result"]
    assert isinstance(serialized_result, dict)
    serialized_result["verdict"] = GateVerdict.PASSED.value
    serialized_result["reasons"] = []
    decoder = msgspec.json.Decoder(GateEvaluation)
    with pytest.raises(msgspec.ValidationError, match="must match"):
        decoder.decode(msgspec.json.encode(serialized))


def test_gate_failure_reason_derivation_rejects_untrusted_inputs() -> None:
    with pytest.raises(TypeError, match="GateCoverageAssessment"):
        derive_gate_failure_reasons(cast("GateCoverageAssessment", "bad"), (_severity(),))
    with pytest.raises(TypeError, match="GateSeverityAssessment"):
        derive_gate_failure_reasons(_coverage(), cast("tuple[GateSeverityAssessment, ...]", []))
    with pytest.raises(TypeError, match="GateSeverityAssessment"):
        derive_gate_failure_reasons(
            _coverage(),
            (cast("GateSeverityAssessment", "bad"),),
        )
