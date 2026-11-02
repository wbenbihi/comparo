"""Behavior-layer access to exact domain JSONPath rendering."""

from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath_derivation import canonical_jsonpath as _canonical


def canonical_jsonpath(ast: JsonPathAst) -> str:
    """Render canonical JSONPath syntax derived solely from immutable AST."""
    return _canonical(ast)
