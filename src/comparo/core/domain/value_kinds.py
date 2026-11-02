"""Cycle-free static value categories shared by domain expression consumers."""

from comparo.core.domain.enums import DomainStrEnum


class ValueKind(DomainStrEnum):
    """Exact runtime value categories visible to static evaluation planning."""

    UNKNOWN = "unknown"
    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    ARRAY = "array"
    OBJECT = "object"
    BYTES = "bytes"
    DURATION = "duration"
    SIZE = "size"
    INSTANT = "instant"
    COUNT = "count"
    RATIO = "ratio"
    BODY_HANDLE = "body-handle"
    VIEW_HANDLE = "view-handle"
    MEDIA_TYPE = "media-type"
    HTTP_FIELD_VALUE = "http-field-value"
    HTTP_FIELD_ITEMS = "http-field-items"
