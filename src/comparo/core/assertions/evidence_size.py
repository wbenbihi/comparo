"""Sensitivity-safe structural sizing for retained assertion evidence."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

from comparo.core.domain.assertions.evidence import ApproximateEvidence
from comparo.core.domain.assertions.evidence import DigestEvidence
from comparo.core.domain.assertions.evidence import SchemaValidationEvidence
from comparo.core.domain.assertions.results import MatchFact
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.locations import AttemptLocation
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import ExecutionLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import IndexedLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.locations import ScenarioLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import is_selected_value
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value

_ACCESS = SensitiveValueAccess(purpose="assertion.evidence.measure")
_STRUCTURAL_FIELD_BYTES = 8
_UUID_BYTES = 16


def evidence_fact_size(fact: MatchFact) -> int:
    """Measure the canonical structural evidence footprint without serialization."""
    if type(fact) is not MatchFact:
        raise TypeError("evidence sizing requires a MatchFact")
    diagnostic = 0 if fact.diagnostic is None else _text_size(fact.diagnostic.value)
    return (
        _STRUCTURAL_FIELD_BYTES
        + _UUID_BYTES
        + selected_value_size(fact.match.value)
        + location_size(fact.match.location)
        + _text_size(fact.outcome.value)
        + diagnostic
        + _operator_evidence_size(fact.operator_evidence)
    )


def _operator_evidence_size(value: object | None) -> int:
    if value is None:
        return 0
    lineage = 0
    if is_sensitive_value(value):
        lineage = sum(_text_size(origin.value) for origin in value.origins)
        value = value.reveal(access=_ACCESS)
    if type(value) is ApproximateEvidence:
        return lineage + _ratio_size(value.delta) + _ratio_size(value.effective_tolerance)
    if type(value) is DigestEvidence:
        return lineage + _text_size(value.algorithm.value) + selected_value_size(value.actual)
    if type(value) is SchemaValidationEvidence:
        return lineage + (
            _integer_size(value.violation_count.value)
            + _integer_size(value.omitted.value)
            + sum(
                _text_size(item.instance.value) + _text_size(item.schema.value)
                for item in value.retained
            )
        )
    raise TypeError("unsupported assertion operator evidence")


def _ratio_size(value: Ratio) -> int:
    return _integer_size(value.numerator) + 1 + _integer_size(value.denominator.value)


def selected_value_size(value: object) -> int:
    """Measure a sealed selected value without rendering or persisting its payload."""
    lineage = 0
    if is_sensitive_value(value):
        lineage = sum(_text_size(origin.value) for origin in value.origins)
        value = value.reveal(access=_ACCESS)
    if not is_selected_value(value):
        raise TypeError("evidence value must be a closed SelectedValue")
    return lineage + _plain_selected_value_size(value)


def _plain_selected_value_size(value: SelectedValue) -> int:
    if is_json_value(value):
        return _json_size(value)
    if type(value) is BytesValue:
        return len(value.value)
    http_size = _http_value_size(value)
    if http_size is not None:
        return http_size
    return _scalar_value_size(value)


def _http_value_size(value: SelectedValue) -> int | None:
    if type(value) is HttpFieldTextValue:
        return _text_size(value.value)
    if type(value) is BareQueryFieldValue:
        return 0
    if type(value) is HttpFieldItems:
        return max(0, len(value.items) - 1) + sum(_text_size(item.value) for item in value.items)
    return None


def _scalar_value_size(value: SelectedValue) -> int:
    if type(value) is Count:
        return _integer_size(value.value)
    if type(value) is ByteCount:
        return _integer_size(value.value)
    if type(value) is Duration:
        return _integer_size(value.nanoseconds)
    if type(value) is Ratio:
        return _integer_size(value.numerator) + 1 + _integer_size(value.denominator.value)
    if type(value) is UtcInstant:
        return _integer_size(value.unix_nanoseconds)
    if type(value) in {BodyId, ViewId}:
        return _UUID_BYTES
    if type(value) is MediaType:
        return _media_type_size(value)
    raise TypeError("unsupported selected value")  # pragma: no cover


def _json_size(root: JsonValue) -> int:
    total = 0
    stack = [root]
    while stack:
        value = stack.pop()
        if type(value) is JsonNull:
            total += 4
        elif type(value) is JsonBoolean:
            total += 4 if value.value else 5
        elif type(value) is JsonInteger:
            total += _integer_size(value.value)
        elif type(value) is JsonDecimal:
            total += _decimal_size(value.value)
        elif type(value) is JsonString:
            total += _json_string_size(value.value)
        elif type(value) is JsonArray:
            total += 2 + max(0, len(value.items) - 1)
            stack.extend(reversed(value.items))
        elif type(value) is JsonObject:
            total += 2 + max(0, len(value.members) - 1)
            for member in reversed(value.members):
                total += _json_string_size(member.name) + 1
                stack.append(member.value)
        else:  # pragma: no cover - closed JsonValue union
            raise TypeError("unsupported JSON value")
    return total


def location_size(location: ConcreteLocation) -> int:  # noqa: C901
    """Measure exact typed coordinates without generic reflection."""
    base = _UUID_BYTES + _text_size(location.source.value)
    if type(location) is ExecutionLocation:
        return base + _field_size(location.field.segments)
    if type(location) is AttemptLocation:
        return base + _UUID_BYTES + _field_size(location.field.segments)
    if type(location) is ExchangeLocation:
        return base + _UUID_BYTES + _field_size(location.field.segments)
    if type(location) is ScenarioLocation:
        return base + _UUID_BYTES + _field_size(location.field.segments)
    if type(location) is HeaderLocation:
        optional = 0
        if location.informational_response is not None:
            optional += _integer_size(location.informational_response.value)
        if location.informational_status is not None:
            optional += _integer_size(location.informational_status)
        return (
            base
            + _UUID_BYTES
            + _text_size(location.section.value)
            + _integer_size(location.occurrence.value)
            + _text_size(location.name.value)
            + optional
        )
    if type(location) is NamedFieldLocation:
        return (
            base
            + _UUID_BYTES
            + _text_size(location.name.value)
            + _integer_size(location.occurrence.value)
        )
    if type(location) is IndexedLocation:
        return base + _UUID_BYTES + _integer_size(location.index.value)
    if type(location) is JsonLocation:
        body_view = 0 if location.body_view is None else _text_size(location.body_view.value)
        return base + _UUID_BYTES + _text_size(location.pointer.value) + body_view
    if type(location) is ViewLocation:
        return base + _UUID_BYTES + _text_size(location.body_view.value)
    if type(location) is BodyLocation:
        bounds = 0
        if location.start is not None and location.end is not None:
            bounds = _integer_size(location.start.value) + _integer_size(location.end.value)
        return base + _UUID_BYTES + _text_size(location.body_view.value) + bounds
    if type(location) is StreamLocation:
        nested = 0 if location.nested_pointer is None else _text_size(location.nested_pointer.value)
        return (
            base
            + _UUID_BYTES
            + _integer_size(location.connection_segment.value)
            + _integer_size(location.item_index.value)
            + _integer_size(location.category_index.value)
            + nested
        )
    raise TypeError("unsupported concrete location")  # pragma: no cover


def _field_size(segments: tuple[str, ...]) -> int:
    return max(0, len(segments) - 1) + sum(_text_size(segment) for segment in segments)


def _media_type_size(value: MediaType) -> int:
    total = _text_size(value.type) + 1 + _text_size(value.subtype)
    for parameter in value.parameters:
        item = parameter.value.value
        item_size = _text_size(item)
        if type(parameter.value) is MediaTypeQuotedValue:
            item_size += 2 + sum(character in {'"', "\\"} for character in item)
        total += 3 + _text_size(parameter.name) + item_size
    return total


def _decimal_size(value: Decimal) -> int:
    sign, digits, exponent = value.as_tuple()
    exact_exponent = cast("int", exponent)
    point = len(digits) + exact_exponent
    if point > 0:
        return int(bool(sign)) + len(digits) + 1
    return int(bool(sign)) + 2 + (-point) + len(digits)


def _integer_size(value: int) -> int:
    remaining = abs(value)
    digits = 1
    while remaining >= 10:
        remaining //= 10
        digits += 1
    return digits + int(value < 0)


def _json_string_size(value: str) -> int:
    size = 2
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\"} or character in "\b\f\n\r\t":
            size += 2
        elif codepoint < 0x20 or 0xD800 <= codepoint <= 0xDFFF:
            size += 6
        else:
            size += len(character.encode("utf-8"))
    return size


def _text_size(value: str) -> int:
    return sum(len(character.encode("utf-8")) for character in value)
