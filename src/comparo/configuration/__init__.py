"""Filesystem adapters for loading and compiling Comparo projects."""

from __future__ import annotations

from comparo.configuration.loader import DEFAULT_PROJECT_LOAD_LIMITS
from comparo.configuration.loader import LoadedCompilation
from comparo.configuration.loader import ProjectLoadLimits
from comparo.configuration.loader import ProjectSourceSet
from comparo.configuration.loader import SourceCatalogEntry
from comparo.configuration.loader import compile_project_path
from comparo.configuration.loader import load_project_sources
from comparo.configuration.yaml_parser import DEFAULT_YAML_PARSE_LIMITS
from comparo.configuration.yaml_parser import YamlParseLimits
from comparo.configuration.yaml_parser import parse_yaml_source

__all__ = (
    "DEFAULT_PROJECT_LOAD_LIMITS",
    "DEFAULT_YAML_PARSE_LIMITS",
    "LoadedCompilation",
    "ProjectLoadLimits",
    "ProjectSourceSet",
    "SourceCatalogEntry",
    "YamlParseLimits",
    "compile_project_path",
    "load_project_sources",
    "parse_yaml_source",
)
