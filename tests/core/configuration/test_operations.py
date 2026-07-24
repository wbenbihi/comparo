"""Tests for the closed v1alpha1 operation-name registry."""

from comparo.core.configuration.v1alpha1.operations import ASSERTION_OPERATION_ALIASES
from comparo.core.configuration.v1alpha1.operations import supports_operation


def test_operation_registry_accepts_aliases_and_exact_builtin_identities_only() -> None:
    assert supports_operation("equals", ASSERTION_OPERATION_ALIASES)
    assert supports_operation("comparo.core/equals@1", ASSERTION_OPERATION_ALIASES)
    assert not supports_operation(1, ASSERTION_OPERATION_ALIASES)
    assert not supports_operation("third.party/equals@1", ASSERTION_OPERATION_ALIASES)
    assert not supports_operation("comparo.core/equals@2", ASSERTION_OPERATION_ALIASES)
    assert not supports_operation("comparo.core/unknown@1", ASSERTION_OPERATION_ALIASES)
