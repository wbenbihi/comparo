"""Exhaustive selector runtime results without collapsed failure states."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.result_values import SelectionMatch as SelectionMatch
from comparo.core.domain.selectors.result_values import (
    SelectionViewProvenance as SelectionViewProvenance,
)
from comparo.core.domain.selectors.result_values import (
    require_match_collection_contract as require_match_collection_contract,
)
from comparo.core.domain.selectors.result_values import (
    require_match_contract as require_match_contract,
)
from comparo.core.domain.selectors.result_values import (
    require_view_provenance as require_view_provenance,
)
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.values import HttpFieldItems as HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue as HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue as SelectedValue
from comparo.core.domain.selectors.values import is_selected_value as is_selected_value


@final
class SelectionReady(msgspec.Struct, frozen=True, kw_only=True):
    """A complete ordered result satisfying its compiled runtime contract."""

    selector: CompiledSelector
    environment_execution_id: EnvironmentExecutionId
    matches: tuple[SelectionMatch, ...]
    views: tuple[SelectionViewProvenance, ...] = ()

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
        require_match_collection_contract(self.matches, self.selector)
        require_view_provenance(
            self.views,
            self.selector,
            tuple(match.location for match in self.matches),
        )
        for match in self.matches:
            require_match_contract(match, self.selector, self.environment_execution_id)


class SelectionPartialReason(DomainStrEnum):
    """Reasons useful complete stream items exist without a complete result."""

    VIEW_PREFIX = "view-prefix"
    STREAM_WINDOW_INCOMPLETE = "stream-window-incomplete"
    ITEM_FAILURE = "item-failure"


@final
class SelectionItemDecodeIssue(msgspec.Struct, frozen=True, kw_only=True):
    """One located stream-record or nested-event decoding failure."""

    location: StreamLocation
    decoder: DecoderSpec
    code: DecodeErrorCode

    def __post_init__(self) -> None:
        """Require exact decoder provenance and a stable typed failure."""
        if type(self.location) is not StreamLocation:
            raise TypeError("selection item issue requires a StreamLocation")
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("selection decode issue requires a DecoderSpec")
        if type(self.code) is not DecodeErrorCode:
            raise TypeError("selection decode issue requires a DecodeErrorCode")


@final
class SelectionItemBudgetIssue(msgspec.Struct, frozen=True, kw_only=True):
    """One located nested-event decode budget exhaustion with exact evidence."""

    location: StreamLocation
    decoder: DecoderSpec
    exhaustion: BudgetExceeded

    def __post_init__(self) -> None:
        """Preserve the upstream decoder and canonical decoding exhaustion."""
        if type(self.location) is not StreamLocation:
            raise TypeError("selection item issue requires a StreamLocation")
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("selection budget issue requires a DecoderSpec")
        if type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("selection budget issue requires BudgetExceeded evidence")
        if self.exhaustion.budget.stage is not BudgetStage.DECODING:
            raise ValueError("selection item budget issue requires decoding evidence")


type SelectionItemIssue = SelectionItemDecodeIssue | SelectionItemBudgetIssue


@final
class SelectionPartial(msgspec.Struct, frozen=True, kw_only=True):
    """Complete selected items from a source whose enclosing extent is incomplete."""

    selector: CompiledSelector
    environment_execution_id: EnvironmentExecutionId
    matches: tuple[SelectionMatch, ...]
    reason: SelectionPartialReason
    issues: tuple[SelectionItemIssue, ...] = ()
    views: tuple[SelectionViewProvenance, ...] = ()

    def __post_init__(self) -> None:
        """Keep a usable prefix explicit and impossible to mistake for ready."""
        _require_partial_components(self)
        require_view_provenance(
            self.views,
            self.selector,
            (
                *(match.location for match in self.matches),
                *(issue.location for issue in self.issues),
            ),
        )
        for match in self.matches:
            require_match_contract(match, self.selector, self.environment_execution_id)
        _require_partial_issue_ownership(self)
        require_match_collection_contract(self.matches, self.selector)


def _require_partial_components(result: SelectionPartial) -> None:
    """Validate the direct immutable components of a partial result."""
    if type(result.selector) is not CompiledSelector:
        raise TypeError("partial selection requires a CompiledSelector")
    if type(result.environment_execution_id) is not EnvironmentExecutionId:
        raise TypeError("partial selection requires an EnvironmentExecutionId")
    if result.selector.capabilities.cardinality is not SelectorCardinality.ZERO_OR_MORE:
        raise ValueError("only plural selectors can return a partial result")
    if type(result.matches) is not tuple or not result.matches:
        raise ValueError("partial selection requires at least one complete match")
    if any(type(match) is not SelectionMatch for match in result.matches):
        raise TypeError("partial selection contains an unsupported match")
    if type(result.reason) is not SelectionPartialReason:
        raise TypeError("partial selection requires a SelectionPartialReason")
    if type(result.issues) is not tuple or any(
        type(issue) not in {SelectionItemDecodeIssue, SelectionItemBudgetIssue}
        for issue in result.issues
    ):
        raise TypeError("partial selection issues must be an immutable typed tuple")


def _require_partial_issue_ownership(result: SelectionPartial) -> None:
    """Bind every partial issue to its selector and execution."""
    if any(
        issue.location.environment_execution_id != result.environment_execution_id
        for issue in result.issues
    ):
        raise ValueError("partial selection issue belongs to another execution")
    if any(issue.location.source is not result.selector.source for issue in result.issues):
        raise ValueError("partial selection issue source contradicts its selector")
    issue_locations = tuple(issue.location for issue in result.issues)
    if len(set(issue_locations)) != len(issue_locations):
        raise ValueError("partial selection issue locations must be unique")


class SourceUnavailableReason(DomainStrEnum):
    """Typed reasons the addressed observation source does not exist."""

    OBSERVATION_UNAVAILABLE = "observation-unavailable"
    NO_BODY = "no-body"
    NO_TERMINAL_EXCHANGE = "no-terminal-exchange"
    NO_TERMINAL_RESPONSE = "no-terminal-response"
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
class SelectionFactUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """The source exists but one applicable typed fact was not measured or performed."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Retain only a stable payload-free reason code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("unavailable selection fact requires a DiagnosticCode")


@final
class SelectionObservedInvalid(msgspec.Struct, frozen=True, kw_only=True):
    """The selected observation explicitly records malformed source metadata."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Retain only a stable payload-free invalidity code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("invalid observed selection fact requires a DiagnosticCode")


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
    """Selection or its required decoded view exhausted a canonical budget."""

    exhaustion: BudgetExceeded

    def __post_init__(self) -> None:
        """Require shared exact budget evidence."""
        if type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("selection budget result requires BudgetExceeded evidence")
        # A selector can encounter its own exhaustion or the cached exhaustion of the
        # explicitly requested decoded view.  In both cases the exact responsible stage,
        # operation, resource, scope, and usage remain available on the shared evidence.
        if self.exhaustion.budget.stage not in {
            BudgetStage.DECODING,
            BudgetStage.SELECTION,
        }:
            raise ValueError("selection budget evidence must come from decoding or selection")


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
    | SelectionPartial
    | SelectionSourceUnavailable
    | SelectionFactUnavailable
    | SelectionObservedInvalid
    | SelectionDecodeFailure
    | SelectionIncomplete
    | SelectionBudgetExceeded
    | SelectionUnsupported
    | SelectionCancelled
)


def is_selection_result(value: object) -> TypeGuard[SelectionResult]:
    """Narrow an arbitrary value to the exhaustive runtime result union."""
    return type(value) in {
        SelectionReady,
        SelectionPartial,
        SelectionSourceUnavailable,
        SelectionFactUnavailable,
        SelectionObservedInvalid,
        SelectionDecodeFailure,
        SelectionIncomplete,
        SelectionBudgetExceeded,
        SelectionUnsupported,
        SelectionCancelled,
    }
