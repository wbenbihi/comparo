"""Pure dependency analysis for value expressions."""

from __future__ import annotations

from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionDependency
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import ValueExpression


def expression_dependencies(
    expression: ValueExpression,
) -> tuple[ExpressionDependency, ...]:
    """Return ordered unique dependencies without storing derived metadata."""
    discovered = _all_dependencies(expression)
    return tuple(
        dependency
        for index, dependency in enumerate(discovered)
        if dependency not in discovered[:index]
    )


def _all_dependencies(expression: ValueExpression) -> tuple[ExpressionDependency, ...]:
    if type(expression) in {LiteralExpr, SensitiveLiteralExpr}:
        return ()
    if type(expression) is LookupExpr:
        return (expression.dependency,)
    if type(expression) is FileExpr:
        return _all_dependencies(expression.path)
    if type(expression) is FallbackExpr:
        return tuple(
            dependency for source in expression.sources for dependency in _all_dependencies(source)
        )
    if type(expression) is OptionalExpr:
        return _all_dependencies(expression.source)
    if type(expression) is ConvertExpr:
        return _all_dependencies(expression.source)
    if type(expression) is ArrayExpr:
        return tuple(
            dependency for item in expression.items for dependency in _all_dependencies(item)
        )
    if type(expression) is ObjectExpr:
        return tuple(
            dependency
            for member in expression.members
            for dependency in _all_dependencies(member.expression)
        )
    if type(expression) is InterpolationExpr:
        return tuple(
            dependency
            for segment in expression.segments
            if type(segment) is ExpressionSegment
            for dependency in _all_dependencies(segment.expression)
        )
    raise TypeError("unsupported value expression")
