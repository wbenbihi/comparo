"""Immutable, value-safe inputs for pure selector evaluation."""

from __future__ import annotations

from hashlib import sha256
from typing import TypeGuard
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import DecodedViewOutcome
from comparo.core.domain.http.views import ReadyView
from comparo.core.domain.http.views import ViewBudgetExceeded
from comparo.core.domain.http.views import ViewCancelled
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewIncomplete
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import ViewNoBody
from comparo.core.domain.http.views import ViewUnsupported
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.body_view_semantics import validate_requested_body_decoder
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import is_selected_value
from comparo.core.domain.selectors.values import is_selected_value_type
from comparo.core.domain.selectors.values import selected_value_kind_type
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonString

type BodyBytesValue = BytesValue | SensitiveValue[BytesValue]

_INTEGRITY_ACCESS = SensitiveValueAccess(purpose="selector.snapshot.integrity")


@final
class SelectionSnapshotRequirements(msgspec.Struct, frozen=True, kw_only=True):
    """Minimum storage-backed semantic material needed by configured selectors.

    Raw HTTP capture remains independent of this contract.  The requirements only control which
    values a snapshot provider must materialize into the I/O-free selection snapshot.
    """

    body_views: tuple[BodyViewKind, ...] = ()
    stream: bool = False

    def __post_init__(self) -> None:
        """Require a unique immutable set of materialized views and an exact stream flag."""
        if type(self.body_views) is not tuple or any(
            type(item) is not BodyViewKind for item in self.body_views
        ):
            raise TypeError("snapshot body-view requirements must be an immutable typed tuple")
        if len(set(self.body_views)) != len(self.body_views):
            raise ValueError("snapshot body-view requirements must be unique")
        if type(self.stream) is not bool:
            raise TypeError("snapshot stream requirement must be bool")


@final
class ResolvedBodyBytes(msgspec.Struct, frozen=True, kw_only=True):
    """Bytes already fetched from a BodyStore, bound to their opaque body identity."""

    source: BodyHandle | RepeatableByteSource
    value: BodyBytesValue

    def __post_init__(self) -> None:
        """Require exact immutable bytes without weakening sensitive containment."""
        if type(self.source) not in {BodyHandle, RepeatableByteSource}:
            raise TypeError("resolved body bytes require an exact source descriptor")
        if type(self.value) is not BytesValue and not is_sensitive_value(self.value):
            raise TypeError("resolved body bytes require BytesValue containment")
        if is_sensitive_value(self.value) and self.value.payload_type is not BytesValue:
            raise TypeError("sensitive resolved body bytes must contain BytesValue")
        _validate_body_bytes_integrity(self)

    @property
    def body_id(self) -> BodyId:
        """Return the source identity without exposing a storage locator."""
        return self.source.body_id if type(self.source) is BodyHandle else self.source.source_id


def _validate_body_bytes_integrity(payload: ResolvedBodyBytes) -> None:
    value = _body_bytes_value(payload.value, _INTEGRITY_ACCESS)
    digest = payload.source.digest
    expected = _body_digest_value(digest, _INTEGRITY_ACCESS)
    expected_sensitive = (
        payload.source.sensitivity is Sensitivity.SENSITIVE
        if type(payload.source) is BodyHandle
        else is_sensitive_value(digest)
    )
    if len(value.value) != payload.source.byte_count.value:
        raise ValueError("resolved body bytes length contradicts its source descriptor")
    if sha256(value.value).hexdigest() != expected.hex_value:
        raise ValueError("resolved body bytes digest contradicts its source descriptor")
    if is_sensitive_value(payload.value) != expected_sensitive:
        raise ValueError("resolved body bytes must preserve source sensitivity")
    if (
        is_sensitive_value(payload.value)
        and is_sensitive_value(digest)
        and payload.value.origins != digest.origins
    ):
        raise ValueError("resolved body bytes must preserve source taint origins")


def _body_bytes_value(
    value: BodyBytesValue,
    access: SensitiveValueAccess,
) -> BytesValue:
    if type(value) is BytesValue:
        return value
    return value.reveal(access=access)


def _body_digest_value(
    value: Sha256Digest | SensitiveValue[Sha256Digest],
    access: SensitiveValueAccess,
) -> Sha256Digest:
    if type(value) is Sha256Digest:
        return value
    return value.reveal(access=access)


type ResolvedViewValue = SelectedValue | SensitiveValue[SelectedValue]


@final
class ResolvedBodyView(msgspec.Struct, frozen=True, kw_only=True):
    """A store-resolved semantic view payload with exact handle provenance."""

    handle: DecodedViewHandle
    requested_view: BodyViewKind
    value: ResolvedViewValue
    payload_fingerprint: Fingerprint | SensitiveValue[Fingerprint]

    def __post_init__(self) -> None:
        """Bind payload kind, completeness, and sensitivity to its decoded handle."""
        if type(self.handle) is not DecodedViewHandle:
            raise TypeError("resolved body view requires a DecodedViewHandle")
        if type(self.requested_view) is not BodyViewKind:
            raise TypeError("resolved body view requires a BodyViewKind")
        if self.requested_view in {BodyViewKind.HANDLE, BodyViewKind.ENTITY_BYTES}:
            raise ValueError("handle and byte selections are not decoded body views")
        validate_requested_body_decoder(
            self.requested_view,
            self.handle.decoder,
            self.handle.auto_selection,
        )
        _validate_view_value(self)
        if not _fingerprints_equal(self.payload_fingerprint, self.handle.fingerprint):
            raise ValueError("resolved view payload proof contradicts its handle fingerprint")


def _validate_view_value(view: ResolvedBodyView) -> None:
    value = view.value
    sensitive = is_sensitive_value(value)
    valid, payload_type = _view_value_type(value)
    if not valid:
        raise TypeError("resolved body view has an unsupported payload")
    if sensitive != (view.handle.sensitivity is Sensitivity.SENSITIVE):
        raise ValueError("resolved body view must preserve handle sensitivity")
    decoder_view = view.handle.decoder.view
    if decoder_view is ViewKind.BYTES and payload_type is not BytesValue:
        raise ValueError("bytes decoders require a BytesValue payload")
    if decoder_view is ViewKind.TEXT and payload_type is not JsonString:
        raise ValueError("text views require a JsonString payload")
    if decoder_view is ViewKind.JSON and (
        selected_value_kind_type(payload_type) not in JSON_VALUE_KINDS
    ):
        raise ValueError("JSON views require an exact JSON payload")
    if (
        decoder_view in {ViewKind.SSE, ViewKind.NDJSON, ViewKind.JSON_SEQUENCE}
        and payload_type is not JsonArray
    ):
        raise ValueError("whole stream views require a JsonArray payload")


def _fingerprints_equal(
    supplied: Fingerprint | SensitiveValue[Fingerprint],
    expected: Fingerprint | SensitiveValue[Fingerprint],
) -> bool:
    """Compare sealed fingerprint payload and lineage without rendering either value."""
    if type(supplied) is Fingerprint or type(expected) is Fingerprint:
        return (
            type(supplied) is Fingerprint and type(expected) is Fingerprint and supplied == expected
        )
    if not is_sensitive_value(supplied) or not is_sensitive_value(expected):
        return False
    if supplied.payload_type is not Fingerprint or expected.payload_type is not Fingerprint:
        return False
    left = cast("SensitiveValue[Fingerprint]", supplied)
    right = cast("SensitiveValue[Fingerprint]", expected)
    return left.origins == right.origins and left.reveal(access=_INTEGRITY_ACCESS) == right.reveal(
        access=_INTEGRITY_ACCESS
    )


def _view_value_type(value: ResolvedViewValue) -> tuple[bool, type[object]]:
    if is_sensitive_value(value):
        return is_selected_value_type(value.payload_type), value.payload_type
    return is_selected_value(value), type(value)


type FailedViewOutcome = (
    ViewNoBody
    | ViewDecodeError
    | ViewIncomplete
    | ViewBudgetExceeded
    | ViewUnsupported
    | ViewCancelled
)


@final
class FailedBodyView(msgspec.Struct, frozen=True, kw_only=True):
    """One already-computed, non-ready decoded-view outcome bound to a source body."""

    source: BodyHandle | RepeatableByteSource
    requested_view: BodyViewKind
    outcome: FailedViewOutcome

    def __post_init__(self) -> None:
        """Reject ready outcomes and require the requested decoder family."""
        if type(self.source) not in {BodyHandle, RepeatableByteSource}:
            raise TypeError("failed body view requires an exact source descriptor")
        if type(self.requested_view) is not BodyViewKind:
            raise TypeError("failed body view requires a BodyViewKind")
        if self.requested_view in {BodyViewKind.HANDLE, BodyViewKind.ENTITY_BYTES}:
            raise ValueError("handle and byte selections are not decoded body views")
        if type(self.outcome) not in {
            ViewNoBody,
            ViewDecodeError,
            ViewIncomplete,
            ViewBudgetExceeded,
            ViewUnsupported,
            ViewCancelled,
        }:
            raise TypeError("failed body view requires a non-ready view outcome")
        validate_requested_body_decoder(
            self.requested_view,
            self.outcome.decoder,
            self.outcome.auto_selection,
        )

    @property
    def body_id(self) -> BodyId:
        """Return the exact source body identity used for snapshot lookup."""
        return self.source.body_id if type(self.source) is BodyHandle else self.source.source_id


type BodyViewSnapshot = ResolvedBodyView | FailedBodyView


@final
class SelectionSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    """A closed, I/O-free observation plus values already obtained through store ports."""

    observation: EnvironmentExecutionObservation
    body_bytes: tuple[ResolvedBodyBytes, ...] = ()
    body_views: tuple[BodyViewSnapshot, ...] = ()
    stream: StreamObservation | None = None
    stream_items: tuple[StreamItemEnvelope, ...] = ()

    def __post_init__(self) -> None:
        """Bind every supplied value to one body or stream owned by the observation."""
        if type(self.observation) is not EnvironmentExecutionObservation:
            raise TypeError("selection snapshot requires an EnvironmentExecutionObservation")
        if type(self.body_bytes) is not tuple or any(
            type(item) is not ResolvedBodyBytes for item in self.body_bytes
        ):
            raise TypeError("snapshot body bytes must be an immutable typed tuple")
        if type(self.body_views) is not tuple or any(
            type(item) not in {ResolvedBodyView, FailedBodyView} for item in self.body_views
        ):
            raise TypeError("snapshot body views must be an immutable typed tuple")
        _validate_unique_payloads(self)
        _validate_owned_bodies(self)
        _validate_stream(self)


def _validate_unique_payloads(snapshot: SelectionSnapshot) -> None:
    body_ids = tuple(item.body_id for item in snapshot.body_bytes)
    if len(set(body_ids)) != len(body_ids):
        raise ValueError("snapshot body-byte identities must be unique")
    view_keys = tuple(_view_key(item) for item in snapshot.body_views)
    if len(set(view_keys)) != len(view_keys):
        raise ValueError("snapshot body-view identities must be unique")


def _view_key(view: BodyViewSnapshot) -> tuple[BodyId, BodyViewKind]:
    body_id = view.handle.body_id if type(view) is ResolvedBodyView else view.body_id
    return body_id, view.requested_view


def _owned_body_sources(
    observation: EnvironmentExecutionObservation,
) -> tuple[BodyHandle | RepeatableByteSource, ...]:
    sources: list[BodyHandle | RepeatableByteSource] = []
    for attempt in observation.attempts:
        for exchange in attempt.exchanges:
            request_body = exchange.request.request.body
            if type(request_body) is MaterializedBody:
                sources.append(request_body.source)
            response = exchange.response
            if (
                response is not None
                and type(response.body) is ResponseEntityBody
                and response.body.handle is not None
            ):
                sources.append(response.body.handle)
        if attempt.pre_send is not None:
            pre_send_request = _pre_send_request(attempt.pre_send)
            if type(pre_send_request.body) is MaterializedBody:
                sources.append(pre_send_request.body.source)
    return tuple(sources)


def _validate_owned_bodies(snapshot: SelectionSnapshot) -> None:
    owned_sources = _owned_body_sources(snapshot.observation)
    supplied_sources = (
        *(item.source for item in snapshot.body_bytes),
        *(
            item.handle.source if type(item) is ResolvedBodyView else item.source
            for item in snapshot.body_views
        ),
    )
    if any(source not in owned_sources for source in supplied_sources):
        raise ValueError("snapshot payload source differs from its observation descriptor")
    declared_views = snapshot.observation.views
    if any(
        type(item) is ResolvedBodyView and item.handle not in declared_views
        for item in snapshot.body_views
    ):
        raise ValueError("resolved body view handle differs from its declared observation handle")


def _validate_stream(snapshot: SelectionSnapshot) -> None:
    if snapshot.stream is None:
        if snapshot.stream_items:
            raise ValueError("stream items require a stream observation")
        return
    if type(snapshot.stream) is not StreamObservation:
        raise TypeError("snapshot stream must be a StreamObservation")
    if snapshot.stream.records.owner != snapshot.observation:
        raise ValueError("snapshot stream belongs to a different execution observation")
    if type(snapshot.stream_items) is not tuple:
        raise TypeError("snapshot stream items must be an immutable tuple")
    snapshot.stream.validate_item_envelopes(snapshot.stream_items)
    expected = (
        snapshot.stream.records.record_count.value
        + snapshot.stream.records.control_count.value
        + snapshot.stream.records.error_count.value
    )
    if len(snapshot.stream_items) != expected:
        raise ValueError("snapshot stream items must cover the complete closed stream index")
    indexes = tuple(envelope.item.location.item_index.value for envelope in snapshot.stream_items)
    if indexes != tuple(range(expected)):
        raise ValueError("snapshot stream item indexes must be contiguous from zero")


def _pre_send_request(outcome: HttpPreSendOutcome) -> FinalizedWireRequest:
    if type(outcome) is HttpResolutionFailure:
        return outcome.request.request
    return outcome.request


def body_bytes_for(snapshot: SelectionSnapshot, body_id: BodyId) -> ResolvedBodyBytes | None:
    """Return one exact pre-resolved body payload without touching a store."""
    return next((item for item in snapshot.body_bytes if item.body_id == body_id), None)


def body_view_for(
    snapshot: SelectionSnapshot,
    body_id: BodyId,
    requested_view: BodyViewKind,
) -> BodyViewSnapshot | None:
    """Return one exact pre-resolved view outcome without touching a decoder or store."""
    return next(
        (item for item in snapshot.body_views if _view_key(item) == (body_id, requested_view)),
        None,
    )


def is_failed_view_outcome(value: object) -> TypeGuard[FailedViewOutcome]:
    """Narrow already-computed view failures at evaluator boundaries."""
    return type(value) in {
        ViewNoBody,
        ViewDecodeError,
        ViewIncomplete,
        ViewBudgetExceeded,
        ViewUnsupported,
        ViewCancelled,
    }


def decoded_outcome(view: BodyViewSnapshot) -> DecodedViewOutcome:
    """Expose a standard view outcome for adapters that only need resolution state."""
    if type(view) is FailedBodyView:
        return view.outcome
    return ReadyView(handle=view.handle, completeness=ViewCompleteness(view.handle.completeness))
