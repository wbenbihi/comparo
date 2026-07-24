"""Pure assertion evaluation over immutable selection snapshots."""

from comparo.core.assertions.accounting import AssertionBudgets
from comparo.core.assertions.accounting import assertion_budget_operations
from comparo.core.assertions.bindings import capability_implementation
from comparo.core.assertions.bindings import operands_fingerprint
from comparo.core.assertions.bindings import operation_fingerprint
from comparo.core.assertions.bindings import selected_fingerprint
from comparo.core.assertions.engine import AssertionControl
from comparo.core.assertions.engine import AssertionEvaluation
from comparo.core.assertions.engine import evaluate_assertion
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.inputs import PreparedCapabilityKind
from comparo.core.assertions.inputs import PreparedExpression
from comparo.core.assertions.inputs import PreparedOperandError
from comparo.core.assertions.inputs import PreparedOperandReady
from comparo.core.assertions.inputs import PreparedOperandUnavailable
from comparo.core.assertions.inputs import PreparedPredicate
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.assertions.inputs import required_expressions

__all__ = (
    "AssertionBudgets",
    "AssertionControl",
    "AssertionEvaluation",
    "PreparedAssertionInputs",
    "PreparedCapabilityKind",
    "PreparedExpression",
    "PreparedOperandError",
    "PreparedOperandReady",
    "PreparedOperandUnavailable",
    "PreparedPredicate",
    "PreparedPredicateOutcome",
    "assertion_budget_operations",
    "capability_implementation",
    "evaluate_assertion",
    "operands_fingerprint",
    "operation_fingerprint",
    "required_expressions",
    "selected_fingerprint",
)
