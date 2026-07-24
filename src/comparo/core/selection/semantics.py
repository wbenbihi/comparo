"""Behavior-layer access to exact domain JSONPath capabilities."""

from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath_derivation import derive_jsonpath_capabilities
from comparo.core.domain.selectors.sources import SelectorCapabilities


def validate_and_derive_capabilities(ast: JsonPathAst) -> SelectorCapabilities:
    """Validate static semantics and derive capabilities solely from immutable AST."""
    return derive_jsonpath_capabilities(ast)
