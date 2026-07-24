"""Mutation-resistant string enums for stable domain tokens."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import override


class DomainStrEnum(StrEnum):
    """A string enum whose live members cannot be altered after class construction."""

    def _is_finalized_member(self) -> bool:
        """Return whether EnumMeta has installed this member in its completed class."""
        try:
            name = super().__getattribute__("_name_")
        except AttributeError:
            return False
        return type(self).__members__.get(name) is self

    @override
    def __setattr__(self, name: str, value: object) -> None:
        """Allow EnumMeta initialization, then reject member-state mutation."""
        if self._is_finalized_member():
            raise AttributeError("domain enum members are immutable")
        super().__setattr__(name, value)

    @override
    def __delattr__(self, name: str) -> None:
        """Reject deletion from every completed member."""
        if self._is_finalized_member():
            raise AttributeError("domain enum members are immutable")
        super().__delattr__(name)

    @override
    def __getattribute__(self, name: str) -> object:
        """Expose member state through a read-only mapping."""
        value = super().__getattribute__(name)
        if name == "__dict__":
            return MappingProxyType(value)
        return value
