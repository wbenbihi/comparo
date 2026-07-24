"""Pure, versioned configuration compilation primitives."""

from __future__ import annotations

from comparo.core.configuration.canonical import CompilationResult
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.compiler import compile_v1alpha1
from comparo.core.configuration.migrations import MigrationResult
from comparo.core.configuration.migrations import classify_migration
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationParseResult
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import SourceSpan
from comparo.core.configuration.schema_generation import generate_v1alpha1_schema
from comparo.core.configuration.schema_generation import render_v1alpha1_schema

__all__ = (
    "CompilationResult",
    "CompiledProject",
    "ConfigurationDiagnostic",
    "ConfigurationParseResult",
    "MigrationResult",
    "ParsedDocument",
    "SourceSpan",
    "classify_migration",
    "compile_v1alpha1",
    "generate_v1alpha1_schema",
    "render_v1alpha1_schema",
)
