"""Closed scalar vocabularies used by compiled value expressions."""

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.value_kinds import ValueKind

_JSON_OUTPUT_KINDS = frozenset(
    {
        ValueKind.NULL,
        ValueKind.BOOLEAN,
        ValueKind.INTEGER,
        ValueKind.NUMBER,
        ValueKind.STRING,
        ValueKind.ARRAY,
        ValueKind.OBJECT,
    }
)


class ConversionType(DomainStrEnum):
    """The strict, explicit value-conversion vocabulary."""

    STRING = "string"
    BYTES = "bytes"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"
    YAML = "yaml"
    DURATION = "duration"
    SIZE = "size"


class FileTrim(DomainStrEnum):
    """Explicit text trimming behavior for file conversion."""

    NONE = "none"
    START = "start"
    END = "end"
    BOTH = "both"


class InterpolationMode(DomainStrEnum):
    """Whether interpolation retains a native value or constructs text."""

    WHOLE_VALUE = "whole-value"
    EMBEDDED_TEXT = "embedded-text"


def conversion_output_kinds(conversion: ConversionType) -> frozenset[ValueKind]:
    """Return the exact static output-kind contract of a conversion."""
    if conversion is ConversionType.STRING:
        return frozenset({ValueKind.STRING})
    if conversion is ConversionType.BYTES:
        return frozenset({ValueKind.BYTES})
    if conversion is ConversionType.INTEGER:
        return frozenset({ValueKind.INTEGER})
    if conversion is ConversionType.NUMBER:
        return frozenset({ValueKind.NUMBER})
    if conversion is ConversionType.BOOLEAN:
        return frozenset({ValueKind.BOOLEAN})
    if conversion in {ConversionType.JSON, ConversionType.YAML}:
        return _JSON_OUTPUT_KINDS
    if conversion is ConversionType.DURATION:
        return frozenset({ValueKind.DURATION})
    if conversion is ConversionType.SIZE:
        return frozenset({ValueKind.SIZE})
    raise TypeError(  # pragma: no cover - callers require ConversionType
        "unsupported conversion type"
    )
