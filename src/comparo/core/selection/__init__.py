"""Pure shared selector parsing and evaluation behavior."""

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

__all__ = [
    "DEFAULT_SELECTOR_PARSER_LIMITS",
    "PathCompileFailure",
    "PathCompileReady",
    "PathCompileResult",
    "SelectorParserLimits",
    "SelectorSyntaxCode",
    "SelectorSyntaxError",
    "compile_json_pointer",
    "compile_jsonpath",
    "select",
    "try_compile_json_pointer",
    "try_compile_jsonpath",
]
