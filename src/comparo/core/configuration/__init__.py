"""Pure, versioned configuration compilation primitives."""

from __future__ import annotations

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationParseResult
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import SourceSpan
from comparo.core.configuration.yaml_parser import parse_yaml_source

__all__ = (
    "ConfigurationDiagnostic",
    "ConfigurationParseResult",
    "ParsedDocument",
    "SourceSpan",
    "parse_yaml_source",
)
