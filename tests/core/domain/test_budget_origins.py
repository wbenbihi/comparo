"""Tests for exact, value-free budget-origin identities."""

from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.budget_origins import DefinitionBudgetOrigin
from comparo.core.domain.budget_origins import InvocationBudgetOrigin
from comparo.core.domain.budget_origins import VersionedDefaultSetRef
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from tests.core.domain.budget_origin_factories import default_budget_origin
from tests.core.domain.budget_origin_factories import definition_budget_origin
from tests.core.domain.budget_origin_factories import invocation_budget_origin


def test_versioned_default_set_has_stable_version_and_content_identity() -> None:
    default_set = default_budget_origin().default_set
    assert default_set.identifier == "comparo.core-defaults"
    assert default_set.version == "1"
    assert type(default_set.fingerprint) is Fingerprint


@pytest.mark.parametrize(
    "identifier",
    ["", "Comparo.defaults", "comparo_defaults", ".defaults", "comparo..defaults", 1],
)
def test_default_set_identifier_rejects_ambiguous_values(identifier: object) -> None:
    with pytest.raises(ValueError, match="lowercase namespaced token"):
        VersionedDefaultSetRef(
            identifier=identifier,  # type: ignore[arg-type]
            version="1",
            fingerprint=default_budget_origin().default_set.fingerprint,
        )


@pytest.mark.parametrize(
    "version",
    ["", "0", "01", "v1", "\N{ARABIC-INDIC DIGIT ONE}", 1],
)
def test_default_set_version_requires_canonical_positive_ascii_decimal(
    version: object,
) -> None:
    with pytest.raises(ValueError, match="canonical positive decimal"):
        VersionedDefaultSetRef(
            identifier="comparo.defaults",
            version=version,  # type: ignore[arg-type]
            fingerprint=default_budget_origin().default_set.fingerprint,
        )


def test_default_set_requires_a_typed_content_fingerprint() -> None:
    with pytest.raises(TypeError, match="Fingerprint"):
        VersionedDefaultSetRef(
            identifier="comparo.defaults",
            version="1",
            fingerprint="0" * 64,  # type: ignore[arg-type]
        )


def test_origin_variants_require_exact_typed_identities() -> None:
    definition = definition_budget_origin(ResourceKind.PROJECT).definition
    invocation = invocation_budget_origin().invocation_id
    default_set = default_budget_origin().default_set
    assert DefaultBudgetOrigin(default_set=default_set).default_set is default_set
    assert DefinitionBudgetOrigin(definition=definition).definition is definition
    assert InvocationBudgetOrigin(invocation_id=invocation).invocation_id is invocation

    with pytest.raises(TypeError, match="VersionedDefaultSetRef"):
        DefaultBudgetOrigin(default_set=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DefinitionRef"):
        DefinitionBudgetOrigin(definition=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="InvocationId"):
        InvocationBudgetOrigin(invocation_id=object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kind",
    [
        ResourceKind.PROJECT,
        ResourceKind.EXECUTION_PROFILE,
        ResourceKind.ASSERTION_PROFILE,
        ResourceKind.DIFF_PROFILE,
        ResourceKind.GATE_PROFILE,
        ResourceKind.REDACTION_PROFILE,
        ResourceKind.ARTIFACT_PROFILE,
    ],
)
def test_ordinary_budget_requests_accept_exact_project_and_profile_origins(
    kind: ResourceKind,
) -> None:
    contribution = BudgetContribution(
        kind=BudgetContributionKind.REQUEST,
        amount=Count(value=1),
        origin=definition_budget_origin(kind),
    )
    assert isinstance(contribution.origin, DefinitionBudgetOrigin)
    definition = cast("DefinitionRef", contribution.origin.definition)
    assert definition.identity.kind is kind


def test_ordinary_budget_requests_accept_an_exact_invocation_origin() -> None:
    invocation_id = UUID("10000000-0000-0000-0000-000000000001")
    contribution = BudgetContribution(
        kind=BudgetContributionKind.REQUEST,
        amount=Count(value=1),
        origin=invocation_budget_origin(invocation_id),
    )
    assert contribution.origin == InvocationBudgetOrigin(
        invocation_id=InvocationId(value=invocation_id)
    )


@pytest.mark.parametrize(
    "kind",
    [ResourceKind.SAFETY_PROFILE, ResourceKind.EXECUTION_POLICY],
)
def test_budget_ceilings_accept_only_exact_operator_authority_definitions(
    kind: ResourceKind,
) -> None:
    contribution = BudgetContribution(
        kind=BudgetContributionKind.CEILING,
        amount=Count(value=1),
        origin=definition_budget_origin(kind),
    )
    assert isinstance(contribution.origin, DefinitionBudgetOrigin)
    definition = cast("DefinitionRef", contribution.origin.definition)
    assert definition.identity.kind is kind


def test_budget_contribution_roles_reject_inexact_or_wrong_authorities() -> None:
    with pytest.raises(ValueError, match="versioned default-set"):
        BudgetContribution(
            kind=BudgetContributionKind.DEFAULT,
            amount=Count(value=1),
            origin=invocation_budget_origin(),
        )
    for origin in (
        default_budget_origin(),
        invocation_budget_origin(),
        definition_budget_origin(ResourceKind.PROJECT),
    ):
        with pytest.raises(ValueError, match="SafetyProfile or ExecutionPolicy"):
            BudgetContribution(
                kind=BudgetContributionKind.CEILING,
                amount=Count(value=1),
                origin=origin,
            )
    for origin in (
        default_budget_origin(),
        definition_budget_origin(ResourceKind.SAFETY_PROFILE),
        definition_budget_origin(ResourceKind.REQUEST),
    ):
        with pytest.raises(ValueError, match="Project, profile, or invocation"):
            BudgetContribution(
                kind=BudgetContributionKind.REQUEST,
                amount=Count(value=1),
                origin=origin,
            )


def test_budget_origins_do_not_admit_payload_bearing_legacy_provenance() -> None:
    assert DefinitionBudgetOrigin.__struct_fields__ == ("definition",)
    assert InvocationBudgetOrigin.__struct_fields__ == ("invocation_id",)
    assert DefaultBudgetOrigin.__struct_fields__ == ("default_set",)
    assert VersionedDefaultSetRef.__struct_fields__ == (
        "identifier",
        "version",
        "fingerprint",
    )
    assert DefinitionRef.__struct_fields__ == ("identity", "fingerprint")
