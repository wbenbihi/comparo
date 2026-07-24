"""Tests for response capture, transport metadata, and observation hierarchy semantics."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import BodyStorageToken
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CaptureBudgetKind
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ContentCoding
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import ExplicitlyTruncatedCapture
from comparo.core.domain.http.response_bodies import InvalidMediaType
from comparo.core.domain.http.response_bodies import InvalidMediaTypeReason
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ParsedMediaType
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import RemoteTruncatedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.budget_factories import capture_exhaustion
from tests.core.domain.http.factories import digest
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response


def test_no_body_is_distinct_from_a_complete_empty_entity() -> None:
    effective_request = request()
    empty_entity = response()
    observed = exchange(
        exchange_number=1,
        effective_request=effective_request,
        observed_response=empty_entity,
    )
    assert observed.response is not None
    assert isinstance(observed.response.body, ResponseEntityBody)
    assert observed.response.body.handle is not None
    assert observed.response.body.handle.byte_count.value == 0

    no_body = response(body=NoResponseBody(reason=NoBodyReason.STATUS_204), status=204)
    assert (
        exchange(
            exchange_number=2,
            effective_request=effective_request,
            observed_response=no_body,
        ).response
        is no_body
    )
    with pytest.raises(ValueError, match="NoBody reason"):
        exchange(
            exchange_number=3,
            effective_request=effective_request,
            observed_response=response(body=NoResponseBody(reason=NoBodyReason.STATUS_204)),
        )


def test_body_forbidden_bytes_survive_only_with_protocol_failure() -> None:
    effective_request = request()
    unexpected = response(status=204)
    with pytest.raises(ValueError, match="protocol failure"):
        exchange(
            exchange_number=1,
            effective_request=effective_request,
            observed_response=unexpected,
        )
    partial = exchange(
        exchange_number=2,
        effective_request=effective_request,
        observed_response=unexpected,
        failure=failure(FailurePhase.PROTOCOL),
    )
    assert partial.response is not None
    assert type(partial.response.body) is ResponseEntityBody
    assert type(unexpected.body) is ResponseEntityBody
    assert partial.response.body.capture == unexpected.body.capture
    assert partial.failure is not None


def test_capture_states_keep_policy_omission_and_partial_handles_explicit() -> None:
    complete_handle = body_handle(size=4)
    partial_handle = body_handle(size=4, extent=BodyCaptureExtent.PARTIAL)
    assert (
        ResponseEntityBody(
            capture=CompleteCapture(),
            handle=complete_handle,
            encoded_byte_count=ByteCount(value=4),
        ).handle
        is complete_handle
    )
    for state in (
        RemoteTruncatedCapture(),
        BudgetExceededCapture(
            exhaustion=capture_exhaustion(
                handle=partial_handle,
                kind=CaptureBudgetKind.ENTITY_BYTES,
                encoded_byte_count=ByteCount(value=4),
            )
        ),
        ExplicitlyTruncatedCapture(limit=ByteCount(value=4)),
    ):
        assert (
            ResponseEntityBody(
                capture=state,
                handle=partial_handle,
                encoded_byte_count=ByteCount(value=4),
            ).handle
            is partial_handle
        )
    assert (
        ResponseEntityBody(
            capture=StorageFailedCapture(),
            handle=None,
            encoded_byte_count=None,
        ).handle
        is None
    )
    assert (
        ResponseEntityBody(
            capture=CancelledCapture(),
            handle=None,
            encoded_byte_count=None,
        ).handle
        is None
    )
    for operational_state in (
        BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
        ContentDecodingFailedCapture(),
    ):
        assert (
            ResponseEntityBody(
                capture=operational_state,
                handle=partial_handle,
                encoded_byte_count=ByteCount(value=4),
            ).handle
            is partial_handle
        )
    assert (
        ResponseEntityBody(
            capture=PolicyOmittedCapture(),
            handle=None,
            encoded_byte_count=None,
        ).handle
        is None
    )

    with pytest.raises(ValueError, match="require a body handle"):
        ResponseEntityBody(capture=CompleteCapture(), handle=None, encoded_byte_count=None)
    with pytest.raises(ValueError, match="cannot expose"):
        ResponseEntityBody(
            capture=PolicyOmittedCapture(),
            handle=complete_handle,
            encoded_byte_count=None,
        )
    with pytest.raises(ValueError, match="cannot exceed"):
        ResponseEntityBody(
            capture=ExplicitlyTruncatedCapture(limit=ByteCount(value=3)),
            handle=partial_handle,
            encoded_byte_count=ByteCount(value=4),
        )
    with pytest.raises(ValueError, match="complete BodyHandle"):
        ResponseEntityBody(
            capture=CompleteCapture(),
            handle=partial_handle,
            encoded_byte_count=ByteCount(value=4),
        )
    with pytest.raises(ValueError, match="partial BodyHandle"):
        ResponseEntityBody(
            capture=RemoteTruncatedCapture(),
            handle=complete_handle,
            encoded_byte_count=ByteCount(value=4),
        )


def test_body_storage_locator_is_opaque_and_nonserializable() -> None:
    handle = body_handle()
    assert "private/body/path" not in repr(handle)
    with pytest.raises(TypeError) as captured:
        msgspec.json.encode(handle)
    assert "private/body/path" not in str(captured.value)


def test_sensitive_body_content_requires_a_sealed_content_derived_digest() -> None:
    locator = SensitiveValue(
        BodyStorageToken(value="private/body/path"),
        origins=(TaintOriginId(value="body-store"),),
    )
    with pytest.raises(TypeError, match="must remain sealed"):
        BodyHandle(
            body_id=BodyId(value=UUID(int=10)),
            locator=locator,
            byte_count=ByteCount(value=1),
            digest=digest("e"),
            media=MissingMediaType(),
            sensitivity=Sensitivity.SENSITIVE,
            extent=BodyCaptureExtent.COMPLETE,
        )


def test_body_media_capture_and_handle_components_validate_direct_construction() -> None:
    media = ParsedMediaType(value=MediaType(type="application", subtype="json"))
    assert media.value.subtype == "json"
    with pytest.raises(TypeError, match="parsed response"):
        ParsedMediaType(value=cast("MediaType", "application/json"))
    with pytest.raises(TypeError, match="invalid media reason"):
        InvalidMediaType(reason=cast("InvalidMediaTypeReason", "bad"))
    with pytest.raises(ValueError, match="lowercase HTTP token"):
        ContentCoding(value="GZip")
    with pytest.raises(ValueError, match="lowercase HTTP token"):
        ContentCoding(value=cast("str", 1))
    with pytest.raises(ValueError, match="lowercase HTTP token"):
        ContentCoding(value="not valid")
    assert ContentCoding(value="gzip").value == "gzip"
    with pytest.raises(ValueError, match="non-empty"):
        BodyStorageToken(value="")
    with pytest.raises(TypeError, match="capture budget"):
        BudgetExceededCapture(exhaustion=cast("BudgetExceeded", "entity"))
    with pytest.raises(TypeError, match="truncation limit"):
        ExplicitlyTruncatedCapture(limit=cast("ByteCount", 4))
    with pytest.raises(TypeError, match="body-read capture failure"):
        BodyReadFailedCapture(kind=cast("BodyReadFailureKind", "read"))
    with pytest.raises(TypeError, match="no-body reason"):
        NoResponseBody(reason=cast("NoBodyReason", "none"))

    valid = body_handle()
    with pytest.raises(TypeError, match="body handle ID"):
        msgspec.structs.replace(valid, body_id=cast("BodyId", "body"))
    with pytest.raises(TypeError, match="sealed BodyStorageLocator"):
        msgspec.structs.replace(
            valid,
            locator=cast("SensitiveValue[BodyStorageToken]", BodyStorageToken(value="raw")),
        )
    wrong_locator = SensitiveValue(
        HeaderText(value="wrong"),
        origins=(TaintOriginId(value="wrong-body-locator"),),
    )
    with pytest.raises(TypeError, match="locator payload"):
        BodyHandle(
            body_id=valid.body_id,
            locator=cast("SensitiveValue[BodyStorageToken]", wrong_locator),
            byte_count=valid.byte_count,
            digest=valid.digest,
            media=valid.media,
            sensitivity=valid.sensitivity,
            extent=valid.extent,
        )
    with pytest.raises(TypeError, match="byte count"):
        msgspec.structs.replace(valid, byte_count=cast("ByteCount", 0))
    with pytest.raises(TypeError, match="media"):
        msgspec.structs.replace(valid, media=cast("MissingMediaType", "missing"))
    with pytest.raises(TypeError, match="sensitivity"):
        msgspec.structs.replace(valid, sensitivity=cast("Sensitivity", "sensitive"))
    with pytest.raises(TypeError, match="extent"):
        msgspec.structs.replace(valid, extent=cast("BodyCaptureExtent", "complete"))
    public_locator = SensitiveValue(
        BodyStorageToken(value="public/path"),
        origins=(TaintOriginId(value="body-store"),),
    )
    with pytest.raises(TypeError, match="public body digest"):
        BodyHandle(
            body_id=valid.body_id,
            locator=public_locator,
            byte_count=valid.byte_count,
            digest=valid.digest,
            media=valid.media,
            sensitivity=Sensitivity.PUBLIC,
            extent=valid.extent,
        )
    with pytest.raises(TypeError, match="capture must"):
        ResponseEntityBody(
            capture=cast("CompleteCapture", "complete"),
            handle=valid,
            encoded_byte_count=ByteCount(value=0),
        )
    with pytest.raises(TypeError, match="handle must"):
        ResponseEntityBody(
            capture=StorageFailedCapture(),
            handle=cast("BodyHandle", "body"),
            encoded_byte_count=None,
        )
    with pytest.raises(TypeError, match="encoded body"):
        ResponseEntityBody(
            capture=StorageFailedCapture(),
            handle=None,
            encoded_byte_count=cast("ByteCount", 0),
        )
    with pytest.raises(ValueError, match="incomplete operational capture"):
        ResponseEntityBody(
            capture=StorageFailedCapture(),
            handle=valid,
            encoded_byte_count=ByteCount(value=0),
        )
