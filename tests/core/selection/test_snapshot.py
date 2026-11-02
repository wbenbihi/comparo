"""Integrity tests for closed, I/O-free selection snapshots."""

from __future__ import annotations

from hashlib import sha256
from typing import cast

import msgspec
import pytest

from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderRegistryIdentity
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonString
from comparo.core.selection.snapshot import FailedBodyView
from comparo.core.selection.snapshot import ResolvedBodyBytes
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.stream_factories import single_stream_execution
from tests.core.domain.http.stream_factories import view_handle


def _handle_and_observation(
    data: bytes,
) -> tuple[BodyHandle, EnvironmentExecutionObservation]:
    observation = single_stream_execution(ViewKind.SSE)
    exchange = observation.attempts[0].exchanges[0]
    assert exchange.response is not None
    assert type(exchange.response.body) is ResponseEntityBody
    assert exchange.response.body.handle is not None
    source_digest = exchange.response.body.handle.digest
    assert is_sensitive_value(source_digest)
    typed_source_digest = cast("SensitiveValue[Sha256Digest]", source_digest)
    digest = SensitiveValue(
        msgspec.structs.replace(
            typed_source_digest.reveal(
                access=_access(),
            ),
            hex_value=sha256(data).hexdigest(),
        ),
        origins=(TaintOriginId(value="body-content"),),
    )
    handle = msgspec.structs.replace(
        exchange.response.body.handle,
        byte_count=msgspec.structs.replace(
            exchange.response.body.handle.byte_count,
            value=len(data),
        ),
        digest=digest,
    )
    changed_body = msgspec.structs.replace(
        exchange.response.body,
        handle=handle,
        encoded_byte_count=ByteCount(value=len(data)),
    )
    response = msgspec.structs.replace(
        exchange.response,
        body=changed_body,
    )
    changed_exchange = msgspec.structs.replace(exchange, response=response)
    attempt = msgspec.structs.replace(observation.attempts[0], exchanges=(changed_exchange,))
    changed = msgspec.structs.replace(
        observation,
        attempts=(attempt,),
        views=(),
    )
    return handle, changed


def test_body_bytes_are_bound_to_length_digest_sensitivity_and_owner() -> None:
    data = b"safe bytes"
    handle, observation = _handle_and_observation(data)
    value = SensitiveValue(
        BytesValue(value=data),
        origins=(TaintOriginId(value="body-content"),),
    )

    snapshot = SelectionSnapshot(
        observation=observation,
        body_bytes=(ResolvedBodyBytes(source=handle, value=value),),
    )

    assert snapshot.body_bytes[0].body_id == handle.body_id


def test_body_bytes_reject_substitution_and_duplicate_identity() -> None:
    data = b"safe bytes"
    handle, observation = _handle_and_observation(data)
    valid = SensitiveValue(
        BytesValue(value=data),
        origins=(TaintOriginId(value="body-content"),),
    )
    payload = ResolvedBodyBytes(source=handle, value=valid)

    with pytest.raises(ValueError, match="digest"):
        ResolvedBodyBytes(
            source=handle,
            value=SensitiveValue(
                BytesValue(value=b"other data"),
                origins=(TaintOriginId(value="body-content"),),
            ),
        )
    with pytest.raises(ValueError, match="unique"):
        SelectionSnapshot(observation=observation, body_bytes=(payload, payload))


def test_snapshot_rejects_unowned_body_source() -> None:
    data = b"safe bytes"
    _handle, observation = _handle_and_observation(data)
    unrelated = body_handle(size=len(data))
    unrelated_digest = unrelated.digest
    assert is_sensitive_value(unrelated_digest)
    typed_unrelated_digest = cast("SensitiveValue[Sha256Digest]", unrelated_digest)
    unrelated = msgspec.structs.replace(
        unrelated,
        digest=SensitiveValue(
            msgspec.structs.replace(
                typed_unrelated_digest.reveal(access=_access()),
                hex_value=sha256(data).hexdigest(),
            ),
            origins=(TaintOriginId(value="body-content"),),
        ),
    )
    value = SensitiveValue(
        BytesValue(value=data),
        origins=(TaintOriginId(value="body-content"),),
    )

    with pytest.raises(ValueError, match="differs from its observation descriptor"):
        SelectionSnapshot(
            observation=observation,
            body_bytes=(ResolvedBodyBytes(source=unrelated, value=value),),
        )


def test_snapshot_rejects_same_id_body_descriptor_substitution() -> None:
    data = b"safe bytes"
    handle, observation = _handle_and_observation(data)
    forged = msgspec.structs.replace(handle, extent=BodyCaptureExtent.PARTIAL)
    payload = ResolvedBodyBytes(
        source=forged,
        value=SensitiveValue(
            BytesValue(value=data),
            origins=(TaintOriginId(value="body-content"),),
        ),
    )

    with pytest.raises(ValueError, match="descriptor"):
        SelectionSnapshot(observation=observation, body_bytes=(payload,))


def test_snapshot_rejects_same_id_decoded_handle_downgrade() -> None:
    observation = single_stream_execution(ViewKind.SSE)
    declared = observation.views[0]
    forged = msgspec.structs.replace(
        declared,
        completeness=ViewCompleteness.PREFIX,
    )
    resolved = ResolvedBodyView(
        handle=forged,
        requested_view=BodyViewKind.SSE_EVENTS,
        value=_sensitive(JsonArray(items=())),
        payload_fingerprint=forged.fingerprint,
    )

    with pytest.raises(ValueError, match="declared observation handle"):
        SelectionSnapshot(observation=observation, body_views=(resolved,))


def test_equivalent_sealed_fingerprint_payload_and_origins_are_accepted() -> None:
    observation = single_stream_execution(ViewKind.SSE)
    handle = observation.views[0]
    fingerprint = handle.fingerprint
    assert is_sensitive_value(fingerprint)
    typed = cast("SensitiveValue[Fingerprint]", fingerprint)
    equivalent = SensitiveValue(
        typed.reveal(access=_access()),
        origins=typed.origins,
    )
    resolved = ResolvedBodyView(
        handle=handle,
        requested_view=BodyViewKind.SSE_EVENTS,
        value=_sensitive(JsonArray(items=())),
        payload_fingerprint=equivalent,
    )

    snapshot = SelectionSnapshot(observation=observation, body_views=(resolved,))

    assert snapshot.body_views == (resolved,)


def test_sealed_fingerprint_requires_exact_payload_and_origins() -> None:
    handle = view_handle(ViewKind.SSE)
    fingerprint = handle.fingerprint
    assert is_sensitive_value(fingerprint)
    typed = cast("SensitiveValue[Fingerprint]", fingerprint)
    wrong_origins = SensitiveValue(
        typed.reveal(access=_access()),
        origins=(TaintOriginId(value="different.fingerprint"),),
    )

    with pytest.raises(ValueError, match="fingerprint"):
        ResolvedBodyView(
            handle=handle,
            requested_view=BodyViewKind.SSE_EVENTS,
            value=_sensitive(JsonArray(items=())),
            payload_fingerprint=wrong_origins,
        )


def test_auto_and_explicit_views_bind_provenance_and_decoder_payload_kind() -> None:
    plain = view_handle(ViewKind.JSON)
    automatic = msgspec.structs.replace(
        plain,
        auto_selection=AutoViewSelection(
            registry=DecoderRegistryIdentity(value="comparo/auto@1"),
            decoder=plain.decoder,
        ),
    )
    resolved = ResolvedBodyView(
        handle=automatic,
        requested_view=BodyViewKind.AUTO,
        value=_sensitive(JsonString(value="value")),
        payload_fingerprint=automatic.fingerprint,
    )

    assert resolved.handle is automatic
    with pytest.raises(ValueError, match="require automatic selection"):
        ResolvedBodyView(
            handle=plain,
            requested_view=BodyViewKind.AUTO,
            value=_sensitive(JsonString(value="value")),
            payload_fingerprint=plain.fingerprint,
        )
    with pytest.raises(ValueError, match="explicit body views"):
        ResolvedBodyView(
            handle=automatic,
            requested_view=BodyViewKind.JSON,
            value=_sensitive(JsonString(value="value")),
            payload_fingerprint=automatic.fingerprint,
        )
    with pytest.raises(ValueError, match="JSON views"):
        ResolvedBodyView(
            handle=automatic,
            requested_view=BodyViewKind.AUTO,
            value=_sensitive(BytesValue(value=b"not-json")),
            payload_fingerprint=automatic.fingerprint,
        )


def test_failed_view_binds_exact_source_and_auto_provenance() -> None:
    observation = single_stream_execution(ViewKind.SSE)
    source = observation.views[0].source
    decoder = view_handle(ViewKind.JSON).decoder
    auto_selection = AutoViewSelection(
        registry=DecoderRegistryIdentity(value="comparo/auto@1"),
        decoder=decoder,
    )
    failure = FailedBodyView(
        source=source,
        requested_view=BodyViewKind.AUTO,
        outcome=ViewDecodeError(
            decoder=decoder,
            code=DecodeErrorCode.INVALID_SYNTAX,
            auto_selection=auto_selection,
        ),
    )

    SelectionSnapshot(observation=observation, body_views=(failure,))
    forged_source = msgspec.structs.replace(source, extent=BodyCaptureExtent.PARTIAL)
    forged_failure = msgspec.structs.replace(failure, source=forged_source)
    with pytest.raises(ValueError, match="descriptor"):
        SelectionSnapshot(observation=observation, body_views=(forged_failure,))
    with pytest.raises(ValueError, match="explicit body views"):
        FailedBodyView(
            source=source,
            requested_view=BodyViewKind.JSON,
            outcome=failure.outcome,
        )


def _sensitive[T](value: T) -> SensitiveValue[T]:
    return SensitiveValue(value, origins=(TaintOriginId(value="view-content"),))


def _access() -> SensitiveValueAccess:
    return SensitiveValueAccess(purpose="test.snapshot")
