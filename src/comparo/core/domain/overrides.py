"""Typed override operations with deterministic, field-owned composition semantics."""

from __future__ import annotations

from typing import final

import msgspec


@final
class Inherit(msgspec.Struct, frozen=True, kw_only=True):
    """Leave the value supplied by less-specific configuration unchanged."""


@final
class Clear(msgspec.Struct, frozen=True, kw_only=True):
    """Explicitly remove a value instead of inheriting it."""


@final
class SetValue[ValueT](msgspec.Struct, frozen=True, kw_only=True):
    """Replace a scalar with an explicit value, including falsy values."""

    value: ValueT


type ScalarOverride[ValueT] = Inherit | Clear | SetValue[ValueT]


@final
class KeepBase(msgspec.Struct, frozen=True, kw_only=True):
    """Start a collection edit from the inherited collection."""


@final
class ClearBase(msgspec.Struct, frozen=True, kw_only=True):
    """Start a collection edit from an explicitly cleared collection."""


@final
class ReplaceBase[ItemT](msgspec.Struct, frozen=True, kw_only=True):
    """Start a collection edit from an explicit immutable replacement."""

    items: tuple[ItemT, ...]

    def __post_init__(self) -> None:
        """Reject a mutable replacement container."""
        if type(self.items) is not tuple:
            raise TypeError("replacement items must be an immutable tuple")


type CollectionBase[ItemT] = KeepBase | ClearBase | ReplaceBase[ItemT]


@final
class KeyedSet[IdentityT, ItemT](msgspec.Struct, frozen=True, kw_only=True):
    """Replace all inherited items with one identity by an explicit item."""

    identity: IdentityT
    item: ItemT


@final
class CollectionOverridePlan[IdentityT, ItemT](msgspec.Struct, frozen=True, kw_only=True):
    """A deterministic collection edit independent of authoring operation order.

    Consumers apply the operations in the fixed order: choose ``base``, remove identities,
    perform keyed sets, then append additions. The receiving field owns identity extraction and
    collision semantics; this object never performs a generic recursive merge.
    """

    base: CollectionBase[ItemT]
    remove: tuple[IdentityT, ...] = ()
    set_items: tuple[KeyedSet[IdentityT, ItemT], ...] = ()
    add: tuple[ItemT, ...] = ()

    def __post_init__(self) -> None:
        """Require immutable operation groups and unambiguous keyed identities."""
        if type(self.base) not in {KeepBase, ClearBase, ReplaceBase}:
            raise TypeError("collection override base must be a supported base operation")
        if type(self.remove) is not tuple:
            raise TypeError("removed identities must be an immutable tuple")
        if type(self.set_items) is not tuple:
            raise TypeError("set operations must be an immutable tuple")
        if type(self.add) is not tuple:
            raise TypeError("added items must be an immutable tuple")
        if any(type(operation) is not KeyedSet for operation in self.set_items):
            raise TypeError("set operations must contain KeyedSet values")
        if _contains_duplicate(self.remove):
            raise ValueError("removed identities must be unique")
        set_identities = tuple(operation.identity for operation in self.set_items)
        if _contains_duplicate(set_identities):
            raise ValueError("set identities must be unique")


def _contains_duplicate[ValueT](values: tuple[ValueT, ...]) -> bool:
    """Check equality-based uniqueness without requiring hashable identities."""
    return any(value in values[:index] for index, value in enumerate(values))
