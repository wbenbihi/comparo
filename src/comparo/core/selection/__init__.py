"""Pure shared selector parsing and evaluation behavior."""

from comparo.core.selection.accounting import SelectionBudgets
from comparo.core.selection.accounting import SelectionCancellation
from comparo.core.selection.accounting import SelectionControl
from comparo.core.selection.accounting import SelectionEvaluation
from comparo.core.selection.accounting import selection_budget_operations
from comparo.core.selection.compile import compile_json_pointer
from comparo.core.selection.compile import compile_jsonpath
from comparo.core.selection.engine import select
from comparo.core.selection.errors import SelectorSyntaxCode
from comparo.core.selection.errors import SelectorSyntaxError
from comparo.core.selection.limits import DEFAULT_SELECTOR_PARSER_LIMITS
from comparo.core.selection.limits import SelectorParserLimits
from comparo.core.selection.results import PathCompileFailure
from comparo.core.selection.results import PathCompileReady
from comparo.core.selection.results import PathCompileResult
from comparo.core.selection.results import try_compile_json_pointer
from comparo.core.selection.results import try_compile_jsonpath
from comparo.core.selection.snapshot import FailedBodyView
from comparo.core.selection.snapshot import ResolvedBodyBytes
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.snapshot import SelectionSnapshotRequirements

__all__ = (
    "DEFAULT_SELECTOR_PARSER_LIMITS",
    "FailedBodyView",
    "PathCompileFailure",
    "PathCompileReady",
    "PathCompileResult",
    "ResolvedBodyBytes",
    "ResolvedBodyView",
    "SelectionBudgets",
    "SelectionCancellation",
    "SelectionControl",
    "SelectionEvaluation",
    "SelectionSnapshot",
    "SelectionSnapshotRequirements",
    "SelectorParserLimits",
    "SelectorSyntaxCode",
    "SelectorSyntaxError",
    "compile_json_pointer",
    "compile_jsonpath",
    "select",
    "selection_budget_operations",
    "try_compile_json_pointer",
    "try_compile_jsonpath",
)
