"""Storage-backed decoded body-view descriptors and exhaustive resolution outcomes."""

from __future__ import annotations

import re
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value

_IDENTITY_PATTERN = re.compile(
    r"[a-z0-9]+(?:[.-][a-z0-9]+)*/[a-z0-9]+(?:[.-][a-z0-9]+)*@[1-9][0-9]*"
)
_OPTION_NAME_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


class ViewKind(DomainStrEnum):
    """The built-in semantic representations addressable by selectors and diffs."""

    BYTES = "bytes"
    TEXT = "text"
    JSON = "json"
    SSE = "sse"
    NDJSON = "ndjson"
    JSON_SEQUENCE = "json-sequence"


@final
class DecoderIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """A stable, versioned decoder implementation identity."""

    value: str

    def __post_init__(self) -> None:
        """Require a namespaced identity so implementations cannot collide silently."""
        if type(self.value) is not str or _IDENTITY_PATTERN.fullmatch(self.value) is None:
            raise ValueError("decoder identity must have the form namespace/name@positive-version")


@final
class DecoderRegistryIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """The versioned registry used by deterministic automatic view selection."""

    value: str

    def __post_init__(self) -> None:
        """Require the same stable versioned namespace as decoder identities."""
        if type(self.value) is not str or _IDENTITY_PATTERN.fullmatch(self.value) is None:
            raise ValueError("decoder registry must have the form namespace/name@positive-version")


@final
class DecoderOption(msgspec.Struct, frozen=True, kw_only=True):
    """One canonical decoder option retained in decoder and artifact provenance."""

    name: str
    value: JsonValue

    def __post_init__(self) -> None:
        """Require a static option name and an exact immutable JSON value."""
        if type(self.name) is not str or _OPTION_NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("decoder option name must be a lowercase hyphenated identifier")
        if not is_json_value(self.value):
            raise TypeError("decoder option value must be an exact JsonValue")


@final
class DecoderSpec(msgspec.Struct, frozen=True, kw_only=True):
    """A complete decoder choice, including semantic view and canonical options."""

    identity: DecoderIdentity
    view: ViewKind
    options: tuple[DecoderOption, ...] = ()

    def __post_init__(self) -> None:
        """Reject ambiguous duplicate options and mutable option collections."""
        if type(self.identity) is not DecoderIdentity:
            raise TypeError("decoder spec identity must be DecoderIdentity")
        if type(self.view) is not ViewKind:
            raise TypeError("decoder spec view must be ViewKind")
        if type(self.options) is not tuple or any(
            type(option) is not DecoderOption for option in self.options
        ):
            raise TypeError("decoder options must be an immutable tuple of DecoderOption values")
        names = tuple(option.name for option in self.options)
        if len(set(names)) != len(names):
            raise ValueError("decoder option names must be unique")


@final
class AutoViewSelection(msgspec.Struct, frozen=True, kw_only=True):
    """The exact registry and decoder selected from parsed HTTP media metadata."""

    registry: DecoderRegistryIdentity
    decoder: DecoderSpec

    def __post_init__(self) -> None:
        """Require validated automatic-selection provenance."""
        if type(self.registry) is not DecoderRegistryIdentity:
            raise TypeError("auto-view registry must be DecoderRegistryIdentity")
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("auto-view decoder must be DecoderSpec")


def _validate_auto_selection(
    selection: AutoViewSelection | None,
    decoder: DecoderSpec,
) -> None:
    if selection is not None and type(selection) is not AutoViewSelection:
        raise TypeError("automatic view selection must be AutoViewSelection when present")
    if selection is not None and selection.decoder != decoder:
        raise ValueError("automatic view selection must identify the resolved decoder")


@final
class ViewStorageToken(msgspec.Struct, frozen=True, kw_only=True):
    """A locator token that may appear in a handle only inside a sealed sensitive value."""

    value: str

    def __post_init__(self) -> None:
        """Require a non-empty token whose contents have no domain-level interpretation."""
        if type(self.value) is not str or not self.value:
            raise ValueError("view storage locator must be non-empty text")


type ViewStorageLocator = SensitiveValue[ViewStorageToken]


class ViewCompleteness(DomainStrEnum):
    """The semantic extent made available by a successful decoder."""

    FULL = "full"
    PREFIX = "prefix"


def _validate_view_containment(handle: DecodedViewHandle) -> None:
    """Prevent a decoded representation from lowering source sensitivity or completeness."""
    if type(handle.sensitivity) is not Sensitivity:
        raise TypeError("decoded-view sensitivity must be Sensitivity")
    if type(handle.completeness) is not ViewCompleteness:
        raise TypeError("decoded-view completeness must be ViewCompleteness")
    if (
        handle.source.sensitivity is Sensitivity.SENSITIVE
        and handle.sensitivity is not Sensitivity.SENSITIVE
    ):
        raise ValueError("decoded views cannot lower source-body sensitivity")
    if (
        handle.completeness is ViewCompleteness.FULL
        and type(handle.source) is BodyHandle
        and handle.source.extent is not BodyCaptureExtent.COMPLETE
    ):
        raise ValueError("a full decoded view requires a complete source body")
    if handle.completeness is ViewCompleteness.PREFIX and handle.decoder.view not in {
        ViewKind.BYTES,
        ViewKind.TEXT,
        ViewKind.SSE,
        ViewKind.NDJSON,
        ViewKind.JSON_SEQUENCE,
    }:
        raise ValueError("only prefix-safe decoded handles may represent a prefix")


def _validate_view_fingerprint(handle: DecodedViewHandle) -> None:
    """Keep fingerprints at least as sensitive as decoded content."""
    if handle.sensitivity is Sensitivity.PUBLIC and type(handle.fingerprint) is not Fingerprint:
        raise TypeError("public decoded-view fingerprint must be Fingerprint")
    if handle.sensitivity is Sensitivity.SENSITIVE and (
        not is_sensitive_value(handle.fingerprint)
        or handle.fingerprint.payload_type is not Fingerprint
    ):
        raise TypeError("sensitive decoded-view fingerprint must remain sealed")


@final
class DecodedViewHandle(msgspec.Struct, frozen=True, kw_only=True):
    """An opaque descriptor for a typed view owned by a DecodedViewStore port."""

    view_id: ViewId
    source: BodyHandle | RepeatableByteSource
    locator: ViewStorageLocator
    decoder: DecoderSpec
    fingerprint: Fingerprint | SensitiveValue[Fingerprint]
    sensitivity: Sensitivity
    completeness: ViewCompleteness
    auto_selection: AutoViewSelection | None = None

    def __post_init__(self) -> None:
        """Require canonical identities and keep the actual decoded content outside the model."""
        if type(self.view_id) is not ViewId:
            raise TypeError("decoded-view ID must be ViewId")
        if type(self.source) not in {BodyHandle, RepeatableByteSource}:
            raise TypeError("decoded-view source must be an exact response or request body handle")
        if not is_sensitive_value(self.locator):
            raise TypeError("decoded-view locator must be a sealed ViewStorageLocator")
        if self.locator.payload_type is not ViewStorageToken:
            raise TypeError("decoded-view locator payload must be ViewStorageToken")
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("decoded-view decoder must be DecoderSpec")
        _validate_view_containment(self)
        _validate_view_fingerprint(self)
        _validate_auto_selection(self.auto_selection, self.decoder)

    @property
    def body_id(self) -> BodyId:
        """Return the exact source body's identity for storage-port lookup compatibility."""
        if type(self.source) is BodyHandle:
            return self.source.body_id
        return self.source.source_id


class DecodeErrorCode(DomainStrEnum):
    """Portable classes of deterministic representation decoding failure."""

    INVALID_ENCODING = "invalid-encoding"
    INVALID_TEXT = "invalid-text"
    INVALID_SYNTAX = "invalid-syntax"
    DUPLICATE_MEMBER = "duplicate-member"
    NON_FINITE_NUMBER = "non-finite-number"
    TRAILING_CONTENT = "trailing-content"
    INVALID_MEDIA_METADATA = "invalid-media-metadata"


class IncompleteViewReason(DomainStrEnum):
    """Reasons a semantic view requires more entity data than capture supplied."""

    ENTITY_PARTIAL = "entity-partial"
    RECORD_PARTIAL = "record-partial"
    CONTENT_DECODING_INCOMPLETE = "content-decoding-incomplete"


class ViewBudgetKind(DomainStrEnum):
    """Independent view budget dimensions."""

    TEXT_CHARACTERS = "text-characters"
    JSON_DEPTH = "json-depth"
    JSON_NODES = "json-nodes"
    JSON_TOKENS = "json-tokens"
    INDEX_STORAGE = "index-storage"
    MEMORY = "memory"


class UnsupportedViewReason(DomainStrEnum):
    """Reasons a requested semantic representation cannot be produced."""

    DECODER_UNAVAILABLE = "decoder-unavailable"
    CONTENT_ENCODING_UNAVAILABLE = "content-encoding-unavailable"
    VIEW_NOT_APPLICABLE = "view-not-applicable"


@final
class ReadyView(msgspec.Struct, frozen=True, kw_only=True):
    """A successfully resolved storage-backed view with explicit extent."""

    handle: DecodedViewHandle
    completeness: ViewCompleteness

    def __post_init__(self) -> None:
        """Require a typed handle and prevent partial structured-document success."""
        if type(self.handle) is not DecodedViewHandle:
            raise TypeError("ready view handle must be DecodedViewHandle")
        if type(self.completeness) is not ViewCompleteness:
            raise TypeError("ready view completeness must be ViewCompleteness")
        if self.completeness is not self.handle.completeness:
            raise ValueError("ready-view completeness must match its stored handle")


@final
class ViewNoBody(msgspec.Struct, frozen=True, kw_only=True):
    """A requested decoder had no semantic response entity to consume."""

    decoder: DecoderSpec
    auto_selection: AutoViewSelection | None = None

    def __post_init__(self) -> None:
        """Retain the requested decoder even though no view exists."""
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("no-body view decoder must be DecoderSpec")
        _validate_auto_selection(self.auto_selection, self.decoder)


@final
class ViewDecodeError(msgspec.Struct, frozen=True, kw_only=True):
    """A deterministic representation failure cached as a typed view outcome."""

    decoder: DecoderSpec
    code: DecodeErrorCode
    auto_selection: AutoViewSelection | None = None

    def __post_init__(self) -> None:
        """Require stable decoder and error identities without raw payload messages."""
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("decode-error decoder must be DecoderSpec")
        if type(self.code) is not DecodeErrorCode:
            raise TypeError("decode-error code must be DecodeErrorCode")
        _validate_auto_selection(self.auto_selection, self.decoder)


@final
class ViewIncomplete(msgspec.Struct, frozen=True, kw_only=True):
    """A representation that cannot be complete from the captured entity extent."""

    decoder: DecoderSpec
    reason: IncompleteViewReason
    auto_selection: AutoViewSelection | None = None

    def __post_init__(self) -> None:
        """Require typed incomplete-view provenance."""
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("incomplete-view decoder must be DecoderSpec")
        if type(self.reason) is not IncompleteViewReason:
            raise TypeError("incomplete-view reason must be IncompleteViewReason")
        _validate_auto_selection(self.auto_selection, self.decoder)


@final
class ViewBudgetExceeded(msgspec.Struct, frozen=True, kw_only=True):
    """A decoder stopped because one independent semantic-view budget was exhausted."""

    decoder: DecoderSpec
    exhaustion: BudgetExceeded
    auto_selection: AutoViewSelection | None = None

    def __post_init__(self) -> None:
        """Require the exact decoder and exceeded budget dimension."""
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("budget-exceeded decoder must be DecoderSpec")
        _validate_view_budget_exhaustion(self.exhaustion)
        _validate_auto_selection(self.auto_selection, self.decoder)

    @property
    def budget(self) -> ViewBudgetKind:
        """Derive the local view classification from canonical resource identity."""
        return _view_budget_kind(self.exhaustion.budget.resource)


def _view_budget_kind(resource: BudgetResource) -> ViewBudgetKind:
    """Map one supported canonical view resource to its presentation classification."""
    try:
        return {
            BudgetResource.DECODED_CHARACTERS: ViewBudgetKind.TEXT_CHARACTERS,
            BudgetResource.JSON_DEPTH: ViewBudgetKind.JSON_DEPTH,
            BudgetResource.JSON_NODES: ViewBudgetKind.JSON_NODES,
            BudgetResource.JSON_TOKENS: ViewBudgetKind.JSON_TOKENS,
            BudgetResource.VIEW_INDEX_BYTES: ViewBudgetKind.INDEX_STORAGE,
            BudgetResource.MEMORY_BYTES: ViewBudgetKind.MEMORY,
        }[resource]
    except KeyError as error:
        raise ValueError("view exhaustion resource is not a semantic-view dimension") from error


def _validate_view_budget_exhaustion(exhaustion: BudgetExceeded) -> None:
    """Require canonical body-scoped decoding exhaustion for a semantic view."""
    if type(exhaustion) is not BudgetExceeded:
        raise TypeError("view budget evidence must be BudgetExceeded")
    _view_budget_kind(exhaustion.budget.resource)
    budget = exhaustion.budget
    if (
        budget.scope_kind is not BudgetScopeKind.BODY
        or type(budget.scope) is not BodyBudgetScope
        or budget.stage is not BudgetStage.DECODING
    ):
        raise ValueError("view exhaustion requires its exact body scope and decoding stage")


@final
class ViewUnsupported(msgspec.Struct, frozen=True, kw_only=True):
    """A requested representation unavailable in the effective capability set."""

    decoder: DecoderSpec
    reason: UnsupportedViewReason
    auto_selection: AutoViewSelection | None = None

    def __post_init__(self) -> None:
        """Require typed unsupported-view provenance."""
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("unsupported-view decoder must be DecoderSpec")
        if type(self.reason) is not UnsupportedViewReason:
            raise TypeError("unsupported-view reason must be UnsupportedViewReason")
        _validate_auto_selection(self.auto_selection, self.decoder)


@final
class ViewCancelled(msgspec.Struct, frozen=True, kw_only=True):
    """A view computation interrupted by the enclosing run cancellation boundary."""

    decoder: DecoderSpec
    auto_selection: AutoViewSelection | None = None

    def __post_init__(self) -> None:
        """Retain the decoder whose work was cancelled."""
        if type(self.decoder) is not DecoderSpec:
            raise TypeError("cancelled-view decoder must be DecoderSpec")
        _validate_auto_selection(self.auto_selection, self.decoder)


type DecodedViewOutcome = (
    ReadyView
    | ViewNoBody
    | ViewDecodeError
    | ViewIncomplete
    | ViewBudgetExceeded
    | ViewUnsupported
    | ViewCancelled
)


def is_decoded_view_outcome(value: object) -> TypeGuard[DecodedViewOutcome]:
    """Narrow an arbitrary value to the exhaustive decoded-view outcome union."""
    return type(value) in {
        ReadyView,
        ViewNoBody,
        ViewDecodeError,
        ViewIncomplete,
        ViewBudgetExceeded,
        ViewUnsupported,
        ViewCancelled,
    }
