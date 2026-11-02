"""Translate caller-owned CLI configuration limits to the local loader contract."""

from __future__ import annotations

from comparo.configuration import YamlParseLimits
from comparo.configuration.loader import ProjectLoadLimits
from comparo.policy import PolicyLoadLimits

from .contracts import CliConfigLoadLimits


def project_load_limits(value: CliConfigLoadLimits) -> ProjectLoadLimits:
    """Build one validated loader policy without opening any project source."""
    if type(value) is not CliConfigLoadLimits:
        raise TypeError("configuration load limits require CliConfigLoadLimits")
    return ProjectLoadLimits(
        maximum_sources=value.maximum_sources,
        maximum_discovery_entries=value.maximum_discovery_entries,
        maximum_total_bytes=value.maximum_total_bytes,
        maximum_total_documents=value.maximum_total_documents,
        maximum_total_nodes=value.maximum_total_nodes,
        maximum_resources=value.maximum_resources,
        yaml=YamlParseLimits(
            maximum_bytes=value.maximum_yaml_bytes,
            maximum_documents=value.maximum_yaml_documents,
            maximum_depth=value.maximum_yaml_depth,
            maximum_nodes=value.maximum_yaml_nodes,
        ),
    )


def policy_load_limits(value: CliConfigLoadLimits) -> PolicyLoadLimits:
    """Build one validated explicit-policy loader contract without reading its path."""
    if type(value) is not CliConfigLoadLimits:
        raise TypeError("policy load limits require CliConfigLoadLimits")
    return PolicyLoadLimits(
        yaml=YamlParseLimits(
            maximum_bytes=value.maximum_policy_yaml_bytes,
            maximum_documents=1,
            maximum_depth=value.maximum_policy_yaml_depth,
            maximum_nodes=value.maximum_policy_yaml_nodes,
        )
    )


__all__ = ("policy_load_limits", "project_load_limits")
