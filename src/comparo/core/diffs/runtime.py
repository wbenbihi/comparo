"""Private typed values exchanged between pure diff pipeline stages."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import is_selected_value
from comparo.core.domain.selectors.values import is_selected_value_type
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value


@final
class DerivedValue(msgspec.Struct, frozen=True, kw_only=True):
    """One live derived value retaining every source and operation identity."""

    value: SelectedValue | SensitiveValue[SelectedValue]
    selector: CompiledSelector
    origins: tuple[ConcreteLocation, ...]
    operations: tuple[DiffOperationIdentity, ...] = ()
    empty_reducer_identity: bool = False

    def __post_init__(self) -> None:  # noqa: C901
        """Require a closed value and exact immutable lineage."""
        if is_sensitive_value(self.value):
            if not is_selected_value_type(self.value.payload_type):
                raise TypeError("sensitive derived value has an unsupported payload")
        elif not is_selected_value(self.value):
            raise TypeError("derived value has an unsupported payload")
        if type(self.selector) is not CompiledSelector:
            raise TypeError("derived value requires its root CompiledSelector")
        if type(self.origins) is not tuple:
            raise TypeError("derived origins must be an immutable tuple")
        if any(not is_concrete_location(origin) for origin in self.origins):
            raise TypeError("derived origins must be concrete locations")
        if len(set(self.origins)) != len(self.origins):
            raise ValueError("derived origins must be unique")
        if type(self.operations) is not tuple or any(
            type(operation) is not DiffOperationIdentity for operation in self.operations
        ):
            raise TypeError("derived operation trace must be immutable and typed")
        if type(self.empty_reducer_identity) is not bool:
            raise TypeError("empty reducer identity marker must be bool")
        if bool(self.origins) == self.empty_reducer_identity:
            raise ValueError(
                "exactly originless derived values require an empty reducer identity marker"
            )
        if self.empty_reducer_identity and (
            not self.operations or self.operations[-1].family is not DiffOperationFamily.REDUCER
        ):
            raise ValueError("originless derived values require a terminal reducer operation")

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Return safe taint identity without exposing the payload."""
        return self.value.origins if is_sensitive_value(self.value) else ()


@final
class PairUnit(msgspec.Struct, frozen=True, kw_only=True):
    """One bilateral or one-sided unit emitted by a pairing strategy."""

    baseline: DerivedValue | None = None
    candidate: DerivedValue | None = None
    incomparable: DiagnosticCode | None = None
    ignored: bool = False

    def __post_init__(self) -> None:
        """Require at least one typed side and an optional safe issue code."""
        if self.baseline is not None and type(self.baseline) is not DerivedValue:
            raise TypeError("pair baseline must be a DerivedValue")
        if self.candidate is not None and type(self.candidate) is not DerivedValue:
            raise TypeError("pair candidate must be a DerivedValue")
        if self.baseline is None and self.candidate is None:
            raise ValueError("pair unit must consume at least one side")
        if self.incomparable is not None and type(self.incomparable) is not DiagnosticCode:
            raise TypeError("pair incomparable reason must be a DiagnosticCode")
        if type(self.ignored) is not bool:
            raise TypeError("pair ignored marker must be bool")
        if self.ignored and self.incomparable is not None:
            raise ValueError("pair unit cannot be both ignored and incomparable")


class DiffHaltError(Exception):
    """Payload-free control flow for a terminal pure pipeline interruption."""

    __slots__ = (
        "code",
        "exhaustion",
        "incomplete_cause",
        "partial_derived",
        "partial_pairs",
        "side",
    )

    def __init__(
        self,
        *,
        code: DiagnosticCode | None = None,
        exhaustion: BudgetExceeded | None = None,
        incomplete_cause: DiffIncompleteCause | None = None,
        partial_derived: tuple[DerivedValue, ...] = (),
        partial_pairs: tuple[PairUnit, ...] = (),
        side: DiffSide = DiffSide.BOTH,
    ) -> None:
        """Require exactly one safe terminal cause."""
        if (code is None) == (exhaustion is None):
            raise ValueError("diff halt requires exactly a code or budget exhaustion")
        if incomplete_cause is not None and type(incomplete_cause) is not DiffIncompleteCause:
            raise TypeError("diff halt incomplete cause must be typed")
        if incomplete_cause is not None and code is None:
            raise ValueError("expected incomplete diff halts require a diagnostic code")
        if incomplete_cause is DiffIncompleteCause.BUDGET_EXCEEDED:
            raise ValueError("budget diff halts use exact exhaustion evidence")
        self.code = code
        self.exhaustion = exhaustion
        self.incomplete_cause = incomplete_cause
        if type(partial_derived) is not tuple or any(
            type(value) is not DerivedValue for value in partial_derived
        ):
            raise TypeError("partial derived progress must be an immutable typed tuple")
        if type(partial_pairs) is not tuple or any(
            type(value) is not PairUnit for value in partial_pairs
        ):
            raise TypeError("partial pair progress must be an immutable typed tuple")
        if type(side) is not DiffSide:
            raise TypeError("diff halt side must be typed")
        self.partial_derived = partial_derived
        self.partial_pairs = partial_pairs
        self.side = side

    def prepend_derived(self, values: tuple[DerivedValue, ...], side: DiffSide) -> None:
        """Attach already-completed outputs while preserving the terminal cause."""
        self.partial_derived = (*values, *self.partial_derived)
        self.side = side


def merged_origins(*values: DerivedValue) -> tuple[ConcreteLocation, ...]:
    """Return stable first-seen concrete source lineage."""
    seen: set[ConcreteLocation] = set()
    output: list[ConcreteLocation] = []
    for value in values:
        for origin in value.origins:
            if origin not in seen:
                seen.add(origin)
                output.append(origin)
    return tuple(output)
