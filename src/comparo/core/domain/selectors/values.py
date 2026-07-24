"""Closed runtime value carriers and kind mapping for selector results."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import is_json_value


@final
class HttpFieldTextValue(msgspec.Struct, frozen=True, kw_only=True):
    """One exact raw HTTP query, header, cookie, or derived-field text value."""

    value: str

    def __post_init__(self) -> None:
        """Require text composed only of valid Unicode scalar values."""
        if type(self.value) is not str:
            raise TypeError("raw HTTP field value must be text")
        try:
            self.value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError(
                "raw HTTP field value must contain valid Unicode scalar values"
            ) from error


@final
class BareQueryFieldValue(msgspec.Struct, frozen=True, kw_only=True):
    """An exact bare query occurrence, distinct from an empty text value."""


@final
class HttpFieldItems(msgspec.Struct, frozen=True, kw_only=True):
    """One parsed HTTP field value represented as an immutable token list."""

    items: tuple[MediaTypeTokenValue, ...]

    def __post_init__(self) -> None:
        """Require an immutable sequence of exact HTTP tokens."""
        if type(self.items) is not tuple:
            raise TypeError("HTTP field items must be an immutable tuple")
        if any(type(item) is not MediaTypeTokenValue for item in self.items):
            raise TypeError("HTTP field items must contain MediaTypeTokenValue values")


type SelectedValue = (
    ExactValue
    | Duration
    | ByteCount
    | Count
    | Ratio
    | UtcInstant
    | BodyId
    | ViewId
    | MediaType
    | HttpFieldTextValue
    | BareQueryFieldValue
    | HttpFieldItems
)

_EXTRA_VALUE_KINDS: tuple[tuple[type[object], ValueKind], ...] = (
    (BytesValue, ValueKind.BYTES),
    (Duration, ValueKind.DURATION),
    (ByteCount, ValueKind.SIZE),
    (UtcInstant, ValueKind.INSTANT),
    (Count, ValueKind.COUNT),
    (Ratio, ValueKind.RATIO),
    (BodyId, ValueKind.BODY_HANDLE),
    (ViewId, ValueKind.VIEW_HANDLE),
    (MediaType, ValueKind.MEDIA_TYPE),
    (HttpFieldTextValue, ValueKind.HTTP_FIELD_VALUE),
    (BareQueryFieldValue, ValueKind.HTTP_FIELD_VALUE),
    (HttpFieldItems, ValueKind.HTTP_FIELD_ITEMS),
)

_JSON_VALUE_KINDS: tuple[tuple[type[object], ValueKind], ...] = (
    (JsonNull, ValueKind.NULL),
    (JsonBoolean, ValueKind.BOOLEAN),
    (JsonInteger, ValueKind.INTEGER),
    (JsonDecimal, ValueKind.NUMBER),
    (JsonString, ValueKind.STRING),
    (JsonArray, ValueKind.ARRAY),
    (JsonObject, ValueKind.OBJECT),
)


def is_selected_value(value: object) -> TypeGuard[SelectedValue]:
    """Narrow a value to the closed selector-value union."""
    return is_json_value(value) or selected_value_kind_type(type(value)) is not None


def is_selected_value_type(value_type: type[object]) -> bool:
    """Validate a sealed sensitive wrapper using only its non-secret payload type."""
    return selected_value_kind_type(value_type) is not None


def selected_value_kind(value: SelectedValue) -> ValueKind:
    """Return the exact static kind represented by one closed runtime carrier."""
    kind = selected_value_kind_type(type(value))
    if kind is None:  # pragma: no cover - narrowed by SelectedValue construction
        raise TypeError("selected value has no runtime kind mapping")
    return kind


def selected_value_kind_type(value_type: type[object]) -> ValueKind | None:
    """Return a kind from a sealed payload type without revealing a sensitive value."""
    return next(
        (
            kind
            for carrier, kind in (*_JSON_VALUE_KINDS, *_EXTRA_VALUE_KINDS)
            if carrier is value_type
        ),
        None,
    )
