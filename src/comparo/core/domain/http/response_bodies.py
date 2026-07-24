"""Response media metadata, opaque body handles, and explicit capture states."""

from __future__ import annotations

import re
from math import gcd
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetExhaustionEffect
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import is_sensitive_value

_TOKEN_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


@final
class MissingMediaType(msgspec.Struct, frozen=True, kw_only=True):
    """No Content-Type field was present; automatic selection chooses bytes."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction boundary."""


@final
class ParsedMediaType(msgspec.Struct, frozen=True, kw_only=True):
    """One strictly parsed Content-Type value."""

    value: MediaType

    def __post_init__(self) -> None:
        """Require canonical parsed media metadata."""
        if type(self.value) is not MediaType:
            raise TypeError("parsed response media type must be MediaType")


class InvalidMediaTypeReason(DomainStrEnum):
    """Reasons media metadata is unusable for automatic view selection."""

    MALFORMED = "malformed"
    MULTIPLE_VALUES = "multiple-values"
    CONFLICTING_VALUES = "conflicting-values"


@final
class InvalidMediaType(msgspec.Struct, frozen=True, kw_only=True):
    """Present but invalid Content-Type metadata retained without sniffing."""

    reason: InvalidMediaTypeReason

    def __post_init__(self) -> None:
        """Require one stable media error class."""
        if type(self.reason) is not InvalidMediaTypeReason:
            raise TypeError("invalid media reason must be InvalidMediaTypeReason")


type MediaTypeMetadata = MissingMediaType | ParsedMediaType | InvalidMediaType


def is_media_type_metadata(value: object) -> TypeGuard[MediaTypeMetadata]:
    """Narrow an arbitrary value to an exhaustive media metadata state."""
    return type(value) in {MissingMediaType, ParsedMediaType, InvalidMediaType}


@final
class ContentCoding(msgspec.Struct, frozen=True, kw_only=True):
    """One canonical Content-Encoding token in application order."""

    value: str

    def __post_init__(self) -> None:
        """Require a lowercase HTTP token."""
        if (
            type(self.value) is not str
            or self.value.lower() != self.value
            or _TOKEN_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError("content coding must be a lowercase HTTP token")


class InvalidContentEncodingReason(DomainStrEnum):
    """Reasons Content-Encoding metadata cannot be represented as an ordered coding list."""

    MALFORMED = "malformed"


@final
class BodyStorageToken(msgspec.Struct, frozen=True, kw_only=True):
    """A store token that may appear in a handle only as sealed sensitive data."""

    value: str

    def __post_init__(self) -> None:
        """Require a non-empty store-owned token."""
        if type(self.value) is not str or not self.value:
            raise ValueError("body storage locator must be non-empty text")


type BodyStorageLocator = SensitiveValue[BodyStorageToken]


class BodyCaptureExtent(DomainStrEnum):
    """Whether a BodyHandle addresses a complete entity or a retained prefix."""

    COMPLETE = "complete"
    PARTIAL = "partial"


@final
class BodyHandle(msgspec.Struct, frozen=True, kw_only=True):
    """An opaque descriptor for decoded entity bytes owned by a BodyStore."""

    body_id: BodyId
    locator: BodyStorageLocator
    byte_count: ByteCount
    digest: Sha256Digest | SensitiveValue[Sha256Digest]
    media: MediaTypeMetadata
    sensitivity: Sensitivity
    extent: BodyCaptureExtent

    def __post_init__(self) -> None:
        """Validate descriptor metadata without exposing its locator."""
        if type(self.body_id) is not BodyId:
            raise TypeError("body handle ID must be BodyId")
        if not is_sensitive_value(self.locator):
            raise TypeError("body locator must be a sealed BodyStorageLocator")
        if self.locator.payload_type is not BodyStorageToken:
            raise TypeError("body locator payload must be BodyStorageToken")
        if type(self.byte_count) is not ByteCount:
            raise TypeError("body handle byte count must be ByteCount")
        if not is_media_type_metadata(self.media):
            raise TypeError("body handle media must be MediaTypeMetadata")
        if type(self.sensitivity) is not Sensitivity:
            raise TypeError("body handle sensitivity must be Sensitivity")
        if type(self.extent) is not BodyCaptureExtent:
            raise TypeError("body handle extent must be BodyCaptureExtent")
        if self.sensitivity is Sensitivity.PUBLIC and type(self.digest) is not Sha256Digest:
            raise TypeError("public body digest must be Sha256Digest")
        if self.sensitivity is Sensitivity.SENSITIVE and (
            not is_sensitive_value(self.digest) or self.digest.payload_type is not Sha256Digest
        ):
            raise TypeError("sensitive body digest must remain sealed")


@final
class CompleteCapture(msgspec.Struct, frozen=True, kw_only=True):
    """The complete decoded entity was captured."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction boundary."""


@final
class RemoteTruncatedCapture(msgspec.Struct, frozen=True, kw_only=True):
    """The peer ended an entity before declared framing completed."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction boundary."""


class CaptureBudgetKind(DomainStrEnum):
    """Independent body-capture budget dimensions."""

    ENCODED_BYTES = "encoded-bytes"
    ENTITY_BYTES = "entity-bytes"
    EXPANSION_RATIO = "expansion-ratio"


@final
class BudgetExceededCapture(msgspec.Struct, frozen=True, kw_only=True):
    """Capture stopped with canonical resource-budget exhaustion evidence."""

    exhaustion: BudgetExceeded

    def __post_init__(self) -> None:
        """Bind body capture to the exact supported resource, stage, scope, and effect."""
        if type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("capture budget evidence must be BudgetExceeded")
        budget = self.exhaustion.budget
        expected_stage = {
            BudgetResource.ENCODED_BODY_BYTES: BudgetStage.CAPTURE,
            BudgetResource.ENTITY_BODY_BYTES: BudgetStage.DECODING,
            BudgetResource.DECOMPRESSION_RATIO: BudgetStage.DECODING,
        }.get(budget.resource)
        if expected_stage is None:
            raise ValueError("capture budget resource is not a body-capture dimension")
        if (
            budget.scope_kind is not BudgetScopeKind.BODY
            or type(budget.scope) is not BodyBudgetScope
            or budget.stage is not expected_stage
        ):
            raise ValueError("capture exhaustion requires its exact body scope and stage")
        if self.exhaustion.effect is not BudgetExhaustionEffect.OBSERVATION_PARTIAL:
            raise ValueError("capture exhaustion effect must be observation-partial")

    @property
    def budget(self) -> CaptureBudgetKind:
        """Derive the local capture classification from canonical resource identity."""
        return {
            BudgetResource.ENCODED_BODY_BYTES: CaptureBudgetKind.ENCODED_BYTES,
            BudgetResource.ENTITY_BODY_BYTES: CaptureBudgetKind.ENTITY_BYTES,
            BudgetResource.DECOMPRESSION_RATIO: CaptureBudgetKind.EXPANSION_RATIO,
        }[self.exhaustion.budget.resource]


@final
class StorageFailedCapture(msgspec.Struct, frozen=True, kw_only=True):
    """Capture stopped because the BodyStore failed."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction boundary."""


@final
class CancelledCapture(msgspec.Struct, frozen=True, kw_only=True):
    """Capture stopped at the enclosing cancellation boundary."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction boundary."""


class BodyReadFailureKind(DomainStrEnum):
    """Transport interruptions that stop entity capture after response headers."""

    READ_FAILURE = "read-failure"
    IDLE_TIMEOUT = "idle-timeout"


@final
class BodyReadFailedCapture(msgspec.Struct, frozen=True, kw_only=True):
    """Entity capture stopped during a body read, optionally after retaining a prefix."""

    kind: BodyReadFailureKind

    def __post_init__(self) -> None:
        """Require an exact body-read interruption class."""
        if type(self.kind) is not BodyReadFailureKind:
            raise TypeError("body-read capture failure kind must be BodyReadFailureKind")


@final
class ContentDecodingFailedCapture(msgspec.Struct, frozen=True, kw_only=True):
    """Content coding could not produce a complete decoded entity."""

    def __post_init__(self) -> None:
        """Retain a distinct unavailable-or-partial content-decoding state."""


@final
class PolicyOmittedCapture(msgspec.Struct, frozen=True, kw_only=True):
    """Policy deliberately prevented entity capture."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction boundary."""


@final
class ExplicitlyTruncatedCapture(msgspec.Struct, frozen=True, kw_only=True):
    """Authored configuration deliberately retained a bounded prefix."""

    limit: ByteCount

    def __post_init__(self) -> None:
        """Require the configured prefix limit."""
        if type(self.limit) is not ByteCount:
            raise TypeError("explicit body truncation limit must be ByteCount")


type BodyCaptureState = (
    CompleteCapture
    | RemoteTruncatedCapture
    | BudgetExceededCapture
    | StorageFailedCapture
    | CancelledCapture
    | BodyReadFailedCapture
    | ContentDecodingFailedCapture
    | PolicyOmittedCapture
    | ExplicitlyTruncatedCapture
)


def is_body_capture_state(value: object) -> TypeGuard[BodyCaptureState]:
    """Narrow an arbitrary value to an exhaustive body-capture state."""
    return type(value) in {
        CompleteCapture,
        RemoteTruncatedCapture,
        BudgetExceededCapture,
        StorageFailedCapture,
        CancelledCapture,
        BodyReadFailedCapture,
        ContentDecodingFailedCapture,
        PolicyOmittedCapture,
        ExplicitlyTruncatedCapture,
    }


class NoBodyReason(DomainStrEnum):
    """Protocol reasons a terminal response has no semantic entity body."""

    HEAD_REQUEST = "head-request"
    STATUS_204 = "status-204"
    STATUS_205 = "status-205"
    STATUS_304 = "status-304"
    SWITCHING_PROTOCOLS = "switching-protocols"
    CONNECT_TUNNEL = "connect-tunnel"


@final
class NoResponseBody(msgspec.Struct, frozen=True, kw_only=True):
    """Semantic body absence, distinct from a zero-byte entity."""

    reason: NoBodyReason

    def __post_init__(self) -> None:
        """Require one protocol-defined absence reason."""
        if type(self.reason) is not NoBodyReason:
            raise TypeError("no-body reason must be NoBodyReason")


@final
class ResponseEntityBody(msgspec.Struct, frozen=True, kw_only=True):
    """An entity whose semantic presence is independent from capture completeness."""

    capture: BodyCaptureState
    handle: BodyHandle | None
    encoded_byte_count: ByteCount | None

    def __post_init__(self) -> None:
        """Require handles exactly when captured bytes remain addressable."""
        _validate_response_entity_fields(self)
        _validate_response_entity_extent(self)
        if type(self.capture) is BudgetExceededCapture and self.handle is not None:
            _validate_budget_exceeded_capture(
                self.capture,
                self.handle,
                self.encoded_byte_count,
            )


def _validate_response_entity_fields(body: ResponseEntityBody) -> None:
    """Validate the closed capture state and optional typed byte facts."""
    if not is_body_capture_state(body.capture):
        raise TypeError("entity capture must be BodyCaptureState")
    if body.handle is not None and type(body.handle) is not BodyHandle:
        raise TypeError("entity body handle must be BodyHandle when present")
    if body.encoded_byte_count is not None and type(body.encoded_byte_count) is not ByteCount:
        raise TypeError("encoded body byte count must be ByteCount when known")


def _validate_response_entity_extent(body: ResponseEntityBody) -> None:
    """Reconcile capture outcome with the addressable body's exact extent."""
    capture = body.capture
    handle = body.handle
    if type(capture) is PolicyOmittedCapture and handle is not None:
        raise ValueError("a policy-omitted entity cannot expose a body handle")
    if (
        type(capture)
        in {
            CompleteCapture,
            RemoteTruncatedCapture,
            BudgetExceededCapture,
            ExplicitlyTruncatedCapture,
        }
        and handle is None
    ):
        raise ValueError("captured complete or partial entity bytes require a body handle")
    if (
        type(capture) is ExplicitlyTruncatedCapture
        and handle is not None
        and handle.byte_count.value > capture.limit.value
    ):
        raise ValueError("explicitly truncated bytes cannot exceed the authored limit")
    if type(capture) is CompleteCapture and (
        handle is None or handle.extent is not BodyCaptureExtent.COMPLETE
    ):
        raise ValueError("complete capture requires a complete BodyHandle")
    if type(capture) in {
        RemoteTruncatedCapture,
        BudgetExceededCapture,
        ExplicitlyTruncatedCapture,
    } and (handle is None or handle.extent is not BodyCaptureExtent.PARTIAL):
        raise ValueError("partial capture requires a partial BodyHandle")
    if type(capture) in {
        StorageFailedCapture,
        CancelledCapture,
        BodyReadFailedCapture,
        ContentDecodingFailedCapture,
    } and (handle is not None and handle.extent is not BodyCaptureExtent.PARTIAL):
        raise ValueError("incomplete operational capture cannot expose a complete BodyHandle")


def _validate_budget_exceeded_capture(
    capture: BudgetExceededCapture,
    handle: BodyHandle,
    encoded_byte_count: ByteCount | None,
) -> None:
    """Reconcile canonical exhaustion with the exact captured-body identity and counts."""
    exhaustion = capture.exhaustion
    budget = exhaustion.budget
    scope = budget.scope
    if type(scope) is not BodyBudgetScope or scope.body_id != handle.body_id:
        raise ValueError("capture budget scope must identify the captured body handle")
    if capture.budget is CaptureBudgetKind.ENCODED_BYTES:
        if encoded_byte_count is None or budget.effective != encoded_byte_count:
            raise ValueError("encoded-byte exhaustion must match the retained encoded extent")
        return
    if capture.budget is CaptureBudgetKind.ENTITY_BYTES:
        if budget.effective != handle.byte_count:
            raise ValueError("entity-byte exhaustion must match the retained entity extent")
        return
    if encoded_byte_count is None or encoded_byte_count.value == 0:
        raise ValueError("expansion-ratio exhaustion requires a non-zero encoded extent")
    divisor = gcd(handle.byte_count.value, encoded_byte_count.value)
    observed = Ratio(
        numerator=handle.byte_count.value // divisor,
        denominator=PositiveCount(value=encoded_byte_count.value // divisor),
    )
    if exhaustion.attempted != observed:
        raise ValueError("expansion-ratio exhaustion must match exact encoded and entity extents")


type ResponseBody = NoResponseBody | ResponseEntityBody
