"""Lower authored diff stages to the pure typed diff pipeline kernel."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.diffs.contracts import derive_side_contract
from comparo.core.domain.diffs.operations import CaseMode
from comparo.core.domain.diffs.operations import DiffDigestAlgorithm
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import DiffTransform
from comparo.core.domain.diffs.operations import DigestTransform
from comparo.core.domain.diffs.operations import Exclusion
from comparo.core.domain.diffs.operations import JsonShapeArrayMode
from comparo.core.domain.diffs.operations import JsonShapeTransform
from comparo.core.domain.diffs.operations import LengthTransform
from comparo.core.domain.diffs.operations import LengthUnit
from comparo.core.domain.diffs.operations import NewlineMode
from comparo.core.domain.diffs.operations import Projection
from comparo.core.domain.diffs.operations import TextNormalization
from comparo.core.domain.diffs.operations import TrimMode
from comparo.core.domain.diffs.operations import ValueTypeTransform
from comparo.core.domain.diffs.strategies import CollectionComparator
from comparo.core.domain.diffs.strategies import CollectionComparisonKind
from comparo.core.domain.diffs.strategies import CompatibilityComparator
from comparo.core.domain.diffs.strategies import CompatibilityKind
from comparo.core.domain.diffs.strategies import DiffComparator
from comparo.core.domain.diffs.strategies import DiffPairing
from comparo.core.domain.diffs.strategies import DiffReducer
from comparo.core.domain.diffs.strategies import EmptyInputMode
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import IgnoreComparator
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.diffs.strategies import OrderedPairing
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.diffs.strategies import TextPredicateComparator
from comparo.core.domain.diffs.strategies import TextPredicateRelation
from comparo.core.domain.diffs.strategies import UnorderedPairing
from comparo.core.domain.diffs.strategies import UnorderedPairingKind
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.values import JsonString


class DiffSyntaxFamily(DomainStrEnum):
    """Authored pipeline stage that rejected an operation."""

    TRANSFORM = "transform"
    REDUCER = "reducer"
    PAIRING = "pairing"
    COMPARATOR = "comparator"


@final
class DiffOperationError(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free operation rejection for the outer diagnostic adapter."""

    family: DiffSyntaxFamily
    alias: str | None = None


class InvalidDiffOperationError(ValueError):
    """Internal control-flow exception for invalid authored diff operations."""

    def __init__(self, error: DiffOperationError) -> None:
        """Retain one safe rejection without retaining operation operands."""
        super().__init__("diff operation cannot be lowered")
        self.error = error


def compile_transforms(
    expression: ValueExpression | None,
    *,
    input_contract: ValueContract,
) -> tuple[DiffTransform, ...]:
    """Compile an ordered transform sequence while validating each static contract."""
    if expression is None:
        return ()
    if type(expression) is not ArrayExpr:
        raise _invalid(DiffSyntaxFamily.TRANSFORM, None)
    result: list[DiffTransform] = []
    current = input_contract
    for item in expression.items:
        operation = _operation(item)
        alias = None if operation is None else operation[0]
        try:
            if operation is None:
                raise ValueError("transform operation is not static")
            transform = _compile_transform(*operation)
            current = derive_side_contract(current, (transform,), None)
        except (KeyError, TypeError, ValueError) as error:
            raise _invalid(DiffSyntaxFamily.TRANSFORM, alias) from error
        result.append(transform)
    return tuple(result)


def compile_reducer(
    expression: ValueExpression | None,
    *,
    input_contract: ValueContract,
    transforms: tuple[DiffTransform, ...],
) -> DiffReducer | None:
    """Compile one optional semantic reducer against the post-transform contract."""
    if expression is None:
        return None
    operation = _operation(expression)
    alias = None if operation is None else operation[0]
    try:
        if operation is None:
            raise ValueError("reducer operation is not static")
        reducer = _compile_reducer_operation(*operation)
        derive_side_contract(input_contract, transforms, reducer)
    except (KeyError, TypeError, ValueError) as error:
        raise _invalid(DiffSyntaxFamily.REDUCER, alias) from error
    return reducer


def compile_pairing(
    expression: ValueExpression | None,
    *,
    baseline: ValueContract,
    candidate: ValueContract,
) -> DiffPairing:
    """Compile explicit pairing or derive the only safe cardinality-based default."""
    if expression is None:
        name = (
            "ordered"
            if SelectorCardinality.ZERO_OR_MORE in {baseline.cardinality, candidate.cardinality}
            else "singular"
        )
        fields: dict[str, ValueExpression] = {}
    else:
        operation = _operation(expression)
        if operation is None:
            raise _invalid(DiffSyntaxFamily.PAIRING, None)
        name, fields = operation
    try:
        _require_fields(fields, "name" if expression is not None else None)
        identity = _identity(DiffOperationFamily.PAIRING, _kebab(name))
        if name == "singular":
            return SingularPairing(identity=identity)
        if name == "ordered":
            return OrderedPairing(identity=identity)
        if name in {"set", "multiset"}:
            return UnorderedPairing(
                identity=identity,
                kind=(UnorderedPairingKind.SET if name == "set" else UnorderedPairingKind.MULTISET),
            )
        raise ValueError("pairing needs a relative key selector")
    except (TypeError, ValueError) as error:
        raise _invalid(DiffSyntaxFamily.PAIRING, name) from error


def compile_comparator(expression: ValueExpression | None) -> DiffComparator:
    """Compile one typed comparator, including directed and tolerance semantics."""
    operation = _operation(expression)
    alias = None if operation is None else operation[0]
    try:
        if operation is None:
            raise ValueError("comparator operation is not static")
        name, fields = operation
        identity = _identity(DiffOperationFamily.COMPARATOR, _kebab(name))
        if name in {"exact", "ignore"}:
            _require_fields(fields, "name")
            return (
                ExactComparator(identity=identity)
                if name == "exact"
                else IgnoreComparator(identity=identity)
            )
        compatibility = {
            "type": CompatibilityKind.TYPE,
            "jsonShape": CompatibilityKind.JSON_SHAPE,
            "subset": CompatibilityKind.SUBSET,
            "superset": CompatibilityKind.SUPERSET,
        }
        if name in compatibility:
            _require_fields(fields, "name")
            return CompatibilityComparator(identity=identity, kind=compatibility[name])
        if name == "numericTolerance":
            _require_optional_fields(fields, {"name"}, {"absolute", "relative"})
            return NumericToleranceComparator(
                identity=identity,
                absolute_tolerance=fields.get("absolute"),
                relative_tolerance=fields.get("relative"),
            )
        collections = {
            "ordered": CollectionComparisonKind.ORDERED,
            "set": CollectionComparisonKind.SET,
            "multiset": CollectionComparisonKind.MULTISET,
        }
        if name in collections:
            _require_fields(fields, "name")
            return CollectionComparator(identity=identity, kind=collections[name])
        if name == "textPredicate":
            _require_fields(fields, "name", "relation")
            return TextPredicateComparator(
                identity=identity,
                relation=TextPredicateRelation(_required_text(fields, "relation")),
            )
        raise ValueError("unsupported comparator")
    except (KeyError, TypeError, ValueError) as error:
        raise _invalid(DiffSyntaxFamily.COMPARATOR, alias) from error


def _compile_transform(
    name: str,
    fields: dict[str, ValueExpression],
) -> DiffTransform:
    identity = _identity(DiffOperationFamily.TRANSFORM, _kebab(name))
    if name in {"project", "exclude"}:
        _require_fields(fields, "name", "pointers")
        pointers = tuple(JsonPointer(value=value) for value in _texts(fields["pointers"]))
        return (
            Projection(identity=identity, pointers=pointers)
            if name == "project"
            else Exclusion(identity=identity, pointers=pointers)
        )
    if name == "normalizeText":
        _require_optional_fields(fields, {"name"}, {"trim", "newlines", "case"})
        return TextNormalization(
            identity=identity,
            trim=TrimMode(_text(fields.get("trim")) or "none"),
            newlines=NewlineMode(_text(fields.get("newlines")) or "preserve"),
            case=CaseMode(_text(fields.get("case")) or "preserve"),
        )
    if name == "length":
        _require_fields(fields, "name", "unit")
        return LengthTransform(
            identity=identity,
            unit=LengthUnit(_required_text(fields, "unit")),
        )
    if name == "valueType":
        _require_fields(fields, "name")
        return ValueTypeTransform(identity=identity)
    if name == "jsonShape":
        _require_optional_fields(fields, {"name"}, {"arrays"})
        return JsonShapeTransform(
            identity=identity,
            arrays=JsonShapeArrayMode(_text(fields.get("arrays")) or "positional"),
        )
    if name == "digest":
        _require_fields(fields, "name", "algorithm")
        return DigestTransform(
            identity=identity,
            algorithm=DiffDigestAlgorithm(_required_text(fields, "algorithm")),
        )
    raise ValueError("transform requires a relative item selector")


def _compile_reducer_operation(
    name: str,
    fields: dict[str, ValueExpression],
) -> DiffReducer:
    kind_names = {
        "first": ReducerKind.FIRST,
        "last": ReducerKind.LAST,
        "count": ReducerKind.COUNT,
        "concatenate": ReducerKind.CONCATENATE,
        "sum": ReducerKind.SUM,
        "minimum": ReducerKind.MINIMUM,
        "maximum": ReducerKind.MAXIMUM,
        "collect": ReducerKind.COLLECT,
        "set": ReducerKind.SET,
        "multiset": ReducerKind.MULTISET,
        "distinct": ReducerKind.DISTINCT,
    }
    kind = kind_names.get(name)
    if kind is None:
        raise ValueError("reducer requires relative item selectors")
    optional = {"empty", "separator"} if kind is ReducerKind.CONCATENATE else {"empty"}
    _require_optional_fields(fields, {"name"}, optional)
    separator = fields.get("separator")
    if kind is ReducerKind.CONCATENATE and separator is None:
        raise ValueError("concatenate reducer requires a separator")
    return Reducer(
        identity=_identity(DiffOperationFamily.REDUCER, _kebab(name)),
        kind=kind,
        empty=EmptyInputMode(_text(fields.get("empty")) or "error"),
        separator=separator,
    )


def _operation(
    expression: ValueExpression | None,
) -> tuple[str, dict[str, ValueExpression]] | None:
    shorthand = _text(expression)
    if shorthand is not None and expression is not None:
        return _normalize(shorthand), {"name": expression}
    if type(expression) is not ObjectExpr:
        return None
    fields = {member.name: member.expression for member in expression.members}
    name = _text(fields.get("name"))
    return None if name is None else (_normalize(name), fields)


def _normalize(name: str) -> str:
    prefix = "comparo.core/"
    return name[len(prefix) : -2] if name.startswith(prefix) and name.endswith("@1") else name


def _kebab(name: str) -> str:
    result: list[str] = []
    for character in name:
        if character.isupper():
            result.extend(("-", character.lower()))
        else:
            result.append(character)
    return "".join(result)


def _identity(family: DiffOperationFamily, name: str) -> DiffOperationIdentity:
    return DiffOperationIdentity(namespace="comparo.core", family=family, name=name, version="1")


def _require_fields(fields: dict[str, ValueExpression], *required: str | None) -> None:
    names = {name for name in required if name is not None}
    if set(fields) != names:
        raise ValueError("operation fields do not match its typed schema")


def _require_optional_fields(
    fields: dict[str, ValueExpression],
    required: set[str],
    optional: set[str],
) -> None:
    if not required <= set(fields) or not set(fields) <= required | optional:
        raise ValueError("operation fields do not match its typed schema")


def _texts(value: ValueExpression) -> tuple[str, ...]:
    if type(value) is not ArrayExpr or not value.items:
        raise ValueError("operation requires a non-empty text array")
    items = tuple(_text(item) for item in value.items)
    if any(item is None for item in items):
        raise ValueError("operation requires a text array")
    return tuple(item for item in items if item is not None)


def _text(value: ValueExpression | None) -> str | None:
    return (
        value.value.value
        if type(value) is LiteralExpr and type(value.value) is JsonString
        else None
    )


def _required_text(fields: dict[str, ValueExpression], name: str) -> str:
    value = _text(fields[name])
    if value is None:
        raise ValueError("operation requires static text")
    return value


def _invalid(family: DiffSyntaxFamily, alias: str | None) -> InvalidDiffOperationError:
    return InvalidDiffOperationError(
        DiffOperationError(
            family=family,
            alias=alias if alias is not None and len(alias) <= 64 else None,
        )
    )
