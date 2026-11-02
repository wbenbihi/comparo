"""Public diff call-boundary identity and prepared-input validation."""

from comparo.core.diffs.accounting import DiffControl
from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.diffs.inputs import require_exact_expression_coverage
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.selection import SelectionSnapshot


def validate_diff_inputs(
    baseline: SelectionSnapshot,
    candidate: SelectionSnapshot,
    application: DiffApplication,
    rule: CompiledDiffRule,
    inputs: PreparedDiffInputs,
    control: DiffControl,
) -> None:
    """Bind the pure call to one exact directed application and prepared operand set."""
    if type(baseline) is not SelectionSnapshot or type(candidate) is not SelectionSnapshot:
        raise TypeError("diff evaluation requires two SelectionSnapshot values")
    if type(application) is not DiffApplication or type(rule) is not CompiledDiffRule:
        raise TypeError("diff evaluation requires a DiffApplication and CompiledDiffRule")
    if type(control) is not DiffControl:
        raise TypeError("diff evaluation requires DiffControl")
    if (
        application.definition != rule.definition
        or application.effective_id != rule.effective_id
        or application.rule_fingerprint != rule.fingerprint
    ):
        raise ValueError("diff application does not identify the compiled rule")
    pair = application.pair
    if baseline.observation.execution_id != pair.baseline_execution_id:
        raise ValueError("baseline snapshot does not identify the directed pair")
    if candidate.observation.execution_id != pair.candidate_execution_id:
        raise ValueError("candidate snapshot does not identify the directed pair")
    if baseline.observation.environment != pair.baseline_environment:
        raise ValueError("baseline snapshot Environment contradicts the directed pair")
    if candidate.observation.environment != pair.candidate_environment:
        raise ValueError("candidate snapshot Environment contradicts the directed pair")
    if (
        inputs.comparison_id != pair.comparison_id
        or inputs.baseline_execution_id != pair.baseline_execution_id
        or inputs.candidate_execution_id != pair.candidate_execution_id
    ):
        raise ValueError("prepared diff inputs do not identify the directed pair frames")
    require_exact_expression_coverage(rule, inputs)


__all__ = ("validate_diff_inputs",)
