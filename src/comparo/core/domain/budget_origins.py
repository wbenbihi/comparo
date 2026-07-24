"""Exact value-free identities for every source of an effective budget."""

from __future__ import annotations

import re
from typing import final

import msgspec

from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.primitives import Fingerprint

_DEFAULT_SET_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


@final
class VersionedDefaultSetRef(msgspec.Struct, frozen=True, kw_only=True):
    """Stable version and content identity of one built-in default set."""

    identifier: str
    version: str
    fingerprint: Fingerprint

    def __post_init__(self) -> None:
        """Require canonical identity, decimal version, and exact content fingerprint."""
        if (
            type(self.identifier) is not str
            or _DEFAULT_SET_PATTERN.fullmatch(self.identifier) is None
        ):
            raise ValueError("default-set identifier must be a lowercase namespaced token")
        if (
            type(self.version) is not str
            or not self.version.isascii()
            or not self.version.isdigit()
            or int(self.version) < 1
            or str(int(self.version)) != self.version
        ):
            raise ValueError("default-set version must be a canonical positive decimal")
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("default-set fingerprint must be a Fingerprint")


@final
class DefaultBudgetOrigin(msgspec.Struct, frozen=True, kw_only=True):
    """An exact built-in default-set origin."""

    default_set: VersionedDefaultSetRef

    def __post_init__(self) -> None:
        """Require a stable versioned default-set reference."""
        if type(self.default_set) is not VersionedDefaultSetRef:
            raise TypeError("default budget origin requires a VersionedDefaultSetRef")


@final
class DefinitionBudgetOrigin(msgspec.Struct, frozen=True, kw_only=True):
    """An exact fingerprinted authored or operator-owned definition origin."""

    definition: DefinitionRef

    def __post_init__(self) -> None:
        """Require an exact definition reference."""
        if type(self.definition) is not DefinitionRef:
            raise TypeError("definition budget origin requires a DefinitionRef")


@final
class InvocationBudgetOrigin(msgspec.Struct, frozen=True, kw_only=True):
    """An exact invocation occurrence that supplied an ordinary override."""

    invocation_id: InvocationId

    def __post_init__(self) -> None:
        """Require a typed invocation occurrence identity."""
        if type(self.invocation_id) is not InvocationId:
            raise TypeError("invocation budget origin requires an InvocationId")


type BudgetOrigin = DefaultBudgetOrigin | DefinitionBudgetOrigin | InvocationBudgetOrigin
