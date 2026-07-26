"""Kind-aware Explorer overview grids built from safe structural projections."""

from __future__ import annotations

from collections import Counter
from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widgets import Label
from textual.widgets import Static

from comparo.application.project_outline import ProjectOutlineField
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.tui.presentation import safe_terminal_text


@final
class ResourceOverview(Vertical):
    """Dense explanatory overview whose sections adapt to the resource kind."""

    def __init__(
        self,
        resource: ProjectResourceSummary,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Retain one safe application-owned summary."""
        if type(resource) is not ProjectResourceSummary:
            raise TypeError("resource overview requires a typed safe summary")
        super().__init__(id=id, classes=classes)
        self._resource = resource

    @override
    def compose(self) -> ComposeResult:
        """Compose readable sections without loading authored values or response bodies."""
        sections = _sections(self._resource)
        with Horizontal(classes="explorer-overview-grid"):
            for title, body in sections[:3]:
                yield _OverviewSection(title, body)
        with Horizontal(classes="explorer-overview-grid explorer-overview-grid-secondary"):
            for title, body in sections[3:]:
                yield _OverviewSection(title, body)


@final
class _OverviewSection(Vertical):
    """One reusable labeled reading region inside an Explorer overview."""

    def __init__(self, title: str, body: str) -> None:
        super().__init__(classes="explorer-overview-section")
        self._title = title
        self._body = body

    @override
    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="explorer-section-title")
        yield Static(self._body, classes="explorer-overview-copy", markup=False)


def _sections(resource: ProjectResourceSummary) -> tuple[tuple[str, str], ...]:
    description = ("WHAT THIS RESOURCE DOES", _description(resource))
    identity = ("IDENTITY & SOURCE", _identity(resource))
    kind = resource.coordinate.kind
    if kind == "Request":
        return (
            description,
            ("REQUEST SHAPE", _request_shape(resource)),
            ("INPUTS & DIRECTIVES", _inputs(resource)),
            identity,
            ("VARIANTS & VALIDATION", _request_validation(resource)),
            ("RELATIONSHIPS", _relationships(resource)),
        )
    if kind == "Environment":
        return (
            description,
            ("CONNECTION", _environment_shape(resource)),
            ("AVAILABLE VALUES", _environment_values(resource)),
            identity,
            ("SAFETY BOUNDARY", _environment_safety(resource)),
            ("RELATIONSHIPS", _relationships(resource)),
        )
    if kind == "Matrix":
        return (
            description,
            ("COMPOSITION", _matrix_shape(resource)),
            ("AXES & CASES", _matrix_cases(resource)),
            identity,
            ("EXPANSION INPUTS", _container_summary(resource)),
            ("CONSUMERS", _relationships(resource)),
        )
    if kind == "Schema":
        return (
            description,
            ("SCHEMA SHAPE", _schema_shape(resource)),
            ("CONSTRAINTS", _schema_constraints(resource)),
            identity,
            ("DOCUMENT STRUCTURE", _container_summary(resource)),
            ("REFERENCES", _relationships(resource)),
        )
    return (
        description,
        ("EFFECTIVE STRUCTURE", _container_summary(resource)),
        ("DIRECTIVES & REFERENCES", _inputs(resource)),
        identity,
        ("COMPOSITION", _relationships(resource)),
    )


def _description(resource: ProjectResourceSummary) -> str:
    return safe_terminal_text(
        resource.description or "No description provided.",
        maximum_characters=1024,
        multiline=True,
    )


def _identity(resource: ProjectResourceSummary) -> str:
    coordinate = resource.coordinate
    source = resource.source
    return (
        f"Namespace       {coordinate.namespace}\n"
        f"Resource        {coordinate.kind}/{coordinate.resource_id}\n"
        f"Source          {source.source} · document {source.document}\n"
        f"Location        line {source.line}, column {source.column}\n"
        f"Fingerprint     {coordinate.fingerprint[:18]}…"
    )


def _request_shape(resource: ProjectResourceSummary) -> str:
    fields = _fields(resource)
    return (
        f"Method          {_value(fields, ('request', 'method')) or 'unavailable'}\n"
        f"Endpoint        {_value(fields, ('request', 'endpoint')) or 'unavailable'}\n"
        f"Headers         {_value(fields, ('request', 'headers')) or 'none'}\n"
        f"Query           {_value(fields, ('request', 'query')) or 'none'}\n"
        f"Body            {_value(fields, ('request', 'body')) or 'none'}"
    )


def _inputs(resource: ProjectResourceSummary) -> str:
    selected = tuple(
        field for field in _fields(resource) if field.category in {"directive", "reference"}
    )
    if not selected:
        return "No projected directives or reusable references."
    return "\n".join(f"{'.'.join(field.path):<22} {field.value}" for field in selected[:8])


def _request_validation(resource: ProjectResourceSummary) -> str:
    fields = _fields(resource)
    matrices = _value(fields, ("matrices",)) or "none"
    assertions = _value(fields, ("assertions",)) or "none"
    diffs = _value(fields, ("diffs",)) or "none"
    return f"Matrices        {matrices}\nAssertions      {assertions}\nDiffs           {diffs}"


def _environment_shape(resource: ProjectResourceSummary) -> str:
    fields = _fields(resource)
    return (
        f"Base URL        {_value(fields, ('baseUrl',)) or 'unavailable'}\n"
        f"Variables       {_value(fields, ('variables',)) or 'none'}\n"
        "Exact values stay concealed until an explicit value directive is resolved."
    )


def _environment_values(resource: ProjectResourceSummary) -> str:
    selected = tuple(field for field in _fields(resource) if field.path[:1] == ("variables",))
    if not selected:
        return "No projected environment value names."
    return "\n".join(
        f"{'.'.join(field.path[1:]) or 'variables':<18} {field.value}" for field in selected[:10]
    )


def _environment_safety(resource: ProjectResourceSummary) -> str:
    tags = ", ".join(resource.tags) or "none"
    return (
        f"Classification tags    {tags}\n"
        "Environment sources are available to explicit $env directives only.\n"
        "Secret values are never projected into Explorer."
    )


def _matrix_shape(resource: ProjectResourceSummary) -> str:
    fields = _fields(resource)
    return (
        f"Combine         {_value(fields, ('combine',)) or 'explicit cases'}\n"
        f"Axes            {_value(fields, ('axes',)) or 'none'}\n"
        f"Cases           {_value(fields, ('cases',)) or 'none'}\n"
        f"Include         {_value(fields, ('include',)) or 'none'}\n"
        f"Exclude         {_value(fields, ('exclude',)) or 'none'}"
    )


def _matrix_cases(resource: ProjectResourceSummary) -> str:
    selected = tuple(
        field
        for field in _fields(resource)
        if (
            (len(field.path) == 2 and field.path[0] == "axes")
            or (len(field.path) == 3 and field.path[0] == "cases" and field.path[-1] == "id")
        )
    )
    if not selected:
        return "No projected axes or named cases."
    return "\n".join(f"{'.'.join(field.path):<22} {field.value}" for field in selected[:10])


def _schema_shape(resource: ProjectResourceSummary) -> str:
    fields = _fields(resource)
    return (
        f"Type            {_value(fields, ('type',)) or 'unspecified'}\n"
        f"Properties      {_value(fields, ('properties',)) or 'none'}\n"
        f"Required        {_value(fields, ('required',)) or 'none'}"
    )


def _schema_constraints(resource: ProjectResourceSummary) -> str:
    selected = tuple(
        field
        for field in _fields(resource)
        if field.path and field.path[-1] in {"type", "format", "minimum", "maximum", "$ref"}
    )
    if not selected:
        return "No projected type, format, bounds, or reference constraints."
    return "\n".join(f"{'.'.join(field.path):<22} {field.value}" for field in selected[:9])


def _container_summary(resource: ProjectResourceSummary) -> str:
    selected = tuple(field for field in _fields(resource) if len(field.path) == 1)
    if not selected:
        return "No projected structural fields."
    return "\n".join(f"{field.label:<18} {field.value}" for field in selected[:10])


def _relationships(resource: ProjectResourceSummary) -> str:
    relationships = () if resource.outline is None else resource.outline.relationships
    if not relationships:
        return "No compiled resource dependencies."
    counts = Counter(item.target.kind for item in relationships)
    summary = " · ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
    details = "\n".join(
        f"{item.origin:<12} {item.target.kind}/{item.target.resource_id}"
        for item in relationships[:8]
    )
    return f"{summary}\n{details}"


def _fields(resource: ProjectResourceSummary) -> tuple[ProjectOutlineField, ...]:
    return () if resource.outline is None else resource.outline.fields


def _value(fields: tuple[ProjectOutlineField, ...], path: tuple[str, ...]) -> str:
    field = next((item for item in fields if item.path == path), None)
    return "" if field is None else field.value


__all__ = ("ResourceOverview",)
