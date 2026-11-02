"""Truthful runtime support contracts for schema-admitted profile fields."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import ResourceKind


class ProfileFieldSupport(DomainStrEnum):
    """How the current runtime treats one schema-admitted profile field."""

    ACTIVE = "active"
    RESERVED = "reserved"
    UNSUPPORTED = "unsupported"


@final
class ProfileResourceCapabilities(msgspec.Struct, frozen=True, kw_only=True):
    """Closed, value-free support map for one profile resource kind."""

    kind: ResourceKind
    active_fields: tuple[str, ...] = ()
    reserved_fields: tuple[str, ...] = ()
    unsupported_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require complete disjoint structural field names."""
        if type(self.kind) is not ResourceKind:
            raise TypeError("profile capability kind must be a ResourceKind")
        groups = (self.active_fields, self.reserved_fields, self.unsupported_fields)
        if any(
            type(group) is not tuple
            or any(
                type(field) is not str or not field or len(field) > 64 or not field.isprintable()
                for field in group
            )
            or len(group) != len(set(group))
            for group in groups
        ):
            raise ValueError("profile capability fields must be unique bounded text")
        flattened = (*self.active_fields, *self.reserved_fields, *self.unsupported_fields)
        if len(flattened) != len(set(flattened)):
            raise ValueError("profile capability support groups must be disjoint")

    def support(self, field: str) -> ProfileFieldSupport:
        """Return the runtime status of one declared field, failing closed for unknowns."""
        if field in self.active_fields:
            return ProfileFieldSupport.ACTIVE
        if field in self.reserved_fields:
            return ProfileFieldSupport.RESERVED
        return ProfileFieldSupport.UNSUPPORTED

    @property
    def admitted_fields(self) -> frozenset[str]:
        """Fields accepted by current binders, including validated reserved placeholders."""
        return frozenset((*self.active_fields, *self.reserved_fields))

    @property
    def all_fields(self) -> frozenset[str]:
        """Every schema-admitted field classified by this runtime contract."""
        return frozenset((*self.active_fields, *self.reserved_fields, *self.unsupported_fields))


_CAPABILITIES = {
    ResourceKind.SAFETY_PROFILE: ProfileResourceCapabilities(
        kind=ResourceKind.SAFETY_PROFILE,
        active_fields=("include", "extends", "budgets", "classification", "schemas"),
        unsupported_fields=("network", "filesystem", "environment"),
    ),
    ResourceKind.REDACTION_PROFILE: ProfileResourceCapabilities(
        kind=ResourceKind.REDACTION_PROFILE,
        active_fields=("rules",),
        reserved_fields=("backstop",),
        unsupported_fields=("patterns",),
    ),
    ResourceKind.ARTIFACT_PROFILE: ProfileResourceCapabilities(
        kind=ResourceKind.ARTIFACT_PROFILE,
        active_fields=("capture", "storage"),
        reserved_fields=("retention",),
    ),
}


def profile_resource_capabilities(kind: ResourceKind) -> ProfileResourceCapabilities:
    """Return the current runtime contract for one profile kind."""
    if type(kind) is not ResourceKind:
        raise TypeError("profile capability lookup requires a ResourceKind")
    return _CAPABILITIES.get(kind, ProfileResourceCapabilities(kind=kind))


SAFETY_PROFILE_ADMITTED_FIELDS = profile_resource_capabilities(
    ResourceKind.SAFETY_PROFILE
).admitted_fields
REDACTION_PROFILE_ADMITTED_FIELDS = profile_resource_capabilities(
    ResourceKind.REDACTION_PROFILE
).admitted_fields
ARTIFACT_PROFILE_ADMITTED_FIELDS = profile_resource_capabilities(
    ResourceKind.ARTIFACT_PROFILE
).admitted_fields


__all__ = (
    "ARTIFACT_PROFILE_ADMITTED_FIELDS",
    "REDACTION_PROFILE_ADMITTED_FIELDS",
    "SAFETY_PROFILE_ADMITTED_FIELDS",
    "ProfileFieldSupport",
    "ProfileResourceCapabilities",
    "profile_resource_capabilities",
)
