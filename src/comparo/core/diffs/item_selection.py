"""Neutral metered relative selection shared by diff pipeline stages."""

from __future__ import annotations

from typing import cast

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.json_transforms import encode_pointer_token
from comparo.core.diffs.json_transforms import pointer
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.value_support import contain
from comparo.core.diffs.value_support import require_work
from comparo.core.diffs.value_support import reveal
from comparo.core.diffs.value_support import value_size
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value
from comparo.core.selection.json_runtime import JsonNode
from comparo.core.selection.jsonpath_evaluator import evaluate_jsonpath


def select_item(
    value: DerivedValue,
    selector: CompiledItemSelector,
    identity: DiffOperationIdentity,
    meter: DiffMeter,
) -> tuple[DerivedValue, ...]:
    """Apply one relative selector and retain exact derived provenance."""
    raw = reveal(value.value)
    if type(selector.path) is NoPath:
        selected: tuple[SelectedValue, ...] = (raw,)
    else:
        if not is_json_value(raw):
            raise DiffHaltError(code=DiagnosticCode(value="diff.relative.type"))
        selected = select_json(raw, selector, meter)
    output: list[DerivedValue] = []
    try:
        for item in selected:
            output.append(  # noqa: PERF401 -- partial progress is observable
                derive_value(item, value, identity, meter)
            )
    except DiffHaltError as halted:
        halted.partial_derived = tuple(output)
        raise
    return tuple(output)


class JsonPathDiffMeter:
    """Adapt the diff work meter to the selection JSONPath evaluator."""

    __slots__ = ("meter",)

    def __init__(self, meter: DiffMeter) -> None:
        """Bind one shared diff meter."""
        self.meter = meter

    def charge_work(self, amount: int = 1) -> None:
        """Charge JSONPath traversal work through the diff budget."""
        require_work(self.meter, amount)


def select_json(
    root: JsonValue,
    selector: CompiledItemSelector,
    meter: DiffMeter,
) -> tuple[JsonValue, ...]:
    """Apply a supported relative JSON path under the shared work meter."""
    path = selector.path
    if type(path) is JsonPointerPath:
        found = pointer(root, path.pointer, meter)
        return () if found is None else (found,)
    if type(path) is StaticFieldPath:
        encoded = []
        for token in path.segments:
            require_work(meter, max(1, 2 * len(token) + 1))
            encoded.append(encode_pointer_token(token))
        authored = JsonPointer(value="/" + "/".join(encoded))
        found = pointer(root, authored, meter)
        return () if found is None else (found,)
    if type(path) is JsonPathExpression:
        nodes = evaluate_jsonpath(
            JsonNode(value=root, parent=None, token=None),
            path.ast,
            cast("object", JsonPathDiffMeter(meter)),  # type: ignore[arg-type]
        )
        return tuple(node.value for node in nodes)
    raise DiffHaltError(code=DiagnosticCode(value="diff.relative.path-unsupported"))


def derive_value(
    result: SelectedValue,
    source: DerivedValue,
    operation: DiffOperationIdentity,
    meter: DiffMeter,
) -> DerivedValue:
    """Charge and construct one sensitivity-preserving derived value."""
    size = value_size(result, meter)
    if (failure := meter.charge_derived(size)) is not None:
        raise DiffHaltError(exhaustion=failure)
    return DerivedValue(
        value=contain(result, source.taint_origins),
        selector=source.selector,
        origins=source.origins,
        operations=(*source.operations, operation),
        empty_reducer_identity=source.empty_reducer_identity,
    )


__all__ = ("JsonPathDiffMeter", "derive_value", "select_item", "select_json")
