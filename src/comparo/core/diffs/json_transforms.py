"""Bounded structural JSON operations used by diff transforms."""

from __future__ import annotations

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.value_support import canonical_key
from comparo.core.diffs.value_support import json_text_size
from comparo.core.diffs.value_support import require_derived_capacity
from comparo.core.diffs.value_support import require_work
from comparo.core.diffs.value_support import value_size
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import JsonShapeArrayMode
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue

_TYPE_NAMES = {
    "JsonNull": "null",
    "JsonBoolean": "boolean",
    "JsonInteger": "integer",
    "JsonDecimal": "number",
    "JsonString": "string",
    "JsonArray": "array",
    "JsonObject": "object",
}
_MAX_TREE_DEPTH = 256


def pointer(root: JsonValue, authored: JsonPointer, meter: DiffMeter) -> JsonValue | None:
    """Resolve one strict RFC 6901-style pointer with bounded work."""
    current = root
    if not authored.value:
        return current
    require_work(meter, len(authored.value))
    for encoded in authored.value[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if type(current) is JsonObject:
            member = None
            for item in current.members:
                require_work(meter, max(1, len(item.name) + len(token)))
                if item.name == token:
                    member = item
                    break
            if member is None:
                return None
            current = member.value
        elif (
            type(current) is JsonArray
            and token.isascii()
            and token.isdigit()
            and (token == "0" or not token.startswith("0"))  # noqa: S105
        ):
            index = _array_index(token, len(current.items))
            if index is None:
                return None
            current = current.items[index]
        else:
            return None
    return current


def _array_index(token: str, length: int) -> int | None:
    """Parse a canonical index incrementally without Python's text-to-int limit."""
    index = 0
    for character in token:
        index = index * 10 + ord(character) - ord("0")
        if index >= length:
            return None
    return index


def project(
    root: JsonValue,
    pointers: tuple[JsonPointer, ...],
    meter: DiffMeter,
) -> JsonValue:
    """Retain exactly the authored existing pointer subtrees."""
    if any(pointer(root, authored, meter) is None for authored in pointers):
        raise DiffHaltError(code=DiagnosticCode(value="diff.project.pointer-missing"))
    paths = tuple(pointer_tokens(authored, meter) for authored in pointers)
    if () in paths:
        require_derived_capacity(meter, value_size(root, meter))
        return root
    projected_size = _projected_size(root, paths, meter)
    if projected_size is not None:
        require_derived_capacity(meter, projected_size)
    projected = _project_paths(root, paths, meter)
    if projected is not None:
        return projected
    raise DiffHaltError(code=DiagnosticCode(value="diff.project.invalid-path"))


def _project_paths(
    root: JsonValue,
    paths: tuple[tuple[str, ...], ...],
    meter: DiffMeter,
    depth: int = 0,
) -> JsonValue | None:
    _require_depth(depth)
    require_work(meter)
    if type(root) is JsonObject:
        members = []
        for member in root.members:
            children = _child_paths(paths, member.name, meter)
            if not children:
                continue
            value = (
                member.value
                if () in children
                else _project_paths(member.value, children, meter, depth + 1)
            )
            if value is not None:
                members.append(JsonObjectMember(name=member.name, value=value))
        return JsonObject(members=tuple(members))
    if type(root) is JsonArray:
        items = []
        for index, item in enumerate(root.items):
            children = _child_paths(paths, str(index), meter)
            if not children:
                continue
            value = item if () in children else _project_paths(item, children, meter, depth + 1)
            if value is not None:
                items.append(value)
        return JsonArray(items=tuple(items))
    return None


def exclude(
    root: JsonValue,
    pointers: tuple[JsonPointer, ...],
    meter: DiffMeter,
) -> JsonValue | None:
    """Remove exactly the authored existing pointer subtrees."""
    if any(pointer(root, authored, meter) is None for authored in pointers):
        raise DiffHaltError(code=DiagnosticCode(value="diff.exclude.pointer-missing"))
    paths = tuple(pointer_tokens(authored, meter) for authored in pointers)
    if () in paths:
        return None
    require_derived_capacity(meter, _excluded_size(root, paths, meter))
    return _exclude_paths(root, paths, meter)


def _projected_size(
    root: JsonValue,
    paths: tuple[tuple[str, ...], ...],
    meter: DiffMeter,
    depth: int = 0,
) -> int | None:
    """Measure the exact retained projection before allocating its output tree."""
    _require_depth(depth)
    require_work(meter)
    if type(root) is JsonObject:
        total = 2
        retained = 0
        for member in root.members:
            children = _child_paths(paths, member.name, meter)
            if not children:
                continue
            child_size = (
                value_size(member.value, meter)
                if () in children
                else _projected_size(member.value, children, meter, depth + 1)
            )
            if child_size is None:
                continue
            total += int(retained > 0) + 3 + json_text_size(member.name, meter) + child_size
            retained += 1
        return total
    if type(root) is JsonArray:
        total = 2
        retained = 0
        for index, item in enumerate(root.items):
            children = _child_paths(paths, str(index), meter)
            if not children:
                continue
            child_size = (
                value_size(item, meter)
                if () in children
                else _projected_size(item, children, meter, depth + 1)
            )
            if child_size is None:
                continue
            total += int(retained > 0) + child_size
            retained += 1
        return total
    return None


def _excluded_size(
    root: JsonValue,
    paths: tuple[tuple[str, ...], ...],
    meter: DiffMeter,
    depth: int = 0,
) -> int:
    """Measure the exact exclusion output before allocating its output tree."""
    _require_depth(depth)
    require_work(meter)
    if type(root) is JsonObject:
        total = 2
        retained = 0
        for member in root.members:
            children = _child_paths(paths, member.name, meter)
            if () in children:
                continue
            child_size = (
                _excluded_size(member.value, children, meter, depth + 1)
                if children
                else value_size(member.value, meter)
            )
            total += int(retained > 0) + 3 + json_text_size(member.name, meter) + child_size
            retained += 1
        return total
    if type(root) is JsonArray:
        total = 2
        retained = 0
        for index, item in enumerate(root.items):
            children = _child_paths(paths, str(index), meter)
            if () in children:
                continue
            child_size = (
                _excluded_size(item, children, meter, depth + 1)
                if children
                else value_size(item, meter)
            )
            total += int(retained > 0) + child_size
            retained += 1
        return total
    return value_size(root, meter)


def _exclude_paths(
    root: JsonValue,
    paths: tuple[tuple[str, ...], ...],
    meter: DiffMeter,
    depth: int = 0,
) -> JsonValue:
    _require_depth(depth)
    require_work(meter)
    if type(root) is JsonObject:
        members = []
        for member in root.members:
            children = _child_paths(paths, member.name, meter)
            if () in children:
                continue
            value = (
                _exclude_paths(member.value, children, meter, depth + 1)
                if children
                else member.value
            )
            members.append(JsonObjectMember(name=member.name, value=value))
        return JsonObject(members=tuple(members))
    if type(root) is JsonArray:
        items = []
        for index, item in enumerate(root.items):
            children = _child_paths(paths, str(index), meter)
            if () in children:
                continue
            items.append(_exclude_paths(item, children, meter, depth + 1) if children else item)
        return JsonArray(items=tuple(items))
    return root


def shape(
    value: JsonValue,
    mode: JsonShapeArrayMode,
    meter: DiffMeter,
    depth: int = 0,
) -> JsonObject:
    """Build a bounded value-free JSON shape tree."""
    _require_depth(depth)
    require_work(meter)
    name = _TYPE_NAMES[type(value).__name__]
    members = [JsonObjectMember(name="type", value=JsonString(value=name))]
    if type(value) is JsonObject:
        members.append(
            JsonObjectMember(
                name="members",
                value=JsonObject(
                    members=tuple(
                        JsonObjectMember(
                            name=item.name,
                            value=shape(item.value, mode, meter, depth + 1),
                        )
                        for item in value.members
                    )
                ),
            )
        )
    elif type(value) is JsonArray:
        shapes = tuple(shape(item, mode, meter, depth + 1) for item in value.items)
        if mode is JsonShapeArrayMode.UNION:
            unique = []
            keys = set()
            for item_shape in shapes:
                key = canonical_key(item_shape, meter)
                if key not in keys:
                    keys.add(key)
                    unique.append(item_shape)
            shapes = tuple(unique)
        members.append(JsonObjectMember(name="items", value=JsonArray(items=shapes)))
    return JsonObject(members=tuple(members))


def shape_size_upper_bound(
    value: JsonValue,
    mode: JsonShapeArrayMode,
    meter: DiffMeter,
    depth: int = 0,
) -> int:
    """Measure the positional shape representation before allocating the shape tree."""
    _require_depth(depth)
    require_work(meter)
    type_name = _TYPE_NAMES[type(value).__name__]
    type_member = 3 + json_text_size("type", meter) + 2 + json_text_size(type_name, meter)
    if type(value) is JsonObject:
        nested = 2 + max(0, len(value.members) - 1)
        for member in value.members:
            nested += 3 + json_text_size(member.name, meter)
            nested += shape_size_upper_bound(member.value, mode, meter, depth + 1)
        return 3 + type_member + 3 + json_text_size("members", meter) + nested
    if type(value) is JsonArray:
        nested = 2 + max(0, len(value.items) - 1)
        nested += sum(shape_size_upper_bound(item, mode, meter, depth + 1) for item in value.items)
        return 3 + type_member + 3 + json_text_size("items", meter) + nested
    return 2 + type_member


def pointer_tokens(pointer: JsonPointer, meter: DiffMeter) -> tuple[str, ...]:
    """Decode a validated pointer into member/index tokens."""
    if not pointer.value:
        return ()
    require_work(meter, len(pointer.value))
    return tuple(
        token.replace("~1", "/").replace("~0", "~") for token in pointer.value[1:].split("/")
    )


def _child_paths(
    paths: tuple[tuple[str, ...], ...],
    token: str,
    meter: DiffMeter,
) -> tuple[tuple[str, ...], ...]:
    """Find child paths while charging for comparisons and tuple slicing."""
    children = []
    for path in paths:
        head = path[0] if path else ""
        require_work(meter, max(1, len(token) + len(head)))
        if path and head == token:
            require_work(meter, max(1, len(path)))
            children.append(path[1:])
    return tuple(children)


def encode_pointer_token(token: str) -> str:
    """Encode one static field as an RFC 6901 pointer token."""
    return token.replace("~", "~0").replace("/", "~1")


def _require_depth(depth: int) -> None:
    if depth > _MAX_TREE_DEPTH:
        raise DiffHaltError(code=DiagnosticCode(value="diff.value.depth-unsupported"))


__all__ = (
    "encode_pointer_token",
    "exclude",
    "pointer",
    "project",
    "shape",
    "shape_size_upper_bound",
)
