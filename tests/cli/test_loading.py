"""Tests for the CLI-to-project-loader admission-limit boundary."""

from typing import cast

import msgspec
import pytest

from comparo.cli.contracts import DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1
from comparo.cli.contracts import CliConfigLoadLimits
from comparo.cli.loading import policy_load_limits
from comparo.cli.loading import project_load_limits


def test_callers_can_raise_and_lower_every_versioned_default() -> None:
    lower = msgspec.structs.replace(
        DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1,
        maximum_sources=2,
        maximum_discovery_entries=21,
        maximum_total_bytes=23,
        maximum_total_documents=29,
        maximum_total_nodes=31,
        maximum_resources=37,
        maximum_yaml_bytes=3,
        maximum_yaml_documents=5,
        maximum_yaml_depth=7,
        maximum_yaml_nodes=11,
        maximum_policy_yaml_bytes=13,
        maximum_policy_yaml_depth=17,
        maximum_policy_yaml_nodes=19,
    )
    higher = msgspec.structs.replace(
        DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1,
        maximum_sources=8192,
        maximum_discovery_entries=200_000,
        maximum_total_bytes=128 * 1024 * 1024,
        maximum_total_documents=8192,
        maximum_total_nodes=8_000_000,
        maximum_resources=8192,
        maximum_yaml_bytes=16 * 1024 * 1024,
        maximum_yaml_documents=256,
        maximum_yaml_depth=512,
        maximum_yaml_nodes=2_000_000,
        maximum_policy_yaml_bytes=2 * 1024 * 1024,
        maximum_policy_yaml_depth=256,
        maximum_policy_yaml_nodes=200_000,
    )

    lowered = project_load_limits(lower)
    raised = project_load_limits(higher)
    lowered_policy = policy_load_limits(lower)
    raised_policy = policy_load_limits(higher)

    assert (
        lowered.maximum_sources,
        lowered.maximum_discovery_entries,
        lowered.maximum_total_bytes,
        lowered.maximum_total_documents,
        lowered.maximum_total_nodes,
        lowered.maximum_resources,
        lowered.yaml.maximum_bytes,
        lowered.yaml.maximum_documents,
        lowered.yaml.maximum_depth,
        lowered.yaml.maximum_nodes,
    ) == (2, 21, 23, 29, 31, 37, 3, 5, 7, 11)
    assert (
        raised.maximum_sources,
        raised.maximum_discovery_entries,
        raised.maximum_total_bytes,
        raised.maximum_total_documents,
        raised.maximum_total_nodes,
        raised.maximum_resources,
        raised.yaml.maximum_bytes,
        raised.yaml.maximum_documents,
        raised.yaml.maximum_depth,
        raised.yaml.maximum_nodes,
    ) == (
        8192,
        200_000,
        128 * 1024 * 1024,
        8192,
        8_000_000,
        8192,
        16 * 1024 * 1024,
        256,
        512,
        2_000_000,
    )
    assert (
        lowered_policy.yaml.maximum_bytes,
        lowered_policy.yaml.maximum_documents,
        lowered_policy.yaml.maximum_depth,
        lowered_policy.yaml.maximum_nodes,
    ) == (13, 1, 17, 19)
    assert (
        raised_policy.yaml.maximum_bytes,
        raised_policy.yaml.maximum_documents,
        raised_policy.yaml.maximum_depth,
        raised_policy.yaml.maximum_nodes,
    ) == (2 * 1024 * 1024, 1, 256, 200_000)


def test_loader_translation_rejects_invalid_ranges_and_contract_types() -> None:
    invalid = msgspec.structs.replace(DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1, maximum_sources=0)
    with pytest.raises(ValueError, match="positive"):
        project_load_limits(invalid)
    with pytest.raises(TypeError, match="CliConfigLoadLimits"):
        project_load_limits(cast("CliConfigLoadLimits", object()))
    with pytest.raises(TypeError, match="CliConfigLoadLimits"):
        policy_load_limits(cast("CliConfigLoadLimits", object()))
    invalid_policy = msgspec.structs.replace(
        DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1,
        maximum_policy_yaml_nodes=0,
    )
    with pytest.raises(ValueError, match="positive"):
        policy_load_limits(invalid_policy)
