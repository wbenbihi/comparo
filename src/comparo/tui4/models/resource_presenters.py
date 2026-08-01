"""Pure kind-specific projections over secret-safe resource summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.content import Content

from comparo.tui4.models.explorer import PROFILE_KINDS
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.explorer import environment_roles
from comparo.tui4.models.fields import Field

if TYPE_CHECKING:
    from comparo.application import ProjectResourceSummary


def _outline_value(summary: ProjectResourceSummary, label: str) -> str | None:
    """Return one conservative outline value by label, when present."""
    if summary.outline is None:
        return None
    for field in summary.outline.fields:
        if field.label == label:
            return field.value
    return None


def _targets(
    summary: ProjectResourceSummary,
    kind: str | frozenset[str],
    *,
    inbound: bool = False,
) -> tuple[str, ...]:
    """Return related resource IDs by target kind and direction."""
    if summary.outline is None:
        return ()
    kinds = {kind} if isinstance(kind, str) else kind
    return tuple(
        relationship.target.resource_id
        for relationship in summary.outline.relationships
        if relationship.target.kind in kinds and (relationship.origin == "used-by") == inbound
    )


def project_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return rows derived from Project defaults."""
    summary = entry.summary
    rows: list[Field] = []
    subjects = _targets(summary, "Request")
    gates = _targets(summary, "GateProfile")
    roles = environment_roles(summary)
    baseline = [resource_id for resource_id, role in roles.items() if role == "baseline"]
    candidates = [resource_id for resource_id, role in roles.items() if role == "candidate"]
    if baseline:
        rows.append(Field("baseline", Content(", ".join(baseline))))
    if candidates:
        rows.append(Field("candidates", Content(", ".join(candidates))))
    if subjects:
        rows.append(Field("default subjects", Content(", ".join(subjects))))
    if gates:
        rows.append(Field("gate", Content(", ".join(gates))))
    return tuple(rows)


def environment_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return rows derived from one Environment summary."""
    summary = entry.summary
    facts = entry.facts
    rows = [
        Field("role", facts.role or "—"),
        Field(
            "locality",
            facts.locality or "—",
            tone="danger" if facts.locality == "live" else None,
        ),
    ]
    safety = _targets(summary, "SafetyProfile")
    if safety:
        rows.append(Field("safety", Content(", ".join(safety))))
    variables = _outline_value(summary, "variables")
    if variables:
        rows.append(Field("variables", Content(variables)))
    return tuple(rows)


def request_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return rows derived from one Request summary."""
    summary = entry.summary
    facts = entry.facts
    rows = [Field("method", facts.method or "—")]
    endpoint = _outline_value(summary, "endpoint") or _outline_value(summary, "url")
    if endpoint:
        rows.append(Field("endpoint", Content(endpoint)))
    for label, target in (
        ("matrices", "Matrix"),
        ("response schema", "Schema"),
        ("profiles", PROFILE_KINDS),
    ):
        targets = _targets(summary, target)
        if targets:
            rows.append(Field(label, Content(", ".join(targets))))
    rows.append(Field("streaming", "yes (tagged)" if facts.streaming else "—"))
    return tuple(rows)


def matrix_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return rows derived from one Matrix summary."""
    summary = entry.summary
    rows: list[Field] = []
    for label in ("axes", "cases", "include", "exclude", "combine"):
        value = _outline_value(summary, label)
        if value:
            rows.append(Field(label, Content(value)))
    used_by = _targets(summary, "Request", inbound=True)
    if used_by:
        rows.append(Field("used by", Content(", ".join(used_by))))
    return tuple(rows)


@dataclass(frozen=True)
class MatrixDimension:
    """One safe declared Matrix dimension or explicit-case collection."""

    id: str
    role: str
    shape: str


def matrix_dimensions(entry: ResourceEntry) -> tuple[MatrixDimension, ...]:
    """Describe authored Matrix groups without resolving execution cells locally."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    dimensions = [
        MatrixDimension(field.label, "axis", field.value)
        for field in outline.fields
        if len(field.path) == 2
        and field.path[0] == "axes"
        and field.category == "container"
        and field.value.startswith("array ·")
    ]
    dimensions.extend(
        MatrixDimension(field.label, "cases", field.value)
        for field in outline.fields
        if len(field.path) == 1
        and field.path[0] in {"cases", "include", "exclude"}
        and field.category == "container"
        and field.value.startswith("array ·")
    )
    return tuple(dimensions)


def schema_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Summarize a local or remote response Schema safely."""
    summary = entry.summary
    rows: list[Field] = []
    for label in ("type", "required", "properties", "items", "url"):
        value = _outline_value(summary, label)
        if value:
            rows.append(Field(label, Content(value)))
    used_by = _targets(summary, "Request", inbound=True)
    if used_by:
        rows.append(Field("used by", Content(", ".join(used_by))))
    return tuple(rows)


def value_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Summarize a reusable Value without resolving its expressions."""
    summary = entry.summary
    rows: list[Field] = []
    value = _outline_value(summary, "value") or _outline_value(summary, "spec")
    if value:
        rows.append(Field("shape", Content(value)))
    consumers = (
        tuple(
            relationship.target.resource_id
            for relationship in summary.outline.relationships
            if relationship.origin == "used-by"
        )
        if summary.outline is not None
        else ()
    )
    if consumers:
        rows.append(Field("used by", Content(", ".join(consumers))))
    return tuple(rows)


def profile_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Summarize any policy/profile using safe structural rows."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    rows = [Field("structure", f"{len(outline.fields)} fields")]
    outbound = tuple(
        relationship.target.resource_id
        for relationship in outline.relationships
        if relationship.origin != "used-by"
    )
    inbound = tuple(
        relationship.target.resource_id
        for relationship in outline.relationships
        if relationship.origin == "used-by"
    )
    if outbound:
        rows.append(Field("uses", Content(", ".join(outbound))))
    if inbound:
        rows.append(Field("used by", Content(", ".join(inbound))))
    return tuple(rows)


def generic_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return a safe structural fallback for future or plugin-defined kinds."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    return (
        Field("structure", f"{len(outline.fields)} fields"),
        Field("relationships", str(len(outline.relationships))),
    )
