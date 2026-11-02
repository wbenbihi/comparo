"""Shared exact-name, occurrence, and projection behavior for HTTP fields."""

from __future__ import annotations

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HeaderValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.http_fields import HttpFieldProjection
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.match_accounting import charge_work

_ACCESS = SensitiveValueAccess(purpose="selector.http.field")

type ProjectedHeaderValue = (
    HttpFieldTextValue
    | HttpFieldItems
    | SensitiveValue[HttpFieldTextValue]
    | SensitiveValue[HttpFieldItems]
)


def field_selected(
    path: SelectorPath,
    *,
    name: str,
    occurrence: int,
) -> bool:
    """Return whether one source-ordered field satisfies the compiled field path."""
    if type(path) is NoPath:
        return True
    if type(path) is not HttpFieldPath or path.name != name:
        return False
    return path.occurrence is None or path.occurrence == Count(value=occurrence)


def project_header_value(
    value: HeaderValue,
    path: SelectorPath,
    meter: SelectionMeter,
) -> ProjectedHeaderValue | SelectionObservedInvalid | SelectionBudgetExceeded:
    """Apply the explicit raw or conservative token-list header projection."""
    if type(path) is not HttpFieldPath or path.projection is HttpFieldProjection.RAW_VALUE:
        return _raw_header_value(value)
    raw = value if type(value) is HeaderText else value.reveal(access=_ACCESS)
    exhausted = charge_work(meter, max(1, len(raw.value)))
    if exhausted is not None:
        return exhausted
    items = _token_items(raw.value)
    if items is None:
        return SelectionObservedInvalid(code=DiagnosticCode(value="selection.header.items.invalid"))
    projected = HttpFieldItems(items=items)
    if type(value) is HeaderText:
        return projected
    return SensitiveValue(projected, origins=value.origins)


def _raw_header_value(
    value: HeaderValue,
) -> HttpFieldTextValue | SensitiveValue[HttpFieldTextValue]:
    if type(value) is HeaderText:
        return HttpFieldTextValue(value=value.value)
    payload = value.reveal(access=_ACCESS)
    return SensitiveValue(HttpFieldTextValue(value=payload.value), origins=value.origins)


def _token_items(value: str) -> tuple[MediaTypeTokenValue, ...] | None:
    """Parse the supported comma-separated token-list subset without normalization."""
    candidates = tuple(item.strip(" \t") for item in value.split(","))
    if not candidates or any(not item for item in candidates):
        return None
    try:
        return tuple(MediaTypeTokenValue(value=item) for item in candidates)
    except ValueError:
        return None


__all__ = ("ProjectedHeaderValue", "field_selected", "project_header_value")
