"""Pure sensitivity-preserving diff side transforms."""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from hashlib import sha512

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.diffs.item_selection import derive_value
from comparo.core.diffs.item_selection import select_item
from comparo.core.diffs.item_selection import select_json
from comparo.core.diffs.json_transforms import exclude
from comparo.core.diffs.json_transforms import project
from comparo.core.diffs.json_transforms import shape
from comparo.core.diffs.json_transforms import shape_size_upper_bound
from comparo.core.diffs.operands import resolve_operand
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.value_support import canonical_key
from comparo.core.diffs.value_support import contain
from comparo.core.diffs.value_support import merge_taint_origins
from comparo.core.diffs.value_support import require_derived_capacity
from comparo.core.diffs.value_support import require_work
from comparo.core.diffs.value_support import reveal
from comparo.core.diffs.value_support import value_size
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import CaseMode
from comparo.core.domain.diffs.operations import CollectionFilter
from comparo.core.domain.diffs.operations import CollectionMap
from comparo.core.domain.diffs.operations import DiffDigestAlgorithm
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import DiffTransform
from comparo.core.domain.diffs.operations import DigestTransform
from comparo.core.domain.diffs.operations import Exclusion
from comparo.core.domain.diffs.operations import JsonShapeTransform
from comparo.core.domain.diffs.operations import LengthTransform
from comparo.core.domain.diffs.operations import LengthUnit
from comparo.core.domain.diffs.operations import NewlineMode
from comparo.core.domain.diffs.operations import Projection
from comparo.core.domain.diffs.operations import RelativeSelection
from comparo.core.domain.diffs.operations import SortBy
from comparo.core.domain.diffs.operations import SortDirection
from comparo.core.domain.diffs.operations import StreamProjection
from comparo.core.domain.diffs.operations import StreamSemanticSource
from comparo.core.domain.diffs.operations import TextNormalization
from comparo.core.domain.diffs.operations import TrimMode
from comparo.core.domain.diffs.operations import ValueTypeTransform
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.items import ItemPredicateKind
from comparo.core.domain.selectors.items import ItemTypePredicate
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import selected_value_kind
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import is_json_value

_TYPE_NAMES = {
    "JsonNull": "null",
    "JsonBoolean": "boolean",
    "JsonInteger": "integer",
    "JsonDecimal": "number",
    "JsonString": "string",
    "JsonArray": "array",
    "JsonObject": "object",
    "BytesValue": "bytes",
}


def apply_transforms(
    values: tuple[DerivedValue, ...],
    transforms: tuple[DiffTransform, ...],
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
    side: DiffSide,
) -> tuple[DerivedValue, ...]:
    """Apply authored transforms in order and charge every retained output."""
    current = values
    if not transforms:
        return _retain_all(current, None, meter, side)
    for transform in transforms:
        try:
            current = _apply_transform(current, transform, inputs, meter, side)
        except DiffHaltError as halted:
            halted.side = side
            raise
    return current


def _apply_transform(  # noqa: C901
    values: tuple[DerivedValue, ...],
    transform: DiffTransform,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
    side: DiffSide,
) -> tuple[DerivedValue, ...]:
    if type(transform) is RelativeSelection:
        return _select_all(values, transform.selector, transform.identity, meter, side)
    if type(transform) is CollectionMap:
        return _select_all(values, transform.selector, transform.identity, meter, side)
    if type(transform) is StreamProjection:
        return _stream_project(values, transform, meter, side)
    if type(transform) is CollectionFilter:
        output: list[DerivedValue] = []
        try:
            for value in values:
                matched, operand_taint = _predicate(value, transform.predicate, inputs, meter, side)
                if matched:
                    output.append(
                        _retain(
                            value,
                            transform.identity,
                            meter,
                            extra_taint=operand_taint,
                        )
                    )
        except DiffHaltError as halted:
            halted.prepend_derived(tuple(output), side)
            raise
        return tuple(output)
    if type(transform) is SortBy:
        decorated = []
        for index, value in enumerate(values):
            selected = select_item(value, transform.key, transform.identity, meter)
            if len(selected) != 1:
                raise DiffHaltError(code=DiagnosticCode(value="diff.sort.key-cardinality"))
            decorated.append((canonical_key(reveal(selected[0].value), meter), index, value))
        require_work(meter, max(1, len(decorated) * max(1, len(decorated).bit_length())))
        decorated.sort(
            key=lambda item: (item[0], item[1]),
            reverse=transform.direction is SortDirection.DESCENDING,
        )
        return _retain_all(tuple(item[2] for item in decorated), transform.identity, meter, side)
    output = []
    try:
        for value in values:
            output.append(_scalar_transform(value, transform, meter))
    except DiffHaltError as halted:
        halted.prepend_derived(tuple(output), side)
        raise
    return tuple(output)


def _select_all(
    values: tuple[DerivedValue, ...],
    selector: CompiledItemSelector,
    identity: DiffOperationIdentity,
    meter: DiffMeter,
    side: DiffSide,
) -> tuple[DerivedValue, ...]:
    output: list[DerivedValue] = []
    try:
        for value in values:
            output.extend(select_item(value, selector, identity, meter))
    except DiffHaltError as halted:
        halted.prepend_derived(tuple(output), side)
        raise
    return tuple(output)


def _stream_project(
    values: tuple[DerivedValue, ...],
    transform: StreamProjection,
    meter: DiffMeter,
    side: DiffSide,
) -> tuple[DerivedValue, ...]:
    """Project the explicitly authored semantic stream source, never wire chunks."""
    output: list[DerivedValue] = []
    try:
        for value in values:
            for item in _semantic_stream_items(value, meter):
                if transform.source is not StreamSemanticSource.SSE_DATA:
                    output.extend(select_item(item, transform.selector, transform.identity, meter))
                    continue
                raw = reveal(item.value)
                if type(raw) is not JsonObject:
                    raise DiffHaltError(code=DiagnosticCode(value="diff.stream.sse-event-type"))
                data = None
                for member in raw.members:
                    require_work(meter, max(1, len(member.name) + len("data")))
                    if member.name == "data":
                        data = member.value
                        break
                if type(data) is not JsonString:
                    raise DiffHaltError(code=DiagnosticCode(value="diff.stream.sse-data-missing"))
                if type(transform.selector.path) is not NoPath:
                    raise DiffHaltError(code=DiagnosticCode(value="diff.stream.sse-data-path"))
                extracted = DerivedValue(
                    value=contain(data, item.taint_origins),
                    selector=item.selector,
                    origins=item.origins,
                    operations=item.operations,
                    empty_reducer_identity=item.empty_reducer_identity,
                )
                output.extend(select_item(extracted, transform.selector, transform.identity, meter))
    except DiffHaltError as halted:
        halted.prepend_derived(tuple(output), side)
        raise
    return tuple(output)


def _semantic_stream_items(
    value: DerivedValue,
    meter: DiffMeter,
) -> Iterator[DerivedValue]:
    if value.selector.source not in {
        SelectorSource.REQUEST_BODY,
        SelectorSource.RESPONSE_BODY,
    } or value.selector.body_view not in {
        BodyViewKind.SSE_EVENTS,
        BodyViewKind.NDJSON_RECORDS,
        BodyViewKind.JSON_SEQUENCE_RECORDS,
    }:
        yield value
        return
    raw = reveal(value.value)
    if type(raw) is not JsonArray:
        raise DiffHaltError(code=DiagnosticCode(value="diff.stream.collection-type"))
    for item in raw.items:
        require_work(meter)
        yield DerivedValue(
            value=contain(item, value.taint_origins),
            selector=value.selector,
            origins=value.origins,
            operations=value.operations,
            empty_reducer_identity=value.empty_reducer_identity,
        )


def _retain_all(
    values: tuple[DerivedValue, ...],
    operation: DiffOperationIdentity | None,
    meter: DiffMeter,
    side: DiffSide,
) -> tuple[DerivedValue, ...]:
    output: list[DerivedValue] = []
    try:
        for value in values:
            output.append(  # noqa: PERF401 -- partial progress is observable
                _retain(value, operation, meter)
            )
    except DiffHaltError as halted:
        halted.prepend_derived(tuple(output), side)
        raise
    return tuple(output)


def _scalar_transform(  # noqa: C901
    value: DerivedValue,
    transform: DiffTransform,
    meter: DiffMeter,
) -> DerivedValue:
    raw = reveal(value.value)
    require_work(meter)
    result: SelectedValue
    if type(transform) is Projection:
        if not is_json_value(raw):
            raise DiffHaltError(code=DiagnosticCode(value="diff.project.type"))
        result = project(raw, transform.pointers, meter)
    elif type(transform) is Exclusion:
        if not is_json_value(raw):
            raise DiffHaltError(code=DiagnosticCode(value="diff.exclude.type"))
        excluded = exclude(raw, transform.pointers, meter)
        if excluded is None:
            raise DiffHaltError(code=DiagnosticCode(value="diff.exclude.root-unsupported"))
        result = excluded
    elif type(transform) is TextNormalization:
        if type(raw) is not JsonString:
            raise DiffHaltError(code=DiagnosticCode(value="diff.normalize.type"))
        text = raw.value
        require_work(meter, max(1, len(text)))
        if (
            transform.newlines is NewlineMode.PRESERVE
            and transform.trim is TrimMode.NONE
            and transform.case is CaseMode.PRESERVE
        ):
            require_derived_capacity(meter, value_size(raw, meter))
        else:
            require_derived_capacity(meter, 2 + 24 * len(text))
        if transform.newlines is NewlineMode.LF:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = {
            TrimMode.NONE: text,
            TrimMode.START: text.lstrip(),
            TrimMode.END: text.rstrip(),
            TrimMode.BOTH: text.strip(),
        }[transform.trim]
        text = {
            CaseMode.PRESERVE: text,
            CaseMode.LOWERCASE_UNICODE: text.lower(),
            CaseMode.CASEFOLD_UNICODE: text.casefold(),
        }[transform.case]
        result = JsonString(value=text)
    elif type(transform) is LengthTransform:
        result = JsonInteger(value=_length(raw, transform.unit))
    elif type(transform) is ValueTypeTransform:
        result = JsonString(
            value=_TYPE_NAMES.get(type(raw).__name__, selected_value_kind(raw).value)
        )
    elif type(transform) is JsonShapeTransform:
        if not is_json_value(raw):
            raise DiffHaltError(code=DiagnosticCode(value="diff.shape.type"))
        require_derived_capacity(meter, shape_size_upper_bound(raw, transform.arrays, meter))
        result = shape(raw, transform.arrays, meter)
    elif type(transform) is DigestTransform:
        digest = _digest(raw, transform.algorithm, meter)
        result = BytesValue(value=digest)
    else:
        raise TypeError("unsupported scalar diff transform")
    return derive_value(result, value, transform.identity, meter)


def _predicate(
    value: DerivedValue,
    predicate: CompiledItemPredicate,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
    side: DiffSide,
) -> tuple[bool, tuple[TaintOriginId, ...]]:
    source = reveal(value.value)
    if type(predicate.selector.path) is NoPath:
        matches: tuple[SelectedValue, ...] = (source,)
    else:
        if not is_json_value(source):
            raise DiffHaltError(code=DiagnosticCode(value="diff.filter.type"))
        matches = select_json(source, predicate.selector, meter)
    if not matches:
        return False, ()
    if len(matches) != 1:
        raise DiffHaltError(code=DiagnosticCode(value="diff.filter.cardinality"))
    actual = matches[0]
    if type(predicate.operator) is ItemTypePredicate:
        result = selected_value_kind(actual) is predicate.operator.expected
        operand_taint: tuple[TaintOriginId, ...] = ()
    else:
        expected, operand_taint = _operand(inputs, predicate.operator.expected, side)
        result = canonical_key(actual, meter) == canonical_key(expected, meter)
    matched = not result if predicate.identity.kind is ItemPredicateKind.NOT_EQUALS else result
    return matched, operand_taint


def _operand(
    inputs: PreparedDiffInputs,
    expression: ValueExpression,
    side: DiffSide,
) -> tuple[SelectedValue, tuple[TaintOriginId, ...]]:
    result = resolve_operand(inputs, expression, side)
    return result.value, result.taint_origins


def _retain(
    value: DerivedValue,
    operation: DiffOperationIdentity | None,
    meter: DiffMeter,
    *,
    extra_taint: tuple[TaintOriginId, ...] = (),
) -> DerivedValue:
    raw = reveal(value.value)
    size = value_size(raw, meter)
    if (failure := meter.charge_derived(size)) is not None:
        raise DiffHaltError(exhaustion=failure)
    operations = value.operations if operation is None else (*value.operations, operation)
    unique_taint = merge_taint_origins(meter, value.taint_origins, extra_taint)
    return DerivedValue(
        value=contain(raw, unique_taint),
        selector=value.selector,
        origins=value.origins,
        operations=operations,
        empty_reducer_identity=value.empty_reducer_identity,
    )


def _length(value: SelectedValue, unit: LengthUnit) -> int:
    if unit is LengthUnit.UNICODE_SCALARS and type(value) is JsonString:
        return len(value.value)
    if unit is LengthUnit.BYTES and type(value) is BytesValue:
        return len(value.value)
    if unit is LengthUnit.ITEMS and type(value) is JsonArray:
        return len(value.items)
    if unit is LengthUnit.MEMBERS and type(value) is JsonObject:
        return len(value.members)
    raise DiffHaltError(code=DiagnosticCode(value="diff.length.type"))


def _digest(
    value: SelectedValue,
    algorithm: DiffDigestAlgorithm,
    meter: DiffMeter,
) -> bytes:
    hasher = sha256() if algorithm is DiffDigestAlgorithm.SHA256 else sha512()
    if type(value) is BytesValue:
        require_work(meter, max(1, len(value.value)))
        hasher.update(value.value)
        return hasher.digest()
    if type(value) is JsonString:
        for character in value.value:
            require_work(meter)
            hasher.update(character.encode("utf-8"))
        return hasher.digest()
    raise DiffHaltError(code=DiagnosticCode(value="diff.digest.type"))


__all__ = ("apply_transforms",)
