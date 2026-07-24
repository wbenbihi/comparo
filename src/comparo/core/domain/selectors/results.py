"""Exhaustive selector runtime results without collapsed failure states."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import is_json_value

type SelectedValue = (
    ExactValue | Duration | ByteCount | Count | Ratio | UtcInstant | BodyId | ViewId
)


def is_selected_value(value: object) -> TypeGuard[SelectedValue]:
    """Narrow a value to the closed selector-value union."""
    return is_json_value(value) or type(value) in {
        BytesValue,
        Duration,
        ByteCount,
        Count,
        Ratio,
        UtcInstant,
        BodyId,
        ViewId,
    }


def _is_selected_value_type(value_type: type[SelectedValue]) -> bool:
    """Validate a sealed sensitive wrapper using only its non-secret payload type."""
    return value_type in {
        BytesValue,
        Duration,
        ByteCount,
        Count,
        Ratio,
        UtcInstant,
        BodyId,
        ViewId,
        JsonNull,
        JsonBoolean,
        JsonInteger,
        JsonDecimal,
        JsonString,
        JsonArray,
        JsonObject,
    }


@final
class SelectionMatch(msgspec.Struct, frozen=True, kw_only=True):
    """One selected value at one concrete typed source location."""

    value: SelectedValue | SensitiveValue[SelectedValue]
    location: ConcreteLocation

    def __post_init__(self) -> None:
        """Require closed typed value and location unions."""
        if isinstance(self.value, SensitiveValue) and not _is_selected_value_type(
            self.value.payload_type
        ):
            raise TypeError("sensitive selection payload has an unsupported type")
        if not isinstance(self.value, SensitiveValue) and not is_selected_value(self.value):
            raise TypeError("selection match value has an unsupported type")
        if not is_concrete_location(self.location):
            raise TypeError("selection match location has an unsupported type")

    @property
    def sensitivity(self) -> Sensitivity:
        """Derive sensitivity from containment; callers cannot mislabel a secret public."""
        if isinstance(self.value, SensitiveValue):
            return Sensitivity.SENSITIVE
        return Sensitivity.PUBLIC

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Expose safe lineage identifiers without revealing selected payload data."""
        if isinstance(self.value, SensitiveValue):
            return self.value.origins
        return ()


@final
class SelectionReady(msgspec.Struct, frozen=True, kw_only=True):
    """A complete ordered result; an empty tuple is valid."""

    matches: tuple[SelectionMatch, ...]

    def __post_init__(self) -> None:
        """Preserve immutable deterministic match order."""
        if type(self.matches) is not tuple:
            raise TypeError("selection matches must be an immutable tuple")
        if any(type(match) is not SelectionMatch for match in self.matches):
            raise TypeError("selection result contains an unsupported match")


class SourceUnavailableReason(StrEnum):
    """Typed reasons the addressed observation source does not exist."""

    OBSERVATION_UNAVAILABLE = "observation-unavailable"
    NO_BODY = "no-body"
    NO_TERMINAL_EXCHANGE = "no-terminal-exchange"
    NO_SUCH_ATTEMPT = "no-such-attempt"
    NO_SUCH_STEP = "no-such-step"


@final
class SelectionSourceUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """The requested typed source is unavailable."""

    reason: SourceUnavailableReason

    def __post_init__(self) -> None:
        """Require a stable source category."""
        if type(self.reason) is not SourceUnavailableReason:
            raise TypeError("selection source reason must be a SourceUnavailableReason")


@final
class SelectionDecodeFailure(msgspec.Struct, frozen=True, kw_only=True):
    """The explicitly requested semantic view failed strict decoding."""

    code: DiagnosticCode
    view: BodyViewKind

    def __post_init__(self) -> None:
        """Require safe diagnostics and the failed view."""
        if type(self.code) is not DiagnosticCode or type(self.view) is not BodyViewKind:
            raise TypeError("decode failure requires a DiagnosticCode and BodyViewKind")


class SelectionIncompleteReason(StrEnum):
    """Non-budget reasons complete selector semantics are unavailable."""

    PARTIAL_CAPTURE = "partial-capture"
    PARTIAL_VIEW = "partial-view"
    STREAM_WINDOW_INCOMPLETE = "stream-window-incomplete"
    UPSTREAM_FAILURE = "upstream-failure"


@final
class SelectionIncomplete(msgspec.Struct, frozen=True, kw_only=True):
    """The source exists but is too incomplete for the selector contract."""

    reason: SelectionIncompleteReason

    def __post_init__(self) -> None:
        """Require a stable incompleteness category."""
        if type(self.reason) is not SelectionIncompleteReason:
            raise TypeError("selection incomplete reason must be a SelectionIncompleteReason")


@final
class SelectionBudgetExceeded(msgspec.Struct, frozen=True, kw_only=True):
    """Selection exhausted a budget without reporting a successful prefix."""

    exhaustion: BudgetExceeded

    def __post_init__(self) -> None:
        """Require shared exact budget evidence."""
        if type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("selection budget result requires BudgetExceeded evidence")
        # ResourceBudget already binds resources to valid stages, and BudgetExceeded
        # binds each resource/stage pair to its only permitted exhaustion effects.
        if self.exhaustion.budget.stage is not BudgetStage.SELECTION:
            raise ValueError("selection budget evidence must come from the selection stage")


@final
class SelectionUnsupported(msgspec.Struct, frozen=True, kw_only=True):
    """A required implementation capability is unavailable."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a safe stable diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("unsupported selection requires a DiagnosticCode")


@final
class SelectionCancelled(msgspec.Struct, frozen=True, kw_only=True):
    """Selection was cancelled before a complete result existed."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a safe stable diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("cancelled selection requires a DiagnosticCode")


type SelectionResult = (
    SelectionReady
    | SelectionSourceUnavailable
    | SelectionDecodeFailure
    | SelectionIncomplete
    | SelectionBudgetExceeded
    | SelectionUnsupported
    | SelectionCancelled
)
