"""Profile-field runtime support is complete, shared, and fail-closed."""

from comparo.application.profile_capabilities import ARTIFACT_PROFILE_ADMITTED_FIELDS
from comparo.application.profile_capabilities import REDACTION_PROFILE_ADMITTED_FIELDS
from comparo.application.profile_capabilities import SAFETY_PROFILE_ADMITTED_FIELDS
from comparo.application.profile_capabilities import ProfileFieldSupport
from comparo.application.profile_capabilities import profile_resource_capabilities
from comparo.core.configuration.v1alpha1.schema import schema_for
from comparo.core.domain.identity import ResourceKind


def test_data_profile_capabilities_classify_every_schema_field() -> None:
    for kind in (
        ResourceKind.SAFETY_PROFILE,
        ResourceKind.REDACTION_PROFILE,
        ResourceKind.ARTIFACT_PROFILE,
    ):
        capabilities = profile_resource_capabilities(kind)
        schema_fields = frozenset(field.name for field in schema_for(kind).fields)
        assert capabilities.all_fields == schema_fields


def test_profile_binder_allowlists_are_derived_from_capability_contract() -> None:
    safety = profile_resource_capabilities(ResourceKind.SAFETY_PROFILE)
    redaction = profile_resource_capabilities(ResourceKind.REDACTION_PROFILE)
    artifact = profile_resource_capabilities(ResourceKind.ARTIFACT_PROFILE)

    assert safety.admitted_fields == SAFETY_PROFILE_ADMITTED_FIELDS
    assert redaction.admitted_fields == REDACTION_PROFILE_ADMITTED_FIELDS
    assert artifact.admitted_fields == ARTIFACT_PROFILE_ADMITTED_FIELDS
    assert safety.support("network") is ProfileFieldSupport.UNSUPPORTED
    assert safety.support("budgets") is ProfileFieldSupport.ACTIVE
    assert redaction.support("patterns") is ProfileFieldSupport.UNSUPPORTED
    assert redaction.support("backstop") is ProfileFieldSupport.RESERVED
    assert artifact.support("retention") is ProfileFieldSupport.RESERVED
