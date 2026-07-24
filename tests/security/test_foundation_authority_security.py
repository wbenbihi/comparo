"""Security regressions for exact authority and sensitive-value boundaries."""

from enum import StrEnum

import msgspec
import pytest
from tests.core.domain.analysis_factories import _capability
from tests.core.domain.analysis_factories import _destination

from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.capabilities import CapabilityGranted
from comparo.core.domain.primitives import Count
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId


class _AttackerControlledEnum(StrEnum):
    """Enum that looks scalar but can carry arbitrary instance state."""

    TOKEN = "public-looking"


class _FrozenStructWithDict(msgspec.Struct, frozen=True, dict=True):
    """Frozen declared fields plus mutable undeclared instance state."""

    value: str


def test_arbitrary_string_enum_cannot_smuggle_mutable_state_into_sensitive_value() -> None:
    token = _AttackerControlledEnum.TOKEN
    token.mutable_cache = ["do-not-leak"]  # type: ignore[attr-defined]
    with pytest.raises(TypeError, match="deeply immutable") as caught:
        SensitiveValue(
            token,
            origins=(TaintOriginId(value="secret/project/token"),),
        )
    assert "do-not-leak" not in str(caught.value)


def test_frozen_struct_with_mutable_instance_dictionary_cannot_be_sealed() -> None:
    payload = _FrozenStructWithDict(value="public-looking")
    payload.__dict__["cache"] = ["do-not-leak"]
    with pytest.raises(TypeError, match="deeply immutable") as caught:
        SensitiveValue(
            payload,
            origins=(TaintOriginId(value="secret/project/token"),),
        )
    assert "do-not-leak" not in str(caught.value)


def test_legacy_provenance_cannot_supply_budget_authority() -> None:
    with pytest.raises(TypeError, match="exact BudgetOrigin"):
        BudgetContribution(
            kind=BudgetContributionKind.CEILING,
            amount=Count(value=1),
            origin=ProvenanceEntry(  # type: ignore[arg-type]
                kind=ProvenanceKind.EXECUTION_POLICY
            ),
        )


def test_legacy_provenance_cannot_supply_final_capability_authority() -> None:
    with pytest.raises(TypeError, match="exact ExecutionPolicy"):
        CapabilityGranted(
            request=_capability(target=_destination()),
            authority=(  # type: ignore[arg-type]
                ProvenanceEntry(kind=ProvenanceKind.EXECUTION_POLICY),
            ),
        )
