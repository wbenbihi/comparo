"""Value-free structural provenance for one effective HTTP request occurrence."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.plan_occurrences import PlanOccurrenceInspection
from comparo.application.plan_occurrences import inspect_plan_occurrence
from comparo.application.preflight import PreparedProjectPlan
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.planning._canonical import find_resource
from comparo.core.planning._canonical import object_member
from comparo.core.planning._canonical import resource_spec
from comparo.core.runtime import execution_work_at


class RequestContributionKind(StrEnum):
    """Closed structural origins that may contribute to a wire request field."""

    VERSIONED_DEFAULT = "versioned-default"
    ENVIRONMENT = "environment"
    REQUEST = "request"
    INVOCATION = "invocation"


class RequestCompositionStrategy(StrEnum):
    """Closed runtime composition semantics, ordered from general to specific."""

    REPLACE = "replace"
    MERGE_ENVIRONMENT_THEN_REQUEST = "merge-environment-then-request"
    FALLBACK_REQUEST_THEN_ENVIRONMENT = "fallback-request-then-environment"
    ENVIRONMENT_ONLY = "environment-only"
    URL_ROUTING = "url-routing"


@final
class RequestFieldContribution(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free structural contributor in low-to-high precedence order."""

    kind: RequestContributionKind
    field_path: tuple[str, ...]
    resource: ResourceCoordinate | None = None

    def __post_init__(self) -> None:
        """Require a source coordinate exactly when the contribution is resource-owned."""
        if type(self.kind) is not RequestContributionKind:
            raise TypeError("request field contribution kind must be typed")
        if (
            type(self.field_path) is not tuple
            or not self.field_path
            or any(type(item) is not str or not item for item in self.field_path)
        ):
            raise ValueError("request field contribution path must be non-empty text")
        resource_owned = self.kind in {
            RequestContributionKind.ENVIRONMENT,
            RequestContributionKind.REQUEST,
        }
        if resource_owned != (type(self.resource) is ResourceCoordinate):
            raise ValueError("request field contribution coordinate contradicts its kind")


@final
class RequestFieldComposition(msgspec.Struct, frozen=True, kw_only=True):
    """Exact structural contributors and runtime composition semantics for one field."""

    field: str
    strategy: RequestCompositionStrategy
    contributions: tuple[RequestFieldContribution, ...]

    def __post_init__(self) -> None:
        """Require one named field and a non-empty immutable contribution chain."""
        if type(self.field) is not str or not self.field:
            raise ValueError("request composition field must be non-empty text")
        if type(self.strategy) is not RequestCompositionStrategy:
            raise TypeError("request composition strategy must be typed")
        if type(self.contributions) is not tuple or any(
            type(item) is not RequestFieldContribution for item in self.contributions
        ):
            raise TypeError("request field contributions must be immutable and typed")
        if not self.contributions:
            raise ValueError("request field composition requires at least one contribution")


@final
class RequestCompositionInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Frontend-neutral structural request composition for one canonical occurrence."""

    plan_fingerprint: str
    occurrence: PlanOccurrenceInspection
    fields: tuple[RequestFieldComposition, ...]

    def __post_init__(self) -> None:
        """Require one exact occurrence and unique canonical field entries."""
        if (
            type(self.plan_fingerprint) is not str
            or len(self.plan_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in self.plan_fingerprint)
        ):
            raise ValueError("request composition requires a SHA-256 plan fingerprint")
        if type(self.occurrence) is not PlanOccurrenceInspection:
            raise TypeError("request composition occurrence must be typed")
        if type(self.fields) is not tuple or any(
            type(item) is not RequestFieldComposition for item in self.fields
        ):
            raise TypeError("request composition fields must be immutable and typed")
        names = tuple(item.field for item in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("request composition fields must be unique")


def inspect_request_composition(
    project: PreparedProjectPlan,
    *,
    execution_ordinal: int,
) -> RequestCompositionInspection | None:
    """Inspect structural precedence without resolving values or causing effects."""
    if type(project) is not PreparedProjectPlan:
        raise TypeError("request composition inspection requires PreparedProjectPlan")
    if type(execution_ordinal) is not int or execution_ordinal < 0:
        raise ValueError("request composition execution ordinal must be non-negative")
    prepared = project.prepared
    work = execution_work_at(prepared, execution_ordinal)
    if work is None:
        return None
    if work.subject.identity.kind is not ResourceKind.REQUEST:
        return None
    request = find_resource(
        prepared.project,
        ResourceKind.REQUEST,
        work.subject.identity.resource_id,
        owner=prepared.project.project,
        field="subjects",
    )
    environment = find_resource(
        prepared.project,
        ResourceKind.ENVIRONMENT,
        work.environment.identity.resource_id,
        owner=prepared.project.project,
        field="environments",
    )
    authored = object_member(resource_spec(request), "request")
    if type(authored) is not ObjectExpr:  # pragma: no cover - compiler invariant
        return None
    subject = next(item for item in prepared.effective.subjects if item.definition == work.subject)
    edits = () if subject.request_edit is None else subject.request_edit.field_values()
    edited = frozenset(name for name, _expression in edits)
    request_fields = frozenset(member.name for member in authored.members)
    environment_fields = frozenset(member.name for member in resource_spec(environment).members)
    fields = tuple(
        item
        for field in _FIELD_ORDER
        if (
            item := _field_composition(
                field,
                request,
                environment,
                request_fields=request_fields,
                environment_fields=environment_fields,
                edited=edited,
            )
        )
        is not None
    )
    return RequestCompositionInspection(
        plan_fingerprint=prepared.plan.fingerprint.digest.hex_value,
        occurrence=inspect_plan_occurrence(work),
        fields=fields,
    )


_FIELD_ORDER = (
    "method",
    "url",
    "query",
    "headers",
    "cookies",
    "body",
    "bodyType",
    "auth",
    "stream",
    "timeout",
    "retries",
    "redirects",
    "tls",
    "proxy",
)


def _field_composition(  # noqa: C901 - closed field-semantics table
    field: str,
    request: CompiledResource,
    environment: CompiledResource,
    *,
    request_fields: frozenset[str],
    environment_fields: frozenset[str],
    edited: frozenset[str],
) -> RequestFieldComposition | None:
    contributions: list[RequestFieldContribution] = []
    strategy = RequestCompositionStrategy.REPLACE
    if field == "url":
        strategy = RequestCompositionStrategy.URL_ROUTING
        _resource_contribution(contributions, environment, "baseUrl")
        if "endpoint" in request_fields:
            _resource_contribution(contributions, request, "endpoint")
        if "endpoint" in edited:
            _invocation_contribution(contributions, "endpoint")
        if "url" in request_fields:
            _resource_contribution(contributions, request, "url")
        if "url" in edited:
            _invocation_contribution(contributions, "url")
    elif field in {"headers", "cookies"}:
        strategy = RequestCompositionStrategy.MERGE_ENVIRONMENT_THEN_REQUEST
        if field in environment_fields:
            _resource_contribution(contributions, environment, field)
        _request_contributions(contributions, request, field, request_fields, edited)
    elif field in {"auth", "timeout"}:
        strategy = RequestCompositionStrategy.FALLBACK_REQUEST_THEN_ENVIRONMENT
        if field == "timeout":
            _default_contribution(contributions, field)
        if field in environment_fields:
            _resource_contribution(contributions, environment, field)
        _request_contributions(contributions, request, field, request_fields, edited)
    elif field in {"tls", "proxy"}:
        strategy = RequestCompositionStrategy.ENVIRONMENT_ONLY
        if field == "tls":
            _default_contribution(contributions, field)
        if field in environment_fields:
            _resource_contribution(contributions, environment, field)
    else:
        if field in {"retries", "redirects"}:
            _default_contribution(contributions, field)
        _request_contributions(contributions, request, field, request_fields, edited)
    if not contributions:
        return None
    return RequestFieldComposition(
        field=field,
        strategy=strategy,
        contributions=tuple(contributions),
    )


def _request_contributions(
    result: list[RequestFieldContribution],
    request: CompiledResource,
    field: str,
    request_fields: frozenset[str],
    edited: frozenset[str],
) -> None:
    if field in request_fields:
        _resource_contribution(result, request, field)
    if field in edited:
        _invocation_contribution(result, field)


def _resource_contribution(
    result: list[RequestFieldContribution],
    resource: CompiledResource,
    field: str,
) -> None:
    kind = (
        RequestContributionKind.ENVIRONMENT
        if resource.definition.identity.kind is ResourceKind.ENVIRONMENT
        else RequestContributionKind.REQUEST
    )
    prefix = "request" if kind is RequestContributionKind.REQUEST else "spec"
    result.append(
        RequestFieldContribution(
            kind=kind,
            resource=_coordinate(resource.definition),
            field_path=(prefix, field),
        )
    )


def _invocation_contribution(result: list[RequestFieldContribution], field: str) -> None:
    result.append(
        RequestFieldContribution(
            kind=RequestContributionKind.INVOCATION,
            field_path=("requests", field),
        )
    )


def _default_contribution(result: list[RequestFieldContribution], field: str) -> None:
    result.append(
        RequestFieldContribution(
            kind=RequestContributionKind.VERSIONED_DEFAULT,
            field_path=("http", field),
        )
    )


def _coordinate(value: DefinitionRef) -> ResourceCoordinate:
    identity = value.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=value.fingerprint.digest.hex_value,
    )


__all__ = (
    "RequestCompositionInspection",
    "RequestCompositionStrategy",
    "RequestContributionKind",
    "RequestFieldComposition",
    "RequestFieldContribution",
    "inspect_request_composition",
)
