"""Navigable safe JSON documents and per-path declaration facts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from comparo.tui.models.explorer import ResourceEntry
from comparo.tui.models.resource import ResolvedResourceField

BODY = ("request", "body")


@dataclass(frozen=True)
class BodyLine:
    """One displayed line tied to a structural path and its safe inspector facts."""

    path: tuple[str, ...]
    text: str
    value: str
    directive: str
    origins: tuple[str, ...]
    tone: str | None = None


def body_resolution_paths(entry: ResourceEntry) -> tuple[tuple[str, ...], ...]:
    """Resolve document leaves independently; directive internals are not JSON children."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    fields = {field.path: field for field in outline.fields if field.path[:2] == BODY}
    paths = []
    for path, field in fields.items():
        if field.category == "container":
            continue
        if all(
            fields[parent].category == "container"
            for length in range(2, len(path))
            if (parent := path[:length]) in fields
        ):
            paths.append(path)
    return tuple(paths)


def body_document(
    entry: ResourceEntry,
    resolved: Mapping[tuple[str, ...], ResolvedResourceField],
) -> tuple[BodyLine, ...]:
    """Render formatted JSON from bounded public projections with exact tuple paths."""
    if entry.summary.outline is None:
        return ()
    fields = {field.path: field for field in entry.summary.outline.fields if field.path[:2] == BODY}
    if BODY not in fields:
        return ()
    paths = dict.fromkeys((*fields, *(path for path in resolved if path[:2] == BODY)))
    lines: list[BodyLine] = []

    def visit(path: tuple[str, ...], depth: int, prefix: str = "", comma: bool = False) -> None:
        if len(lines) >= 512:
            return
        authored = fields.get(path)
        effective = resolved.get(path)
        shape = authored.value if authored is not None else ""
        if effective is not None and not effective.value.startswith("<"):
            shape = effective.value
        container = shape.split(" ·", 1)[0] if " ·" in shape else ""
        children = (
            [child for child in paths if child[:-1] == path]
            if container in {"object", "array"}
            else []
        )
        value = (
            effective.value
            if effective
            else authored.exact_value or authored.value
            if authored
            else "<unavailable>"
        )
        directive = (
            authored.value
            if authored and authored.category in {"directive", "reference", "sensitive"}
            else "literal"
        )
        origins = authored.origins if authored else ()
        tone = effective.tone if effective else None
        if resolved and effective is None and container not in {"object", "array"}:
            value, tone = "<not resolved>", "warn"
        suffix = "," if comma else ""
        indent = "  " * depth
        if container in {"object", "array"}:
            opening, closing = ("{", "}") if container == "object" else ("[", "]")
            if children:
                lines.append(BodyLine(path, indent + prefix + opening, shape, directive, origins))
                for index, child in enumerate(children):
                    key = (
                        json.dumps(child[-1], ensure_ascii=False) + ": "
                        if container == "object"
                        else ""
                    )
                    visit(child, depth + 1, key, index < len(children) - 1)
                lines.append(BodyLine(path, indent + closing + suffix, shape, directive, origins))
            else:
                lines.append(
                    BodyLine(
                        path,
                        indent + prefix + opening + closing + suffix,
                        shape,
                        directive,
                        origins,
                    )
                )
            return
        scalar = value
        numeric = tone in {"value.integer", "value.number", "value.boolean", "value.null"}
        scalar = value if numeric else json.dumps(value, ensure_ascii=False)
        lines.append(
            BodyLine(path, indent + prefix + scalar + suffix, value, directive, origins, tone)
        )

    visit(BODY, 0)
    return tuple(lines)


def json_path(path: tuple[str, ...]) -> str:
    """Render an unambiguous path, including dotted names and array indexes."""
    result = "$"
    for part in path[2:]:
        result += (
            f"[{part}]"
            if part.isdecimal()
            else f".{part}"
            if part.isidentifier()
            else f"[{json.dumps(part)}]"
        )
    return result
