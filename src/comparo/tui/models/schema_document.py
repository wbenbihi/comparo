"""Bounded schema structure and local reference expansion from safe outlines."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote
from urllib.parse import urldefrag
from urllib.parse import urljoin

from comparo.tui.models.explorer import ResourceEntry


@dataclass(frozen=True)
class SchemaNode:
    """One schema location, including array items, definitions and reference boundaries."""

    path: tuple[str, ...]
    type: str
    required: bool
    shape: str
    constraints: tuple[str, ...]
    schema_path: tuple[str, ...]
    source_path: tuple[str, ...]
    source: str
    reference: str = ""
    reference_state: str = ""
    role: str = "property"


def schema_nodes(
    entry: ResourceEntry, catalog: tuple[ResourceEntry, ...] = ()
) -> tuple[SchemaNode, ...]:
    """Expand project-local references with bounded depth and explicit cycle stops."""
    document = _SchemaDocument(entry, catalog)
    document.visit(entry.key, (), (), "root", False, "root", ())
    return tuple(document.result)


class _SchemaDocument:
    def __init__(self, entry: ResourceEntry, catalog: tuple[ResourceEntry, ...]) -> None:
        self.entries = {item.key: item for item in (*catalog, entry)}
        self.indexes = {
            key: {field.path: field for field in item.summary.outline.fields}
            for key, item in self.entries.items()
            if item.summary.outline is not None
        }
        self.result: list[SchemaNode] = []

    def exact(self, key: str, path: tuple[str, ...]) -> str:
        field = self.indexes.get(key, {}).get(path)
        return "" if field is None else field.exact_value or ""

    def reference(
        self, key: str, path: tuple[str, ...], ref: str
    ) -> tuple[str, tuple[str, ...]] | None:
        base = self.exact(key, ("$id",))
        for length in range(1, len(path) + 1):
            nested = self.exact(key, (*path[:length], "$id"))
            if nested:
                base = urljoin(base, nested)
        uri, fragment = urldefrag(urljoin(base, ref))
        candidates = (
            [key]
            if not uri or uri == urldefrag(base)[0]
            else [item for item in self.entries if self.exact(item, ("$id",)) == uri]
        )
        if len(candidates) != 1:
            return None
        target = candidates[0]
        if fragment and not fragment.startswith("/"):
            anchors = [
                p[:-1]
                for p in self.indexes[target]
                if p[-1:] == ("$anchor",) and self.exact(target, p) == fragment
            ]
            return (target, anchors[0]) if len(anchors) == 1 else None
        pointer = (
            tuple(
                unquote(part).replace("~1", "/").replace("~0", "~")
                for part in fragment[1:].split("/")
            )
            if fragment
            else ()
        )
        return (target, pointer) if pointer in self.indexes[target] else None

    def visit(
        self,
        key: str,
        source_path: tuple[str, ...],
        display_path: tuple[str, ...],
        label: str,
        required: bool,
        role: str,
        active: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> None:
        if len(self.result) >= 512 or len(display_path) > 32:
            return
        index = self.indexes.get(key, {})
        field = index.get(source_path)
        if field is None:
            return
        ref = self.exact(key, (*source_path, "$ref"))
        target = self.reference(key, source_path, ref) if ref else None
        identity = (key, source_path)
        cycle = identity in active
        state = (
            "cycle"
            if cycle
            else "resolved locally"
            if target
            else "target unavailable locally"
            if ref
            else ""
        )
        node_type = self.exact(key, (*source_path, "type")) or (
            "reference"
            if ref
            else "object"
            if (*source_path, "properties") in index
            else "array"
            if (*source_path, "items") in index
            else "any"
        )
        if field.exact_kind == "boolean":
            node_type = "any" if field.exact_value == "true" else "never"
        constraints = tuple(
            f"{p[-1]}={f.exact_value}"
            for p, f in index.items()
            if len(p) == len(source_path) + 1
            and p[:-1] == source_path
            and f.exact_value is not None
            and p[-1] not in {"type", "$ref", "$id"}
        )
        self.result.append(
            SchemaNode(
                path=(label,),
                type=node_type,
                required=required,
                shape=field.value,
                constraints=constraints,
                schema_path=display_path,
                source_path=source_path,
                source=f"Schema/{self.entries[key].summary.coordinate.resource_id}",
                reference=ref,
                reference_state=state,
                role=role,
            )
        )
        if cycle:
            return
        active = (*active, identity)
        if target is not None:
            self.visit(
                target[0],
                target[1],
                (*display_path, "$ref"),
                "↳ " + ref,
                False,
                "reference",
                active,
            )
        self.children(key, source_path, display_path, active)

    def children(
        self,
        key: str,
        source_path: tuple[str, ...],
        display_path: tuple[str, ...],
        active: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> None:
        index = self.indexes[key]
        required_names = {
            f.exact_value
            for p, f in index.items()
            if p[:-1] == (*source_path, "required") and f.exact_value is not None
        }
        for p in index:
            if p[:-2] == source_path and p[-2:-1] == ("properties",):
                self.visit(
                    key,
                    p,
                    (*display_path, "properties", p[-1]),
                    p[-1],
                    p[-1] in required_names,
                    "property",
                    active,
                )
        for group in (
            "$defs",
            "definitions",
            "allOf",
            "oneOf",
            "anyOf",
            "prefixItems",
            "patternProperties",
        ):
            group_path = (*source_path, group)
            if group_path in index and len(self.result) < 512:
                self.result.append(
                    SchemaNode(
                        path=(group,),
                        type="group",
                        required=False,
                        shape=index[group_path].value,
                        constraints=(),
                        schema_path=(*display_path, group),
                        source_path=group_path,
                        source=f"Schema/{self.entries[key].summary.coordinate.resource_id}",
                        role="group",
                    )
                )
                for p in index:
                    if p[:-1] == group_path:
                        self.visit(
                            key, p, (*display_path, group, p[-1]), p[-1], False, "schema", active
                        )
        for keyword in (
            "items",
            "additionalProperties",
            "contains",
            "not",
            "if",
            "then",
            "else",
            "propertyNames",
        ):
            p = (*source_path, keyword)
            if p in index:
                self.visit(
                    key,
                    p,
                    (*display_path, keyword),
                    "0..n" if keyword == "items" else keyword,
                    False,
                    "schema",
                    active,
                )
