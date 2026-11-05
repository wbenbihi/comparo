"""Resolution inputs independent of executable subjects and plan occurrences."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.scopes import RunScope
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.models import ValueBindingLayer


@final
class ResolutionInputs(msgspec.Struct, frozen=True, kw_only=True):
    """Exact namespace bindings and policy for a bounded resolver lifetime."""

    run: RunScope
    policy: DefinitionRef
    environments: tuple[DefinitionRef, ...]
    value_layers: tuple[ValueBindingLayer, ...]
    standalone: bool = False

    def __post_init__(self) -> None:
        """Require typed inputs without inventing an execution subject."""
        if type(self.standalone) is not bool:
            raise TypeError("resolution standalone mode must be boolean")
        if type(self.run) is not RunScope:
            raise TypeError("resolution inputs require a RunScope")
        if (
            type(self.policy) is not DefinitionRef
            or self.policy.identity.kind is not ResourceKind.EXECUTION_POLICY
        ):
            raise TypeError("resolution inputs require an ExecutionPolicy")
        if (
            type(self.environments) is not tuple
            or any(
                type(item) is not DefinitionRef
                or item.identity.kind is not ResourceKind.ENVIRONMENT
                for item in self.environments
            )
            or len(set(self.environments)) != len(self.environments)
        ):
            raise TypeError("resolution inputs require unique Environment definitions")
        if type(self.value_layers) is not tuple or any(
            type(item) is not ValueBindingLayer for item in self.value_layers
        ):
            raise TypeError("resolution inputs require typed value layers")

    @classmethod
    def from_prepared(cls, prepared: PreparedRun) -> ResolutionInputs:
        """Adapt the existing runtime handoff without changing resolution semantics."""
        return cls(
            run=RunScope(run_id=prepared.invocation.run_id),
            policy=prepared.authority.policy,
            environments=prepared.effective.environments,
            value_layers=prepared.effective.value_layers,
        )
