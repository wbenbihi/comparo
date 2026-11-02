"""Exercise relative selection, filtering, sorting, and semantic stream projection."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.diffs import PreparedDiffInputs
from comparo.core.diffs import PreparedExpression
from comparo.core.diffs import PreparedOperandError
from comparo.core.diffs import PreparedOperandReady
from comparo.core.diffs import PreparedOperandUnavailable
from comparo.core.diffs.item_selection import JsonPathDiffMeter
from comparo.core.diffs.item_selection import select_item
from comparo.core.diffs.item_selection import select_json
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.transforms import _operand
from comparo.core.diffs.transforms import _predicate
from comparo.core.diffs.transforms import apply_transforms
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import CollectionFilter
from comparo.core.domain.diffs.operations import CollectionMap
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffTransform
from comparo.core.domain.diffs.operations import RelativeSelection
from comparo.core.domain.diffs.operations import SortBy
from comparo.core.domain.diffs.operations import SortDirection
from comparo.core.domain.diffs.operations import StreamProjection
from comparo.core.domain.diffs.operations import StreamSemanticSource
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.items import ItemPredicateIdentity
from comparo.core.domain.selectors.items import ItemPredicateKind
from comparo.core.domain.selectors.items import ItemTypePredicate
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.diffs.factories import derived
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import meter
from tests.core.diffs.factories import rule
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _optional_item_selector
from tests.core.domain.diffs._invariant_support import _plural_item_selector
from tests.core.domain.diffs._invariant_support import _predicate as _equality_predicate
from tests.core.domain.diffs.test_model import _body_stream_selector
from tests.core.domain.diffs.test_model import _selector as _stream_root_selector

_SIDE = DiffSide.BASELINE


def _apply(
    values: tuple[DerivedValue, ...],
    transform: DiffTransform,
    prepared: PreparedDiffInputs | None = None,
    *,
    derived_limit: int = 100_000,
) -> tuple[DerivedValue, ...]:
    return apply_transforms(
        values,
        (transform,),
        inputs(rule()) if prepared is None else prepared,
        meter(derived_limit=derived_limit),
        _SIDE,
    )


def _no_path_selector(
    output_kind: ValueKind = ValueKind.STRING,
) -> CompiledItemSelector:
    base = _item_selector()
    contract = msgspec.structs.replace(base.input, kinds=(output_kind,))
    return type(base)(
        fingerprint=base.fingerprint,
        input=contract,
        path=NoPath(),
        capabilities=base.capabilities,
        output=contract,
    )


def _prepared(expression: LiteralExpr, result: object) -> PreparedDiffInputs:
    item = PreparedExpression(
        expression=expression,
        result=PreparedOperandReady(value=JsonString(value="ready")),
    )
    msgspec.structs.force_setattr(item, "result", result)
    return PreparedDiffInputs(
        rule_fingerprint=rule().fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        baseline_expressions=(item,),
    )


def test_relative_selection_and_collection_map_expand_each_current_item() -> None:
    source = derived(JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))))
    selector = _plural_item_selector()
    relative = RelativeSelection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "relative-select"),
        selector=selector,
    )
    mapped = CollectionMap(
        identity=_operation(DiffOperationFamily.TRANSFORM, "collection-map"),
        selector=selector,
    )
    for transform in (relative, mapped):
        result = _apply((source,), transform)
        assert tuple(item.value for item in result) == (
            JsonInteger(value=2),
            JsonInteger(value=1),
        )


def test_relative_selection_preserves_partial_outputs_on_budget_failure() -> None:
    transform = RelativeSelection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "relative-select"),
        selector=_plural_item_selector(),
    )
    source = derived(JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))))
    with pytest.raises(DiffHaltError) as caught:
        _apply((source,), transform, derived_limit=1)
    assert caught.value.side is _SIDE
    assert len(caught.value.partial_derived) == 1


def test_stream_non_data_sources_use_relative_selector_semantics() -> None:
    transform = StreamProjection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
        source=StreamSemanticSource.JSON_RECORD,
        selector=_item_selector(),
    )
    source = derived(JsonString(value="record"))
    assert _apply((source,), transform)[0].value == JsonString(value="record")


def test_sse_data_projection_extracts_data_and_preserves_partial_progress() -> None:
    transform = StreamProjection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
        source=StreamSemanticSource.SSE_DATA,
        selector=_no_path_selector(),
    )
    event = derived(
        JsonObject(
            members=(
                JsonObjectMember(name="event", value=JsonString(value="message")),
                JsonObjectMember(name="data", value=JsonString(value="hello")),
            )
        )
    )
    assert _apply((event,), transform)[0].value == JsonString(value="hello")
    invalid = derived(JsonInteger(value=1))
    with pytest.raises(DiffHaltError) as caught:
        _apply((event, invalid), transform)
    assert len(caught.value.partial_derived) == 1
    assert caught.value.side is _SIDE
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.stream.sse-event-type"


def test_stream_projection_expands_only_explicit_body_stream_collections() -> None:
    event = JsonObject(members=(JsonObjectMember(name="data", value=JsonString(value="hello")),))
    sse_data = StreamProjection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
        source=StreamSemanticSource.SSE_DATA,
        selector=_no_path_selector(),
    )
    body = derived(
        JsonArray(items=(event,)),
        selector=_body_stream_selector(BodyViewKind.SSE_EVENTS),
    )
    assert tuple(value.value for value in _apply((body,), sse_data)) == (JsonString(value="hello"),)
    malformed_body = derived(
        JsonString(value="not-a-stream-collection"),
        selector=_body_stream_selector(BodyViewKind.SSE_EVENTS),
    )
    with pytest.raises(DiffHaltError) as caught:
        _apply((malformed_body,), sse_data)
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.stream.collection-type"

    array_record = JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2)))
    native = derived(
        array_record,
        selector=_stream_root_selector(SelectorSource.STREAM_RECORDS),
    )
    record = StreamProjection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
        source=StreamSemanticSource.JSON_RECORD,
        selector=_no_path_selector(ValueKind.ARRAY),
    )
    result = _apply((native,), record)
    assert len(result) == 1
    assert result[0].value == array_record


def test_sse_data_projection_rejects_missing_data_and_authored_nested_path() -> None:
    base = StreamProjection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
        source=StreamSemanticSource.SSE_DATA,
        selector=_no_path_selector(),
    )
    missing = derived(JsonObject(members=()))
    with pytest.raises(DiffHaltError) as caught:
        _apply((missing,), base)
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.stream.sse-data-missing"

    event = derived(
        JsonObject(members=(JsonObjectMember(name="data", value=JsonString(value="x")),))
    )
    nested_selector = msgspec.structs.replace(base.selector)
    msgspec.structs.force_setattr(nested_selector, "path", _optional_item_selector().path)
    nested = msgspec.structs.replace(base, selector=nested_selector)
    with pytest.raises(DiffHaltError) as caught:
        _apply((event,), nested)
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.stream.sse-data-path"


def test_collection_filter_equality_and_negation_are_side_resolved() -> None:
    predicate = _equality_predicate()
    expression = cast("LiteralExpr", predicate.operator.expected)
    prepared = _prepared(expression, PreparedOperandReady(value=JsonString(value="ready")))
    matching = derived(JsonString(value="ready"), index=401)
    other = derived(JsonString(value="other"), index=402)
    equals = CollectionFilter(
        identity=_operation(DiffOperationFamily.TRANSFORM, "collection-filter"),
        predicate=predicate,
    )
    assert _apply((matching, other), equals, prepared) == (
        _apply((matching,), equals, prepared)[0],
    )

    not_predicate = msgspec.structs.replace(
        predicate,
        identity=ItemPredicateIdentity(
            namespace="comparo.core",
            kind=ItemPredicateKind.NOT_EQUALS,
            version="1",
        ),
    )
    not_equals = msgspec.structs.replace(equals, predicate=not_predicate)
    result = _apply((matching, other), not_equals, prepared)
    assert len(result) == 1
    assert result[0].value == JsonString(value="other")


def test_collection_filter_taints_public_match_from_sensitive_expected_operand() -> None:
    predicate = _equality_predicate()
    expression = cast("LiteralExpr", predicate.operator.expected)
    origin = TaintOriginId(value="diff-filter-secret")
    prepared = _prepared(
        expression,
        PreparedOperandReady(value=SensitiveValue(JsonString(value="ready"), origins=(origin,))),
    )
    transform = CollectionFilter(
        identity=_operation(DiffOperationFamily.TRANSFORM, "collection-filter"),
        predicate=predicate,
    )

    result = _apply((derived(JsonString(value="ready")),), transform, prepared)[0]

    assert type(result.value) is SensitiveValue
    assert result.value.origins == (origin,)
    with pytest.raises(DiffHaltError) as caught:
        _apply(
            (derived(JsonString(value="ready")),),
            transform,
            prepared,
            derived_limit=0,
        )
    assert caught.value.partial_derived == ()
    assert caught.value.side is _SIDE


def test_collection_filter_type_predicate_and_empty_optional_match() -> None:
    predicate = CompiledItemPredicate(
        identity=ItemPredicateIdentity(
            namespace="comparo.core",
            kind=ItemPredicateKind.IS_TYPE,
            version="1",
        ),
        selector=_item_selector(output_kind=ValueKind.INTEGER),
        operator=ItemTypePredicate(expected=ValueKind.INTEGER),
    )
    transform = CollectionFilter(
        identity=_operation(DiffOperationFamily.TRANSFORM, "collection-filter"),
        predicate=predicate,
    )
    assert len(_apply((derived(JsonInteger(value=1)),), transform)) == 1

    optional = msgspec.structs.replace(predicate)
    msgspec.structs.force_setattr(optional, "selector", _optional_item_selector())
    optional_transform = msgspec.structs.replace(transform, predicate=optional)
    assert _apply((derived(JsonObject(members=())),), optional_transform) == ()


def test_collection_filter_no_path_predicate_compares_current_value() -> None:
    predicate = msgspec.structs.replace(
        _equality_predicate(),
        selector=_no_path_selector(),
    )
    expression = cast("LiteralExpr", predicate.operator.expected)
    assert _predicate(
        derived(JsonString(value="ready")),
        predicate,
        _prepared(expression, PreparedOperandReady(value=JsonString(value="ready"))),
        meter(),
        _SIDE,
    ) == (True, ())


def test_collection_filter_rejects_non_json_path_and_plural_matches() -> None:
    predicate = _equality_predicate()
    path_predicate = msgspec.structs.replace(predicate)
    msgspec.structs.force_setattr(path_predicate, "selector", _optional_item_selector())
    with pytest.raises(DiffHaltError) as wrong_type:
        _predicate(
            derived(BytesValue(value=b"raw")),
            path_predicate,
            inputs(rule()),
            meter(),
            _SIDE,
        )
    assert wrong_type.value.code is not None
    assert wrong_type.value.code.value == "diff.filter.type"

    plural = msgspec.structs.replace(predicate)
    msgspec.structs.force_setattr(plural, "selector", _plural_item_selector())
    with pytest.raises(DiffHaltError) as cardinality:
        _predicate(
            derived(JsonArray(items=(JsonString(value="a"), JsonString(value="b")))),
            plural,
            inputs(rule()),
            meter(),
            _SIDE,
        )
    assert cardinality.value.code is not None
    assert cardinality.value.code.value == "diff.filter.cardinality"


def test_filter_operand_terminal_states_are_preserved() -> None:
    predicate = _equality_predicate()
    expression = cast("LiteralExpr", predicate.operator.expected)
    terminals = (
        PreparedOperandUnavailable(code=DiagnosticCode(value="operand.missing")),
        PreparedOperandError(code=DiagnosticCode(value="operand.error")),
    )
    for result in terminals:
        with pytest.raises(DiffHaltError):
            _operand(_prepared(expression, result), expression, _SIDE)
    with pytest.raises(TypeError, match="unsupported prepared"):
        _operand(_prepared(expression, object()), expression, _SIDE)


def test_sort_by_is_stable_directed_and_requires_one_key() -> None:
    ascending = SortBy(
        identity=_operation(DiffOperationFamily.TRANSFORM, "sort-by"),
        key=_item_selector(),
        direction=SortDirection.ASCENDING,
    )
    values = (
        derived(JsonString(value="b"), index=410),
        derived(JsonString(value="a"), index=411),
        derived(JsonString(value="a"), index=412),
    )
    result = _apply(values, ascending)
    assert tuple(item.value for item in result) == (
        JsonString(value="a"),
        JsonString(value="a"),
        JsonString(value="b"),
    )
    assert result[0].origins == values[1].origins
    descending = msgspec.structs.replace(ascending, direction=SortDirection.DESCENDING)
    assert _apply(values, descending)[0].value == JsonString(value="b")

    optional = msgspec.structs.replace(ascending)
    msgspec.structs.force_setattr(optional, "key", _optional_item_selector())
    with pytest.raises(DiffHaltError) as caught:
        _apply((derived(JsonObject(members=())),), optional)
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.sort.key-cardinality"


def test_select_item_and_select_json_cover_every_path_variant() -> None:
    source = derived(
        JsonObject(members=(JsonObjectMember(name="id", value=JsonString(value="x")),))
    )
    identity = _operation(DiffOperationFamily.TRANSFORM, "relative-select")
    assert len(select_item(source, _no_path_selector(ValueKind.OBJECT), identity, meter())) == 1
    assert (
        len(select_item(source, _item_selector(output_kind=ValueKind.OBJECT), identity, meter()))
        == 1
    )
    assert len(select_item(source, _optional_item_selector(), identity, meter())) == 1
    with pytest.raises(DiffHaltError) as wrong_type:
        select_item(
            derived(BytesValue(value=b"not-json")),
            _optional_item_selector(),
            identity,
            meter(),
        )
    assert wrong_type.value.code is not None
    assert wrong_type.value.code.value == "diff.relative.type"

    static = msgspec.structs.replace(_optional_item_selector())
    msgspec.structs.force_setattr(static, "path", StaticFieldPath(segments=("id",)))
    assert select_json(cast("JsonObject", source.value), static, meter()) == (
        JsonString(value="x"),
    )
    plural = _plural_item_selector()
    array = JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2)))
    assert select_json(array, plural, meter()) == (
        JsonInteger(value=2),
        JsonInteger(value=1),
    )
    unsupported = msgspec.structs.replace(static)
    msgspec.structs.force_setattr(unsupported, "path", object())
    with pytest.raises(DiffHaltError):
        select_json(cast("JsonObject", source.value), unsupported, meter())


def test_jsonpath_meter_delegates_exact_work_charge() -> None:
    wrapped = JsonPathDiffMeter(meter(work=1))
    wrapped.charge_work()
    with pytest.raises(DiffHaltError):
        wrapped.charge_work()
