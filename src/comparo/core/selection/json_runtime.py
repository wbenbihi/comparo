"""Shared internal JSON traversal nodes and budget propagation."""

from __future__ import annotations

from collections.abc import Iterator
from typing import final

import msgspec

from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonValue
from comparo.core.selection.accounting import SelectionMeter


@final
class JsonNode(msgspec.Struct, frozen=True, kw_only=True):
    """One JSON value with an O(1) parent-linked concrete location."""

    value: JsonValue
    parent: JsonNode | None
    token: str | None

    def __post_init__(self) -> None:
        """Require a token exactly for non-root nodes."""
        if (self.parent is None) != (self.token is None):
            raise ValueError("exactly child JSON nodes require a location token")
        if self.token is not None and type(self.token) is not str:
            raise TypeError("JSON node location tokens must be strings")


class JsonBudgetExceededError(Exception):
    """Private non-payload control flow for exact budget evidence."""

    __slots__ = ("result",)

    def __init__(self, result: SelectionBudgetExceeded) -> None:
        """Retain only the canonical value-free budget result."""
        self.result = result


def iter_children(
    node: JsonNode,
    meter: SelectionMeter,
    *,
    reverse: bool = False,
) -> Iterator[JsonNode]:
    """Yield charged children without allocating a width-sized reference list."""
    if type(node.value) is JsonArray:
        indices = range(len(node.value.items))
        for index in reversed(indices) if reverse else indices:
            charge_work(meter)
            yield child_node(node, str(index), node.value.items[index])
    elif type(node.value) is JsonObject:
        members = reversed(node.value.members) if reverse else iter(node.value.members)
        for member in members:
            charge_work(meter)
            yield child_node(node, member.name, member.value)


def child_node(parent: JsonNode, token: str, value: JsonValue) -> JsonNode:
    """Create a child without copying its complete ancestor pointer."""
    return JsonNode(value=value, parent=parent, token=token)


def render_json_pointer(node: JsonNode, meter: SelectionMeter) -> JsonPointer:
    """Render one final pointer after charging every retained token and code point."""
    tokens: list[str] = []
    current = node
    while current.parent is not None:
        charge_work(meter)
        token = current.token
        if token is None:  # pragma: no cover - guarded by JsonNode
            raise ValueError("child JSON node is missing its location token")
        for character in token:
            charge_work(meter)
            if character in {"~", "/"}:
                charge_work(meter)
        tokens.append(token.replace("~", "~0").replace("/", "~1"))
        current = current.parent
    tokens.reverse()
    return JsonPointer(value="" if not tokens else "/" + "/".join(tokens))


def strings_equal(left: str, right: str, meter: SelectionMeter) -> bool:
    """Compare Unicode scalar sequences with work charged before each pair."""
    if len(left) != len(right):
        return False
    for left_character, right_character in zip(left, right, strict=True):
        charge_work(meter)
        if left_character != right_character:
            return False
    return True


def compare_strings(left: str, right: str, meter: SelectionMeter) -> int:
    """Lexicographically compare Unicode scalars without an unbounded native scan."""
    for left_character, right_character in zip(left, right, strict=False):
        charge_work(meter)
        if left_character != right_character:
            return (left_character > right_character) - (left_character < right_character)
    return (len(left) > len(right)) - (len(left) < len(right))


def charge_work(meter: SelectionMeter) -> None:
    """Charge one work unit or halt with exact evidence."""
    if (exhaustion := meter.charge_work()) is not None:
        raise JsonBudgetExceededError(SelectionBudgetExceeded(exhaustion=exhaustion))


def charge_match(meter: SelectionMeter) -> None:
    """Charge one final occurrence or halt with exact evidence."""
    if (exhaustion := meter.charge_match()) is not None:
        raise JsonBudgetExceededError(SelectionBudgetExceeded(exhaustion=exhaustion))


def charge_result_bytes(meter: SelectionMeter, amount: int) -> None:
    """Charge exact final payload bytes or halt with exact evidence."""
    if (exhaustion := meter.charge_result_bytes(amount)) is not None:
        raise JsonBudgetExceededError(SelectionBudgetExceeded(exhaustion=exhaustion))


__all__ = (
    "JsonBudgetExceededError",
    "JsonNode",
    "charge_match",
    "charge_result_bytes",
    "charge_work",
    "child_node",
    "compare_strings",
    "iter_children",
    "render_json_pointer",
    "strings_equal",
)
