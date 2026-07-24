"""Sensitivity propagation checks for resolved HTTP redirects."""

from __future__ import annotations

from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value


def validate_sensitive_redirect_resolution(
    raw_origins: tuple[TaintOriginId, ...],
    resolved_url: AbsoluteHttpUrl,
) -> None:
    """Conservatively prevent a sealed Location from becoming public URL value data."""
    required = frozenset(raw_origins)
    _validate_sensitive_path(required, resolved_url.path)
    _validate_sensitive_query(required, resolved_url.query)


def _validate_sensitive_path(
    required: frozenset[TaintOriginId],
    path: UrlPath,
) -> None:
    """Require every value-bearing resolved path segment to preserve raw lineage."""
    for segment in path.segments:
        if is_sensitive_value(segment):
            if not required.issubset(segment.origins):
                raise ValueError("sensitive redirect path lineage must cover raw Location lineage")
            continue
        if type(segment) is DecodedPathSegment and segment.value:
            raise ValueError("sensitive redirect path values must remain sealed")
        if type(segment) is EncodedPathSegment and segment.value:
            raise ValueError("sensitive redirect path values must remain sealed")


def _validate_sensitive_query(
    required: frozenset[TaintOriginId],
    query: QueryParameters,
) -> None:
    """Require every value-bearing resolved query occurrence to preserve raw lineage."""
    for parameter in query.parameters:
        value = parameter.value
        if type(value) is BareQueryValue:
            continue
        if is_sensitive_value(value):
            if not required.issubset(value.origins):
                raise ValueError("sensitive redirect query lineage must cover raw Location lineage")
            continue
        if type(value) is QueryText and value.value:
            raise ValueError("sensitive redirect query values must remain sealed")
