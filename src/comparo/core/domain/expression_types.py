"""Closed scalar vocabularies used by compiled value expressions."""

from comparo.core.domain.enums import DomainStrEnum


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
