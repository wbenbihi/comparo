"""Tests for response capture, transport metadata, and observation hierarchy semantics."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.http.response_bodies import BodyStorageToken
from comparo.core.domain.http.response_bodies import ContentCoding
from comparo.core.domain.http.response_bodies import InvalidContentEncodingReason
from comparo.core.domain.http.response_bodies import InvalidMediaType
from comparo.core.domain.http.response_bodies import InvalidMediaTypeReason
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ParsedMediaType
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_metadata import _decode_quoted
from comparo.core.domain.http.response_metadata import _parse_media_type
from comparo.core.domain.http.response_metadata import derive_content_encodings
from comparo.core.domain.http.response_metadata import derive_media_type
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.responses import InformationalResponse
from comparo.core.domain.http.responses import ReasonPhrase
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.transport import EndpointMetadata
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsUnavailable
from comparo.core.domain.http.transport import TlsUnavailableReason
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import policies
from tests.core.domain.http.observation_factories import endpoint
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response


def response_fields(*values: tuple[str, HeaderText | SensitiveValue[HeaderText]]) -> ResponseFields:
    """Build ordered response fields for metadata-derivation tests."""
    return ResponseFields(
        fields=tuple(
            ResponseField(name=HeaderName(value=name), value=value) for name, value in values
        )
    )


def test_response_media_retains_decoded_quoted_parameter_values() -> None:
    parsed = ParsedMediaType(
        value=MediaType(
            type="application",
            subtype="json",
            parameters=(
                MediaTypeParameter(
                    name="profile",
                    value=MediaTypeQuotedValue(value='https://example.test/a "profile"'),
                ),
            ),
        )
    )
    rendered = render_media_type(parsed.value)
    assert rendered.value == 'application/json; profile="https://example.test/a \\"profile\\""'


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("application", InvalidMediaTypeReason.MALFORMED),
        ('application/json; profile="dangling', InvalidMediaTypeReason.MALFORMED),
        ('application/json; profile="bad\\"', InvalidMediaTypeReason.MALFORMED),
        ("application/json; profile", InvalidMediaTypeReason.MALFORMED),
        ('application/json; profile="bad"quote"', InvalidMediaTypeReason.MALFORMED),
        ("application/json; bad name=value", InvalidMediaTypeReason.MALFORMED),
        ("application/json; charset=utf-8; charset=ascii", InvalidMediaTypeReason.MALFORMED),
    ],
)
def test_response_media_is_strictly_derived_from_fields(
    raw: str,
    reason: InvalidMediaTypeReason,
) -> None:
    derived = derive_media_type(response_fields(("content-type", HeaderText(value=raw))))
    assert derived == InvalidMediaType(reason=reason)


def test_response_media_derivation_handles_quotes_duplicates_and_sensitive_values() -> None:
    parsed = derive_media_type(
        response_fields(
            (
                "content-type",
                HeaderText(value='Application/JSON; profile="a\\"b;c"; charset=utf-8'),
            )
        )
    )
    assert type(parsed) is ParsedMediaType
    assert parsed.value.type == "application"
    assert len(parsed.value.parameters) == 2

    conflicting = derive_media_type(
        response_fields(
            ("content-type", HeaderText(value="application/json")),
            ("content-type", HeaderText(value="text/plain")),
        )
    )
    assert conflicting == InvalidMediaType(reason=InvalidMediaTypeReason.CONFLICTING_VALUES)
    sealed = SensitiveValue(
        HeaderText(value="application/json"),
        origins=(TaintOriginId(value="sealed-media"),),
    )
    assert derive_media_type(response_fields(("content-type", sealed))) == InvalidMediaType(
        reason=InvalidMediaTypeReason.MALFORMED
    )
    assert _decode_quoted("token") is None
    assert _decode_quoted('"a"b"') is None
    assert _decode_quoted('"a\\' + '"') is None
    assert _parse_media_type('application/json; profile="a"junk') is None


def test_content_encoding_is_derived_in_wire_order_or_marked_malformed() -> None:
    fields = response_fields(
        ("content-encoding", HeaderText(value="GZIP, br")),
        ("content-encoding", HeaderText(value="zstd")),
    )
    assert derive_content_encodings(fields) == (
        (
            ContentCoding(value="gzip"),
            ContentCoding(value="br"),
            ContentCoding(value="zstd"),
        ),
        None,
    )
    sealed = SensitiveValue(
        HeaderText(value="gzip"),
        origins=(TaintOriginId(value="sealed-coding"),),
    )
    malformed_fields = (
        response_fields(("content-encoding", sealed)),
        response_fields(("content-encoding", HeaderText(value="gzip,"))),
        response_fields(("content-encoding", HeaderText(value="bad coding"))),
    )
    assert all(
        derive_content_encodings(fields) == ((), InvalidContentEncodingReason.MALFORMED)
        for fields in malformed_fields
    )


def test_response_rejects_independently_claimed_media_and_content_coding() -> None:
    valid = response()
    assert type(valid.body) is ResponseEntityBody
    assert valid.body.handle is not None
    claimed_media = ParsedMediaType(value=MediaType(type="text", subtype="plain"))
    with pytest.raises(ValueError, match="derived from Content-Type"):
        msgspec.structs.replace(
            valid,
            media=claimed_media,
            body=msgspec.structs.replace(
                valid.body,
                handle=msgspec.structs.replace(valid.body.handle, media=claimed_media),
            ),
        )
    with pytest.raises(ValueError, match="derived from Content-Encoding"):
        msgspec.structs.replace(valid, content_encodings=(ContentCoding(value="gzip"),))


def test_identity_content_coding_preserves_exact_entity_extent() -> None:
    valid = response()
    assert type(valid.body) is ResponseEntityBody
    assert valid.body.handle is not None
    headers = response_fields(("content-encoding", HeaderText(value="identity")))
    coding = (ContentCoding(value="identity"),)
    matching_body = msgspec.structs.replace(
        valid.body,
        handle=msgspec.structs.replace(
            valid.body.handle,
            byte_count=ByteCount(value=4),
        ),
        encoded_byte_count=ByteCount(value=4),
    )
    identity = msgspec.structs.replace(
        valid,
        headers=headers,
        content_encodings=coding,
        body=matching_body,
    )
    assert identity.body is matching_body
    with pytest.raises(ValueError, match="encoded and entity byte counts"):
        msgspec.structs.replace(
            identity,
            body=msgspec.structs.replace(
                matching_body,
                encoded_byte_count=ByteCount(value=3),
            ),
        )


def test_set_cookie_requires_sealed_header_text_and_never_echoes_nested_value() -> None:
    secret_cookie = "session=security-sentinel"
    with pytest.raises(ValueError, match="sealed sensitive"):
        ResponseField(name=HeaderName(value="set-cookie"), value=HeaderText(value=secret_cookie))

    field = ResponseField(
        name=HeaderName(value="set-cookie"),
        value=SensitiveValue(
            HeaderText(value=secret_cookie),
            origins=(TaintOriginId(value="response-cookie"),),
        ),
    )
    nested = ResponseFields(fields=(field,))
    assert secret_cookie not in repr(nested)
    with pytest.raises(TypeError) as captured:
        msgspec.json.encode(nested)
    assert secret_cookie not in str(captured.value)


def test_response_preserves_duplicate_fields_informationals_and_invalid_media() -> None:
    fields = ResponseFields(
        fields=(
            ResponseField(
                name=HeaderName(value="set-cookie"),
                value=SensitiveValue(
                    HeaderText(value="a=1"),
                    origins=(TaintOriginId(value="cookie-a"),),
                ),
            ),
            ResponseField(
                name=HeaderName(value="set-cookie"),
                value=SensitiveValue(
                    HeaderText(value="b=2"),
                    origins=(TaintOriginId(value="cookie-b"),),
                ),
            ),
            ResponseField(
                name=HeaderName(value="content-type"),
                value=HeaderText(value="application/json"),
            ),
            ResponseField(
                name=HeaderName(value="content-type"),
                value=HeaderText(value="application/json"),
            ),
        )
    )
    invalid_media = InvalidMediaType(reason=InvalidMediaTypeReason.MULTIPLE_VALUES)
    observed = ResponseObservation(
        protocol=HttpProtocol.HTTP_2,
        status=HttpStatus(value=500),
        reason=None,
        informational=(
            InformationalResponse(
                protocol=HttpProtocol.HTTP_2,
                status=HttpStatus(value=103),
            ),
        ),
        headers=fields,
        trailers=ResponseFields(),
        media=invalid_media,
        content_encodings=(),
        body=ResponseEntityBody(
            capture=PolicyOmittedCapture(),
            handle=None,
            encoded_byte_count=None,
        ),
        endpoint=endpoint(),
        tls=TlsLayers(
            connect=TlsUnavailable(
                reason=TlsUnavailableReason.INSTRUMENTATION_UNSUPPORTED,
                applied_policy=policies().tls,
            )
        ),
    )
    assert observed.headers.fields == fields.fields
    assert observed.status.value == 500


def test_response_scalar_and_collection_components_validate_direct_construction() -> None:
    with pytest.raises(TypeError, match="status must be an integer"):
        HttpStatus(value=cast("int", "200"))
    with pytest.raises(ValueError, match="between 100 and 599"):
        HttpStatus(value=99)
    with pytest.raises(ValueError, match="reason phrase"):
        ReasonPhrase(value="bad\nreason")
    with pytest.raises(ValueError, match="reason phrase"):
        ReasonPhrase(value="bad\N{EURO SIGN}")
    assert ReasonPhrase(value="OK\t").value == "OK\t"
    with pytest.raises(TypeError, match="field name"):
        ResponseField(
            name=cast("HeaderName", "content-type"),
            value=HeaderText(value="application/json"),
        )
    with pytest.raises(TypeError, match="field value"):
        ResponseField(
            name=HeaderName(value="content-type"),
            value=cast("HeaderText", "application/json"),
        )
    wrong_sensitive = SensitiveValue(
        BodyStorageToken(value="wrong"),
        origins=(TaintOriginId(value="wrong-header"),),
    )
    with pytest.raises(TypeError, match="payload must be HeaderText"):
        ResponseField(
            name=HeaderName(value="x-value"),
            value=cast("SensitiveValue[HeaderText]", wrong_sensitive),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        ResponseFields(fields=cast("tuple[ResponseField, ...]", []))
    with pytest.raises(ValueError, match="1xx"):
        InformationalResponse(protocol=HttpProtocol.HTTP_2, status=HttpStatus(value=200))
    with pytest.raises(TypeError, match="informational protocol"):
        InformationalResponse(
            protocol=cast("HttpProtocol", "h2"),
            status=HttpStatus(value=103),
        )
    with pytest.raises(ValueError, match="other than 101"):
        InformationalResponse(protocol=HttpProtocol.HTTP_1_1, status=HttpStatus(value=101))
    with pytest.raises(TypeError, match="informational headers"):
        InformationalResponse(
            protocol=HttpProtocol.HTTP_1_1,
            status=HttpStatus(value=103),
            headers=cast("ResponseFields", ()),
        )
    with pytest.raises(ValueError, match="only 101"):
        response(status=103)
    assert (
        response(
            status=101,
            body=NoResponseBody(reason=NoBodyReason.SWITCHING_PROTOCOLS),
            scheme=HttpScheme.HTTP,
        ).status.value
        == 101
    )
    with pytest.raises(ValueError, match="NoBody reason"):
        exchange(
            exchange_number=101,
            effective_request=request(scheme=HttpScheme.HTTP),
            observed_response=response(
                status=101,
                body=NoResponseBody(reason=NoBodyReason.STATUS_204),
                scheme=HttpScheme.HTTP,
            ),
        )


def test_terminal_response_rejects_protocol_and_collection_contradictions() -> None:
    valid = response()
    with pytest.raises(TypeError, match="protocol and status"):
        msgspec.structs.replace(valid, protocol=cast("HttpProtocol", "h2"))
    with pytest.raises(ValueError, match="HTTP/2 cannot represent"):
        msgspec.structs.replace(valid, status=HttpStatus(value=101))
    with pytest.raises(TypeError, match="reason must be ReasonPhrase"):
        msgspec.structs.replace(valid, reason=cast("ReasonPhrase", "OK"))
    with pytest.raises(ValueError, match="cannot carry a reason phrase"):
        msgspec.structs.replace(valid, reason=ReasonPhrase(value="OK"))
    with pytest.raises(TypeError, match="informational responses"):
        msgspec.structs.replace(valid, informational=cast("tuple[InformationalResponse, ...]", []))
    with pytest.raises(TypeError, match="informational responses"):
        msgspec.structs.replace(
            valid,
            informational=cast("tuple[InformationalResponse, ...]", ("early",)),
        )
    with pytest.raises(TypeError, match="headers and trailers"):
        msgspec.structs.replace(valid, headers=cast("ResponseFields", ()))
    with pytest.raises(TypeError, match="content encodings"):
        msgspec.structs.replace(valid, content_encodings=cast("tuple[ContentCoding, ...]", []))
    with pytest.raises(TypeError, match="content encodings"):
        msgspec.structs.replace(
            valid,
            content_encodings=cast("tuple[ContentCoding, ...]", ("gzip",)),
        )
    with pytest.raises(TypeError, match="diagnostics"):
        msgspec.structs.replace(valid, diagnostics=cast("tuple[Diagnostic, ...]", []))
    with pytest.raises(TypeError, match="diagnostics"):
        msgspec.structs.replace(valid, diagnostics=cast("tuple[Diagnostic, ...]", ("bad",)))
    with pytest.raises(ValueError, match="one HTTP protocol"):
        msgspec.structs.replace(
            valid,
            informational=(
                InformationalResponse(
                    protocol=HttpProtocol.HTTP_1_1,
                    status=HttpStatus(value=103),
                ),
            ),
        )
    with pytest.raises(TypeError, match="response media"):
        msgspec.structs.replace(valid, media=cast("MissingMediaType", "missing"))
    with pytest.raises(TypeError, match="response body"):
        msgspec.structs.replace(valid, body=cast("ResponseEntityBody", "body"))
    with pytest.raises(ValueError, match="media metadata must agree"):
        msgspec.structs.replace(
            valid,
            media=InvalidMediaType(reason=InvalidMediaTypeReason.MALFORMED),
        )
    with pytest.raises(TypeError, match="response endpoint"):
        msgspec.structs.replace(valid, endpoint=cast("EndpointMetadata", "endpoint"))
    with pytest.raises(TypeError, match="explicit TLS layers"):
        msgspec.structs.replace(valid, tls=cast("TlsLayers", "tls"))
