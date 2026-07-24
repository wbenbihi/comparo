"""Exhaustive selector runtime results without collapsed failure states."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import IncompleteViewReason
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.result_values import SelectionMatch as SelectionMatch
from comparo.core.domain.selectors.result_values import (
    SelectionViewFailureProvenance as SelectionViewFailureProvenance,
)
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
from comparo.core.domain.selectors.sources import NATIVE_STREAM_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
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
    stream_extent: StreamExtent | None = None

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
        _require_ready_stream_extent(self)
        require_match_collection_contract(self.matches, self.selector)
        require_view_provenance(
            self.views,
            self.selector,
            tuple(match.location for match in self.matches),
        )
        for match in self.matches:
            require_match_contract(match, self.selector, self.environment_execution_id)


def _require_ready_stream_extent(result: SelectionReady) -> None:
    """Bind native stream success to its exact complete or authored-window extent."""
    if result.stream_extent is not None and type(result.stream_extent) is not StreamExtent:
        raise TypeError("ready stream extent must be a StreamExtent")
    native_stream = result.selector.source in NATIVE_STREAM_SELECTOR_SOURCES
    if native_stream != (result.stream_extent is not None):
        raise ValueError("exactly native stream ready results require their StreamExtent")
    if (
        result.stream_extent is not None
        and result.stream_extent.completeness is StreamCompleteness.INCOMPLETE
    ):
        raise ValueError("ready native stream selection requires a full or window extent")


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
    stream_extent: StreamExtent | None = None

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
    if type(result.matches) is not tuple:
        raise TypeError("partial selection matches must be an immutable tuple")
    if any(type(match) is not SelectionMatch for match in result.matches):
        raise TypeError("partial selection contains an unsupported match")
    if type(result.reason) is not SelectionPartialReason:
        raise TypeError("partial selection requires a SelectionPartialReason")
    if type(result.issues) is not tuple or any(
        type(issue) not in {SelectionItemDecodeIssue, SelectionItemBudgetIssue}
        for issue in result.issues
    ):
        raise TypeError("partial selection issues must be an immutable typed tuple")
    if not result.matches and not result.issues:
        raise ValueError("partial selection requires a complete match or located issue")
    if result.stream_extent is not None and type(result.stream_extent) is not StreamExtent:
        raise TypeError("partial stream extent must be a StreamExtent")
    stream_source = _is_native_stream_source(result.selector.source)
    if stream_source != (result.stream_extent is not None):
        raise ValueError("exactly native stream partial results require their StreamExtent")
    _require_partial_reason_evidence(result, stream_source=stream_source)


def _is_native_stream_source(source: SelectorSource) -> bool:
    return source in NATIVE_STREAM_SELECTOR_SOURCES


def _require_partial_reason_evidence(
    result: SelectionPartial,
    *,
    stream_source: bool,
) -> None:
    """Bind each partial reason to the exact evidence it promises."""
    if result.reason is SelectionPartialReason.VIEW_PREFIX:
        if result.selector.source not in {
            SelectorSource.REQUEST_BODY,
            SelectorSource.RESPONSE_BODY,
        }:
            raise ValueError("view-prefix partial results require an HTTP body source")
        if result.issues or result.stream_extent is not None:
            raise ValueError("view-prefix partial results cannot retain stream evidence")
    elif result.reason is SelectionPartialReason.ITEM_FAILURE:
        if not stream_source or not result.issues:
            raise ValueError("item-failure partial results require located stream issues")
    elif result.reason is SelectionPartialReason.STREAM_WINDOW_INCOMPLETE:
        if (
            not stream_source
            or result.issues
            or result.stream_extent is None
            or result.stream_extent.completeness is not StreamCompleteness.INCOMPLETE
        ):
            raise ValueError(
                "stream-window partial results require an incomplete extent without item issues"
            )


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
    NO_SUCH_EXCHANGE = "no-such-exchange"
    NO_SUCH_STEP = "no-such-step"


@final
class SelectionSourceUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """The requested typed source is unavailable."""

    reason: SourceUnavailableReason
    view_failure: SelectionViewFailureProvenance | None = None

    def __post_init__(self) -> None:
        """Require a stable source category."""
        if type(self.reason) is not SourceUnavailableReason:
            raise TypeError("selection source reason must be a SourceUnavailableReason")
        _require_optional_view_failure(self.view_failure)


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
    provenance: SelectionViewFailureProvenance

    def __post_init__(self) -> None:
        """Require safe diagnostics and the failed view."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("decode failure requires a DiagnosticCode")
        if type(self.provenance) is not SelectionViewFailureProvenance:
            raise TypeError("decode failure requires exact view provenance")

    @property
    def view(self) -> BodyViewKind:
        """Return the failed requested view from its exact provenance."""
        return self.provenance.requested


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
    view_failure: SelectionViewFailureProvenance | None = None
    view_reason: IncompleteViewReason | None = None

    def __post_init__(self) -> None:
        """Require a stable incompleteness category."""
        if type(self.reason) is not SelectionIncompleteReason:
            raise TypeError("selection incomplete reason must be a SelectionIncompleteReason")
        _require_optional_view_failure(self.view_failure)
        if self.view_reason is not None and type(self.view_reason) is not IncompleteViewReason:
            raise TypeError("selection incomplete view reason must be typed")
        if (self.reason is SelectionIncompleteReason.PARTIAL_VIEW) != (
            self.view_failure is not None
        ):
            raise ValueError("exactly partial-view incompleteness requires view provenance")
        if self.view_reason is not None and self.view_failure is None:
            raise ValueError("a view incompleteness reason requires view provenance")


@final
class SelectionBudgetExceeded(msgspec.Struct, frozen=True, kw_only=True):
    """Selection or its required decoded view exhausted a canonical budget."""

    exhaustion: BudgetExceeded
    view_failure: SelectionViewFailureProvenance | None = None

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
        _require_optional_view_failure(self.view_failure)
        if (self.exhaustion.budget.stage is BudgetStage.DECODING) != (
            self.view_failure is not None
        ):
            raise ValueError("exactly decoding budget exhaustion requires view provenance")


@final
class SelectionUnsupported(msgspec.Struct, frozen=True, kw_only=True):
    """A required implementation capability is unavailable."""

    code: DiagnosticCode
    view_failure: SelectionViewFailureProvenance | None = None

    def __post_init__(self) -> None:
        """Require a safe stable diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("unsupported selection requires a DiagnosticCode")
        _require_optional_view_failure(self.view_failure)


@final
class SelectionCancelled(msgspec.Struct, frozen=True, kw_only=True):
    """Selection was cancelled before a complete result existed."""

    code: DiagnosticCode
    view_failure: SelectionViewFailureProvenance | None = None

    def __post_init__(self) -> None:
        """Require a safe stable diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("cancelled selection requires a DiagnosticCode")
        _require_optional_view_failure(self.view_failure)


def _require_optional_view_failure(value: object) -> None:
    if value is not None and type(value) is not SelectionViewFailureProvenance:
        raise TypeError("selection view failure provenance has an unsupported type")


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
