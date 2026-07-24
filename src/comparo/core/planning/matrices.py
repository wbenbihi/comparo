"""Pure Matrix case compilation and compact Cartesian attachment planning."""

from __future__ import annotations

import hashlib
from itertools import product
from typing import cast

from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonString
from comparo.core.planning._canonical import expression_member
from comparo.core.planning._canonical import literal_string
from comparo.core.planning._canonical import object_member
from comparo.core.planning._canonical import resource_spec
from comparo.core.planning._errors import PlanningError as PlanningProblem
from comparo.core.planning.models import EffectiveMatrix
from comparo.core.planning.models import MatrixCaseInput

type AxisValue = tuple[MatrixCaseId, ValueExpression]
type Axis = tuple[str, tuple[AxisValue, ...]]


def compile_matrix(
    resource: CompiledResource,
    binding: MatrixBindingId,
    *,
    maximum_cases: int,
) -> EffectiveMatrix:
    """Compile one Matrix definition without resolving any case value expression."""
    spec = resource_spec(resource)
    cases_value = object_member(spec, "cases")
    axes_value = object_member(spec, "axes")
    combine_value = object_member(spec, "combine")
    if combine_value is None:
        combine = "product"
    elif type(combine_value) is JsonString:
        combine = combine_value.value
    else:
        raise PlanningProblem("planning.matrix.combine-invalid", resource=resource, field="combine")
    if combine not in {"product", "zip", "union"}:
        raise PlanningProblem(
            "planning.matrix.combine-unsupported", resource=resource, field="combine"
        )

    if cases_value is not None:
        cases = _explicit_cases(cases_value, resource=resource, field="cases")
    elif axes_value is not None:
        cases = _axis_cases(
            axes_value,
            combine=combine,
            resource=resource,
            maximum_cases=maximum_cases,
        )
    else:
        raise PlanningProblem("planning.matrix.input-missing", resource=resource)

    include = object_member(spec, "include")
    if include is not None:
        cases = (*cases, *_explicit_cases(include, resource=resource, field="include"))
    if len(cases) > maximum_cases:
        raise PlanningProblem(
            "planning.matrix.expansion-exceeded",
            resource=resource,
            occurrences=len(cases),
        )
    excluded = _excluded_ids(object_member(spec, "exclude"), resource=resource)
    cases = tuple(case for case in cases if case.case_id not in excluded)
    if not cases:
        raise PlanningProblem("planning.matrix.no-cases", resource=resource)
    identities = tuple(case.case_id for case in cases)
    if len(identities) != len(set(identities)):
        raise PlanningProblem("planning.matrix.case-id-duplicate", resource=resource)
    return EffectiveMatrix(binding=binding, definition=resource.definition, cases=cases)


def matrix_field_names(matrix: EffectiveMatrix) -> frozenset[str]:
    """Return names available for unqualified Matrix lookup across all cases."""
    return frozenset(
        member.name
        for case in matrix.cases
        if type(case.values) is ObjectExpr
        for member in case.values.members
    )


def validate_unqualified_fields(
    matrices: tuple[EffectiveMatrix, ...],
    *,
    owner: CompiledResource,
) -> None:
    """Reject ambiguous shorthand fields across attached Matrix bindings."""
    seen: set[str] = set()
    for matrix in matrices:
        names = matrix_field_names(matrix)
        if seen & names:
            raise PlanningProblem(
                "planning.matrix.field-ambiguous",
                resource=owner,
                field="matrices",
            )
        seen.update(names)


def _explicit_cases(
    value: object,
    *,
    resource: CompiledResource,
    field: str,
) -> tuple[MatrixCaseInput, ...]:
    if type(value) is not ArrayExpr:
        raise PlanningProblem("planning.matrix.cases-invalid", resource=resource, field=field)
    result: list[MatrixCaseInput] = []
    for item in value.items:
        if type(item) is not ObjectExpr:
            raise PlanningProblem("planning.matrix.case-invalid", resource=resource, field=field)
        case_id = _case_id(expression_member(item, "id"), resource=resource, field=field)
        values = expression_member(item, "values")
        if type(values) is not ObjectExpr:
            raise PlanningProblem("planning.matrix.values-invalid", resource=resource, field=field)
        result.append(MatrixCaseInput(case_id=case_id, values=values))
    return tuple(result)


def _axis_cases(
    value: object,
    *,
    combine: str,
    resource: CompiledResource,
    maximum_cases: int,
) -> tuple[MatrixCaseInput, ...]:
    if type(value) is not ObjectExpr or not value.members:
        raise PlanningProblem("planning.matrix.axes-invalid", resource=resource, field="axes")
    axes = _parse_axes(value, resource=resource)
    return _combine_axes(
        axes,
        combine=combine,
        source=value,
        resource=resource,
        maximum_cases=maximum_cases,
    )


def _parse_axes(value: ObjectExpr, *, resource: CompiledResource) -> tuple[Axis, ...]:
    axes: list[Axis] = []
    for member in value.members:
        try:
            MatrixBindingId(value=member.name)
        except ValueError as error:
            raise PlanningProblem(
                "planning.matrix.axis-id-invalid",
                resource=resource,
                field="axes",
            ) from error
        if type(member.expression) is not ArrayExpr or not member.expression.items:
            raise PlanningProblem("planning.matrix.axis-invalid", resource=resource, field="axes")
        axis_values: list[tuple[MatrixCaseId, ValueExpression]] = []
        for item in member.expression.items:
            if type(item) is not ObjectExpr:
                raise PlanningProblem(
                    "planning.matrix.axis-value-invalid", resource=resource, field="axes"
                )
            case_id = _case_id(expression_member(item, "id"), resource=resource, field="axes")
            item_value = expression_member(item, "value")
            if item_value is None:
                raise PlanningProblem(
                    "planning.matrix.axis-value-invalid", resource=resource, field="axes"
                )
            axis_values.append((case_id, item_value))
        if len(axis_values) != len({case_id for case_id, _ in axis_values}):
            raise PlanningProblem(
                "planning.matrix.axis-case-duplicate", resource=resource, field="axes"
            )
        axes.append((member.name, tuple(axis_values)))
    return tuple(axes)


def _combine_axes(
    axes: tuple[Axis, ...],
    *,
    combine: str,
    source: ObjectExpr,
    resource: CompiledResource,
    maximum_cases: int,
) -> tuple[MatrixCaseInput, ...]:
    if combine == "product":
        cardinality = 1
        for _, axis_values in axes:
            cardinality *= len(axis_values)
        if cardinality > maximum_cases:
            raise PlanningProblem(
                "planning.matrix.expansion-exceeded",
                resource=resource,
                occurrences=cardinality,
            )
        rows = tuple(product(*(values for _, values in axes)))
        return tuple(_axis_row(axes, row, source) for row in rows)
    if combine == "zip":
        lengths = {len(values) for _, values in axes}
        if len(lengths) != 1:
            raise PlanningProblem("planning.matrix.zip-length", resource=resource, field="axes")
        if next(iter(lengths)) > maximum_cases:
            raise PlanningProblem(
                "planning.matrix.expansion-exceeded",
                resource=resource,
                occurrences=next(iter(lengths)),
            )
        zip_rows = zip(*(values for _, values in axes), strict=True)
        return tuple(_axis_row(axes, row, source) for row in zip_rows)
    union_size = sum(len(axis_values) for _, axis_values in axes)
    if union_size > maximum_cases:
        raise PlanningProblem(
            "planning.matrix.expansion-exceeded",
            resource=resource,
            occurrences=union_size,
        )
    return tuple(
        MatrixCaseInput(
            case_id=_joined_case_id((MatrixCaseId(value=axis_name), case_id)),
            values=ObjectExpr(
                members=(ObjectExpressionMember(name=axis_name, expression=item_value),),
                provenance=source.provenance,
            ),
        )
        for axis_name, values in axes
        for case_id, item_value in values
    )


def _axis_row(
    axes: tuple[Axis, ...],
    row: tuple[tuple[MatrixCaseId, ValueExpression], ...],
    source: ObjectExpr,
) -> MatrixCaseInput:
    return MatrixCaseInput(
        case_id=_joined_case_id(tuple(case_id for case_id, _ in row)),
        values=ObjectExpr(
            members=tuple(
                ObjectExpressionMember(name=axis[0], expression=item_value)
                for axis, (_, item_value) in zip(axes, row, strict=True)
            ),
            provenance=source.provenance,
        ),
    )


def _joined_case_id(parts: tuple[MatrixCaseId, ...]) -> MatrixCaseId:
    value = "-".join(part.value for part in parts)
    if len(value) <= 63:
        return MatrixCaseId(value=value)
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return MatrixCaseId(value=f"{value[:50].rstrip('-')}-{digest}")


def _case_id(
    value: object,
    *,
    resource: CompiledResource,
    field: str,
) -> MatrixCaseId:
    raw = literal_string(value, owner=resource, field=field)
    try:
        return MatrixCaseId(value=raw)
    except ValueError as error:
        raise PlanningProblem(
            "planning.matrix.case-id-invalid", resource=resource, field=field
        ) from error


def _excluded_ids(
    value: object,
    *,
    resource: CompiledResource,
) -> frozenset[MatrixCaseId]:
    if value is None:
        return frozenset()
    if type(value) is not JsonArray or any(type(item) is not JsonString for item in value.items):
        raise PlanningProblem("planning.matrix.exclude-invalid", resource=resource, field="exclude")
    strings = cast("tuple[JsonString, ...]", value.items)
    try:
        result = frozenset(MatrixCaseId(value=item.value) for item in strings)
    except ValueError as error:
        raise PlanningProblem(
            "planning.matrix.exclude-invalid", resource=resource, field="exclude"
        ) from error
    if len(result) != len(value.items):
        raise PlanningProblem(
            "planning.matrix.exclude-duplicate", resource=resource, field="exclude"
        )
    return result
