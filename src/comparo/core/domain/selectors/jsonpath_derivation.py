"""Exact canonical identity and capabilities derived from JSONPath syntax."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import cast

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.jsonpath import AndExpression
from comparo.core.domain.selectors.jsonpath import ChildSegment
from comparo.core.domain.selectors.jsonpath import ComparisonExpression
from comparo.core.domain.selectors.jsonpath import DescendantSegment
from comparo.core.domain.selectors.jsonpath import FilterExpression
from comparo.core.domain.selectors.jsonpath import FilterSelector
from comparo.core.domain.selectors.jsonpath import FunctionExpression
from comparo.core.domain.selectors.jsonpath import IndexSelector
from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath import LiteralExpression
from comparo.core.domain.selectors.jsonpath import NameSelector
from comparo.core.domain.selectors.jsonpath import NotExpression
from comparo.core.domain.selectors.jsonpath import OrExpression
from comparo.core.domain.selectors.jsonpath import QueryExpression
from comparo.core.domain.selectors.jsonpath import QueryOrigin
from comparo.core.domain.selectors.jsonpath import SegmentSelector
from comparo.core.domain.selectors.jsonpath import SliceSelector
from comparo.core.domain.selectors.jsonpath import StandardFunction
from comparo.core.domain.selectors.jsonpath import WildcardSelector
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull

_FINGERPRINT_DOMAIN = b"comparo.selector.rfc9535.compiler-v1\0"
_MAX_EXACT_INDEX = (1 << 53) - 1
_MAX_TRUSTED_DEPTH = 128


class JsonPathAstErrorCode(DomainStrEnum):
    """Stable exact-AST validation failures."""

    TOO_DEEP = "jsonpath.ast.too-deep"
    INVALID_NUMBER = "jsonpath.ast.invalid-number"
    INVALID_TYPE = "jsonpath.ast.invalid-type"


class JsonPathAstError(ValueError):
    """A value-free failure from direct immutable AST construction."""

    def __init__(self, code: JsonPathAstErrorCode) -> None:
        """Retain only a stable failure code."""
        self.code = code
        super().__init__(code.value)


class _ExpressionType(Enum):
    LOGICAL = "logical"
    NODES = "nodes"
    VALUE = "value"


def canonical_jsonpath(ast: JsonPathAst) -> str:
    """Render the unique bracket-normalized identity of a valid AST."""
    _require_ast(ast)
    return "$" + _render_segments(ast.segments)


def jsonpath_fingerprint(ast: JsonPathAst) -> Fingerprint:
    """Derive the compiler-versioned fingerprint from canonical syntax."""
    canonical = canonical_jsonpath(ast)
    digest = sha256(_FINGERPRINT_DOMAIN + canonical.encode("utf-8")).hexdigest()
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value=digest))


def derive_jsonpath_capabilities(ast: JsonPathAst) -> SelectorCapabilities:
    """Validate static types and derive execution requirements from syntax only."""
    _require_ast(ast)
    selectors = tuple(selector for segment in ast.segments for selector in segment.selectors)
    plural = any(
        type(segment) is DescendantSegment
        or len(segment.selectors) > 1
        or any(
            type(selector) not in {NameSelector, IndexSelector} for selector in segment.selectors
        )
        for segment in ast.segments
    )
    functions: list[str] = []
    for selector in selectors:
        if type(selector) is FilterSelector:
            _collect_functions(selector.predicate, functions)
    uses_filter = any(type(selector) is FilterSelector for selector in selectors)
    cardinality = (
        SelectorCardinality.EXACTLY_ONE
        if not ast.segments
        else SelectorCardinality.ZERO_OR_MORE
        if plural
        else SelectorCardinality.ZERO_OR_ONE
    )
    return SelectorCapabilities(
        cardinality=cardinality,
        access=SelectionAccess.MATERIALIZED if uses_filter else _indexed_access(selectors),
        may_duplicate=any(len(segment.selectors) > 1 for segment in ast.segments),
        uses_descendant=any(type(segment) is DescendantSegment for segment in ast.segments),
        uses_filter=uses_filter,
        function_dependencies=tuple(dict.fromkeys(functions)),
    )


def _require_ast(ast: JsonPathAst) -> None:
    if type(ast) is not JsonPathAst:
        raise TypeError("JSONPath derivation requires a JsonPathAst")
    roots = tuple(
        selector.predicate
        for segment in ast.segments
        for selector in segment.selectors
        if type(selector) is FilterSelector
    )
    stack = [
        (selector.predicate, 1)
        for segment in ast.segments
        for selector in segment.selectors
        if type(selector) is FilterSelector
    ]
    for segment in ast.segments:
        for selector in segment.selectors:
            _validate_selector_number(selector)
    while stack:
        expression, depth = stack.pop()
        if depth > _MAX_TRUSTED_DEPTH:
            raise JsonPathAstError(JsonPathAstErrorCode.TOO_DEEP)
        if type(expression) is QueryExpression:
            for segment in expression.segments:
                for selector in segment.selectors:
                    _validate_selector_number(selector)
        stack.extend(_nested_expressions(expression, depth + 1))
    if any(not _is_logical_test(predicate) for predicate in roots):
        raise JsonPathAstError(JsonPathAstErrorCode.INVALID_TYPE)


def _validate_selector_number(selector: SegmentSelector) -> None:
    if type(selector) is IndexSelector and abs(selector.index) > _MAX_EXACT_INDEX:
        raise JsonPathAstError(JsonPathAstErrorCode.INVALID_NUMBER)
    if type(selector) is SliceSelector and any(
        value is not None and abs(value) > _MAX_EXACT_INDEX for value in selector.values
    ):
        raise JsonPathAstError(JsonPathAstErrorCode.INVALID_NUMBER)


def _nested_expressions(
    expression: FilterExpression,
    depth: int,
) -> tuple[tuple[FilterExpression, int], ...]:
    if type(expression) is FunctionExpression:
        return tuple((argument, depth) for argument in expression.arguments)
    if type(expression) in {ComparisonExpression, AndExpression, OrExpression}:
        binary = cast("ComparisonExpression | AndExpression | OrExpression", expression)
        return (binary.left, depth), (binary.right, depth)
    if type(expression) is NotExpression:
        return ((expression.operand, depth),)
    if type(expression) is QueryExpression:
        return tuple(
            (selector.predicate, depth)
            for segment in expression.segments
            for selector in segment.selectors
            if type(selector) is FilterSelector
        )
    return ()


def _expression_type(expression: FilterExpression) -> _ExpressionType:
    if type(expression) is LiteralExpression:
        return _ExpressionType.VALUE
    if type(expression) is QueryExpression:
        return _ExpressionType.NODES
    if type(expression) is FunctionExpression:
        return _validate_function(expression)
    return _logical_expression_type(expression)


def _logical_expression_type(
    expression: ComparisonExpression | NotExpression | AndExpression | OrExpression,
) -> _ExpressionType:
    if type(expression) is ComparisonExpression:
        if _comparison_operand_type(expression.left) is not _ExpressionType.VALUE:
            raise JsonPathAstError(JsonPathAstErrorCode.INVALID_TYPE)
        if _comparison_operand_type(expression.right) is not _ExpressionType.VALUE:
            raise JsonPathAstError(JsonPathAstErrorCode.INVALID_TYPE)
        return _ExpressionType.LOGICAL
    if type(expression) is NotExpression:
        if not _is_logical_test(expression.operand):
            raise JsonPathAstError(JsonPathAstErrorCode.INVALID_TYPE)
        return _ExpressionType.LOGICAL
    if type(expression) in {AndExpression, OrExpression}:
        binary = expression
        if not _is_logical_test(binary.left) or not _is_logical_test(binary.right):
            raise JsonPathAstError(JsonPathAstErrorCode.INVALID_TYPE)
        return _ExpressionType.LOGICAL
    raise TypeError("unsupported JSONPath logical expression")  # pragma: no cover


def _comparison_operand_type(expression: FilterExpression) -> _ExpressionType:
    result = _expression_type(expression)
    if (
        result is _ExpressionType.NODES
        and type(expression) is QueryExpression
        and _singular(expression)
    ):
        return _ExpressionType.VALUE
    return result


def _validate_function(expression: FunctionExpression) -> _ExpressionType:
    expected = {
        StandardFunction.LENGTH: ((_ExpressionType.VALUE,), _ExpressionType.VALUE),
        StandardFunction.COUNT: ((_ExpressionType.NODES,), _ExpressionType.VALUE),
        StandardFunction.MATCH: (
            (_ExpressionType.VALUE, _ExpressionType.VALUE),
            _ExpressionType.LOGICAL,
        ),
        StandardFunction.SEARCH: (
            (_ExpressionType.VALUE, _ExpressionType.VALUE),
            _ExpressionType.LOGICAL,
        ),
        StandardFunction.VALUE: ((_ExpressionType.NODES,), _ExpressionType.VALUE),
    }[expression.function]
    argument_types, result_type = expected
    if len(expression.arguments) != len(argument_types):
        raise JsonPathAstError(JsonPathAstErrorCode.INVALID_TYPE)
    actual = tuple(
        _comparison_operand_type(argument)
        if required is _ExpressionType.VALUE
        else _expression_type(argument)
        for argument, required in zip(expression.arguments, argument_types, strict=True)
    )
    if actual != argument_types:
        raise JsonPathAstError(JsonPathAstErrorCode.INVALID_TYPE)
    return result_type


def _singular(expression: QueryExpression) -> bool:
    return all(
        type(segment) is not DescendantSegment
        and len(segment.selectors) == 1
        and type(segment.selectors[0]) in {NameSelector, IndexSelector}
        for segment in expression.segments
    )


def _is_logical_test(expression: FilterExpression) -> bool:
    return _expression_type(expression) in {_ExpressionType.LOGICAL, _ExpressionType.NODES}


def _collect_functions(expression: FilterExpression, names: list[str]) -> None:
    if type(expression) is FunctionExpression:
        names.append(expression.function.value)
        for argument in expression.arguments:
            _collect_functions(argument, names)
    elif type(expression) is ComparisonExpression:
        _collect_functions(expression.left, names)
        _collect_functions(expression.right, names)
    elif type(expression) is NotExpression:
        _collect_functions(expression.operand, names)
    elif type(expression) in {AndExpression, OrExpression}:
        binary = cast("AndExpression | OrExpression", expression)
        _collect_functions(binary.left, names)
        _collect_functions(binary.right, names)
    elif type(expression) is QueryExpression:
        for segment in expression.segments:
            for selector in segment.selectors:
                if type(selector) is FilterSelector:
                    _collect_functions(selector.predicate, names)


def _indexed_access(selectors: tuple[SegmentSelector, ...]) -> SelectionAccess:
    indexed = any(
        (type(selector) is IndexSelector and selector.index < 0)
        or (
            type(selector) is SliceSelector
            and any(value is not None and value < 0 for value in selector.values)
        )
        for selector in selectors
    )
    return SelectionAccess.INDEXED if indexed else SelectionAccess.INCREMENTAL


def _render_segments(segments: tuple[ChildSegment | DescendantSegment, ...]) -> str:
    return "".join(
        ("" if type(segment) is ChildSegment else "..")
        + "["
        + ",".join(_render_selector(selector) for selector in segment.selectors)
        + "]"
        for segment in segments
    )


def _render_selector(selector: SegmentSelector) -> str:
    if type(selector) is NameSelector:
        return _render_string(selector.name)
    if type(selector) is IndexSelector:
        return str(selector.index)
    if type(selector) is SliceSelector:
        start, stop, step = selector.values
        rendered = f"{_optional_int(start)}:{_optional_int(stop)}"
        return rendered if step is None else f"{rendered}:{step}"
    if type(selector) is WildcardSelector:
        return "*"
    return "?" + _render_expression(selector.predicate)


def _render_expression(expression: FilterExpression, precedence: int = 0) -> str:
    if type(expression) is LiteralExpression:
        return _render_literal(expression)
    if type(expression) is QueryExpression:
        root = "$" if expression.origin is QueryOrigin.ROOT else "@"
        return root + _render_segments(expression.segments)
    if type(expression) is FunctionExpression:
        arguments = ",".join(_render_expression(argument) for argument in expression.arguments)
        return f"{expression.function.value}({arguments})"
    if type(expression) is ComparisonExpression:
        return (
            _render_expression(expression.left, 4)
            + expression.operator.value
            + _render_expression(expression.right, 4)
        )
    if type(expression) is NotExpression:
        return _parenthesize("!" + _render_expression(expression.operand, 3), precedence > 3)
    binary = expression
    level, operator = (2, "&&") if type(binary) is AndExpression else (1, "||")
    rendered = (
        _render_expression(binary.left, level) + operator + _render_expression(binary.right, level)
    )
    return _parenthesize(rendered, precedence > level)


def _render_literal(expression: LiteralExpression) -> str:
    value = expression.value
    if type(value) is JsonNull:
        return "null"
    if type(value) is JsonBoolean:
        return "true" if value.value else "false"
    if type(value) is JsonInteger:
        return str(value.value)
    if type(value) is JsonDecimal:
        return _render_decimal(value.value)
    return _render_string(value.value)


def _render_decimal(value: Decimal) -> str:
    rendered = str(value).lower()
    if "e" not in rendered:
        return rendered
    coefficient, exponent = rendered.split("e", maxsplit=1)
    return coefficient + "e" + str(int(exponent))


def _render_string(value: str) -> str:
    escapes = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    rendered: list[str] = ['"']
    for character in value:
        if character in escapes:
            rendered.append(escapes[character])
        elif ord(character) < 0x20:
            rendered.append(f"\\u{ord(character):04x}")
        else:
            rendered.append(character)
    rendered.append('"')
    return "".join(rendered)


def _optional_int(value: int | None) -> str:
    return "" if value is None else str(value)


def _parenthesize(value: str, required: bool) -> str:
    return f"({value})" if required else value
