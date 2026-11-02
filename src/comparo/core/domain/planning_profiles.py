"""Typed profile-selection inputs supplied to deterministic run planning."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.identity import ResourceId
from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import ScalarOverride
from comparo.core.domain.overrides import SetValue


def _default_profile_collection() -> CollectionOverridePlan[ResourceId, ResourceId]:
    return CollectionOverridePlan(base=KeepBase())


@final
class InvocationProfileOverrides(msgspec.Struct, frozen=True, kw_only=True):
    """Typed profile edits shared across every selected invocation subject."""

    assertions: CollectionOverridePlan[ResourceId, ResourceId] = _default_profile_collection()
    diffs: CollectionOverridePlan[ResourceId, ResourceId] = _default_profile_collection()
    gate: ScalarOverride[ResourceId] = Inherit()
    safety: CollectionOverridePlan[ResourceId, ResourceId] = _default_profile_collection()
    redaction: ScalarOverride[ResourceId] = Inherit()
    artifacts: ScalarOverride[ResourceId] = Inherit()

    def __post_init__(self) -> None:
        """Require field-specific collection and scalar override operations."""
        for label, edit in (
            ("assertion profile", self.assertions),
            ("diff profile", self.diffs),
            ("safety profile", self.safety),
        ):
            _validate_resource_edit(edit, label=label)
        for label, scalar_edit in (
            ("gate profile", self.gate),
            ("redaction profile", self.redaction),
            ("artifact profile", self.artifacts),
        ):
            if type(scalar_edit) not in {Inherit, Clear} and not (
                type(scalar_edit) is SetValue and type(scalar_edit.value) is ResourceId
            ):
                raise TypeError(f"{label} override must inherit, clear, or set a ResourceId")


def _validate_resource_edit(edit: object, *, label: str) -> None:
    if type(edit) is not CollectionOverridePlan:
        raise TypeError(f"{label} selection must be a CollectionOverridePlan")
    base_items = edit.base.items if type(edit.base) is ReplaceBase else ()
    for item in (
        *base_items,
        *edit.remove,
        *(operation.identity for operation in edit.set_items),
        *edit.add,
    ):
        if type(item) is not ResourceId:
            raise TypeError(f"{label} selection identities must be ResourceId values")
    for operation in edit.set_items:
        if type(operation.item) is not ResourceId:
            raise TypeError(f"{label} set values must be ResourceId values")


__all__ = ("InvocationProfileOverrides",)
