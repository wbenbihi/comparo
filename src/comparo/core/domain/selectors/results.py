"""Exhaustive selector runtime results without collapsed failure states."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.selectors.contracts import contract_accepts_runtime_kind
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import ConcreteLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.locations import is_concrete_location
from comparo.core.domain.selectors.locations import location_environment_execution_id
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.model import require_location_matches_selector
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems as HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue as HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue as SelectedValue
from comparo.core.domain.selectors.values import is_selected_value as is_selected_value
from comparo.core.domain.selectors.values import is_selected_value_type
from comparo.core.domain.selectors.values import selected_value_kind_type
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value


@final
class SelectionMatch(msgspec.Struct, frozen=True, kw_only=True):
    """One selected value at one concrete typed source location."""

    value: SelectedValue | SensitiveValue[SelectedValue]
    location: ConcreteLocation

    def __post_init__(self) -> None:
        """Require closed typed value and location unions."""
        if is_sensitive_value(self.value) and not is_selected_value_type(self.value.payload_type):
            raise TypeError("sensitive selection payload has an unsupported type")
        if not is_sensitive_value(self.value) and not is_selected_value(self.value):
            raise TypeError("selection match value has an unsupported type")
        if not is_concrete_location(self.location):
            raise TypeError("selection match location has an unsupported type")
        _require_handle_identity(self)

    @property
    def sensitivity(self) -> Sensitivity:
        """Derive sensitivity from containment; callers cannot mislabel a secret public."""
        if is_sensitive_value(self.value):
            return Sensitivity.SENSITIVE
        return Sensitivity.PUBLIC

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Expose safe lineage identifiers without revealing selected payload data."""
        if is_sensitive_value(self.value):
            return self.value.origins
        return ()


@final
class SelectionReady(msgspec.Struct, frozen=True, kw_only=True):
    """A complete ordered result satisfying its compiled runtime contract."""

    selector: CompiledSelector
    environment_execution_id: EnvironmentExecutionId
    matches: tuple[SelectionMatch, ...]

    def __post_init__(self) -> None:
        """Preserve immutable deterministic match order."""
        if type(self.selector) is not CompiledSelector:
            raise TypeError("selection result requires a CompiledSelector")
        if type(self.environment_execution_id) is not EnvironmentExecutionId:
            raise TypeError("selection result requires an EnvironmentExecutionId")
        if type(self.matches) is not tuple:
            raise TypeError("selection matches must be an immutable tuple")
        if any(type(match) is not SelectionMatch for match in self.matches):
            raise TypeError("selection result contains an unsupported match")
        for match in self.matches:
            require_match_contract(match, self.selector, self.environment_execution_id)
        _require_match_collection_contract(self.matches, self.selector)


def require_match_contract(
    match: SelectionMatch,
    selector: CompiledSelector,
    environment_execution_id: EnvironmentExecutionId,
) -> None:
    """Bind one concrete match to its compiled selector and execution contract."""
    if location_environment_execution_id(match.location) != environment_execution_id:
        raise ValueError("selection match belongs to a different Environment execution")
    require_location_matches_selector(match.location, selector)
    value_type = match.value.payload_type if is_sensitive_value(match.value) else type(match.value)
    kind = selected_value_kind_type(value_type)
    if kind is None or not contract_accepts_runtime_kind(selector.output, kind):
        raise ValueError("selection match value kind contradicts selector output contract")
    if value_type is BareQueryFieldValue and selector.source is not SelectorSource.REQUEST_QUERY:
        raise ValueError("bare query values require the request-query selector source")
    if is_sensitive_value(match.value) and value_type is BareQueryFieldValue:
        raise ValueError("bare query values cannot carry sensitive payload containment")


def _require_match_collection_contract(
    matches: tuple[SelectionMatch, ...],
    selector: CompiledSelector,
) -> None:
    locations = tuple(match.location for match in matches)
    if not selector.capabilities.may_duplicate and len(set(locations)) != len(locations):
        raise ValueError("selector without duplicate semantics cannot repeat a location")
    cardinality = selector.capabilities.cardinality
    if cardinality is SelectorCardinality.EXACTLY_ONE and len(matches) != 1:
        raise ValueError("exactly-one selector result must contain exactly one match")
    if cardinality is SelectorCardinality.ZERO_OR_ONE and len(matches) > 1:
        raise ValueError("zero-or-one selector result cannot contain more than one match")


def _require_handle_identity(match: SelectionMatch) -> None:
    if is_sensitive_value(match.value):
        if match.value.payload_type in {BodyId, ViewId}:
            raise ValueError("selector handle identities cannot be sensitive")
        return
    if type(match.value) is BodyId and (
        type(match.location) is not BodyLocation or match.location.body_id != match.value
    ):
        raise ValueError("selected BodyId must equal its concrete body location")
    if type(match.value) is not ViewId:
        return
    if type(match.location) not in {
        ViewLocation,
        JsonLocation,
        StreamLocation,
    }:
        raise ValueError("selected ViewId requires a concrete view location")
    location = cast("ViewLocation | JsonLocation | StreamLocation", match.location)
    if location.view_id != match.value:
        raise ValueError("selected ViewId must equal its concrete view location")


class SourceUnavailableReason(DomainStrEnum):
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


class SelectionIncompleteReason(DomainStrEnum):
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
