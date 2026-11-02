"""Leaf contracts for logical value names and their permitted scopes."""

from __future__ import annotations

import re
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.scopes import ValueScopeKind

_SYMBOL_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class ValueNamespace(DomainStrEnum):
    """Disjoint symbol tables available to explicit value lookups."""

    VARIABLES = "variables"
    SECRETS = "secrets"
    ENV = "env"
    PARAMETERS = "parameters"
    MATRIX = "matrix"
    STEPS = "steps"


_NAMESPACE_SCOPES = (
    (ValueNamespace.VARIABLES, frozenset(ValueScopeKind)),
    (ValueNamespace.SECRETS, frozenset(ValueScopeKind)),
    (ValueNamespace.ENV, frozenset({ValueScopeKind.ENVIRONMENT})),
    (ValueNamespace.PARAMETERS, frozenset({ValueScopeKind.CELL, ValueScopeKind.STEP})),
    (ValueNamespace.MATRIX, frozenset({ValueScopeKind.CELL})),
    (ValueNamespace.STEPS, frozenset({ValueScopeKind.STEP})),
)


@final
class SymbolPath(msgspec.Struct, frozen=True, kw_only=True):
    """A non-empty static namespace path such as ``login.token``."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require an immutable path of deterministic symbol segments."""
        if type(self.segments) is not tuple:
            raise TypeError("symbol path segments must be an immutable tuple")
        if not self.segments:
            raise ValueError("symbol path must contain at least one segment")
        if any(
            type(segment) is not str or _SYMBOL_PATTERN.fullmatch(segment) is None
            for segment in self.segments
        ):
            raise ValueError("symbol path contains an invalid segment")


def namespace_supports_declared_scope(
    namespace: ValueNamespace,
    scope: ValueScopeKind,
) -> bool:
    """Return whether a namespace may declare values at one abstract scope."""
    if type(namespace) is not ValueNamespace:
        raise TypeError("value namespace must be a ValueNamespace")
    if type(scope) is not ValueScopeKind:
        raise TypeError("declared value scope must be a ValueScopeKind")
    scopes = next(scopes for candidate, scopes in _NAMESPACE_SCOPES if candidate is namespace)
    return scope in scopes


def runtime_value_scope_kind(scope: RuntimeScope) -> ValueScopeKind:
    """Map one exact runtime coordinate to its declared value-scope category."""
    if type(scope) is RunScope:
        return ValueScopeKind.RUN
    if type(scope) is CellScope:
        return ValueScopeKind.CELL
    if type(scope) is TrialScope:
        return ValueScopeKind.TRIAL
    if type(scope) is EnvironmentScope:
        return ValueScopeKind.ENVIRONMENT
    if type(scope) is ExecutionScope:
        return ValueScopeKind.EXECUTION
    if type(scope) is StepScope:
        return ValueScopeKind.STEP
    if type(scope) is AttemptScope:
        return ValueScopeKind.ATTEMPT
    raise TypeError("resolution scope must be an exact RuntimeScope")


def namespace_supports_runtime_scope(
    namespace: ValueNamespace,
    scope: RuntimeScope,
) -> bool:
    """Return whether a namespace may produce a value at one concrete scope."""
    return namespace_supports_declared_scope(namespace, runtime_value_scope_kind(scope))


def namespace_reaches_runtime_scope(
    namespace: ValueNamespace,
    scope: RuntimeScope,
) -> bool:
    """Return whether a namespace can produce at an ancestor of one consumer scope."""
    consumer_kind = runtime_value_scope_kind(scope)
    reachable_kinds = {ValueScopeKind.RUN, consumer_kind}
    if type(scope) is TrialScope:
        reachable_kinds.add(ValueScopeKind.CELL)
    elif type(scope) in {ExecutionScope, StepScope, AttemptScope}:
        reachable_kinds.update(
            {
                ValueScopeKind.CELL,
                ValueScopeKind.TRIAL,
                ValueScopeKind.ENVIRONMENT,
                ValueScopeKind.EXECUTION,
            }
        )
    if type(scope) is AttemptScope and scope.step_execution_id is not None:
        reachable_kinds.add(ValueScopeKind.STEP)
    return any(
        namespace_supports_declared_scope(namespace, candidate) for candidate in reachable_kinds
    )
