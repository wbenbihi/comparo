"""Branch-complete direct-construction tests for HTTP authentication and bodies."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import ApiKeyCredential
from comparo.core.domain.http.auth import ApiKeyPlacement
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BasicPassword
from comparo.core.domain.http.auth import BasicUsername
from comparo.core.domain.http.auth import BasicWireEncoding
from comparo.core.domain.http.auth import BearerToken
from comparo.core.domain.http.auth import CookieApiKeyPlacement
from comparo.core.domain.http.auth import HeaderApiKeyPlacement
from comparo.core.domain.http.auth import QueryApiKeyPlacement
from comparo.core.domain.http.bodies import BodyDigest
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import ByteContent
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormField
from comparo.core.domain.http.bodies import FormName
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import JsonSerialization
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import MultipartBoundaryValidation
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import MultipartDispositionName
from comparo.core.domain.http.bodies import MultipartFilename
from comparo.core.domain.http.bodies import MultipartFilePart
from comparo.core.domain.http.bodies import MultipartPart
from comparo.core.domain.http.bodies import MultipartTextPart
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.bodies import multipart_content_type
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from tests.core.domain.http.factories import api_key_secret
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import password_secret
from tests.core.domain.http.factories import provenance


def sensitive[ValueT](value: ValueT) -> SensitiveValue[ValueT]:
    """Seal a deeply immutable test value with deterministic lineage."""
    return SensitiveValue(value, origins=(TaintOriginId(value="validation-test"),))


def byte_source(*, sealed: bool = False) -> RepeatableByteSource:
    """Build one valid public or sensitive repeatable byte source."""
    body_digest: BodyDigest = sensitive(digest("b")) if sealed else digest("b")
    return RepeatableByteSource(
        source_id=BodyId(value=uuid4()),
        byte_count=ByteCount(value=4),
        digest=body_digest,
        provenance=provenance(),
    )


def test_authentication_scalar_validation_is_exhaustive() -> None:
    """Reject every malformed scalar class before it reaches wire construction."""
    with pytest.raises(ValueError, match="non-empty"):
        BearerToken(value="")
    for malformed in ("contains space", "mid=padding"):
        with pytest.raises(ValueError, match="invalid character"):
            BearerToken(value=malformed)
    with pytest.raises(ValueError, match="colon"):
        BasicUsername(value="user:name")
    with pytest.raises(ValueError, match="safe visible ASCII"):
        ApiKeyCredential(value="key with space")
    with pytest.raises(TypeError, match="must be text"):
        BasicUsername(value=cast("str", 1))
    with pytest.raises(ValueError, match="Unicode scalar"):
        BasicPassword(value="\ud800")
    with pytest.raises(ValueError, match="control"):
        BasicPassword(value="line\nfeed")


def test_authentication_components_reject_wrong_runtime_types() -> None:
    """Keep credentials and placement types closed under direct construction."""
    password = password_secret()
    with pytest.raises(TypeError, match="basic username"):
        BasicAuthentication(
            username=cast("BasicUsername", "user"),
            password=password,
            wire_encoding=BasicWireEncoding.UTF_8,
        )
    with pytest.raises(TypeError, match="username payload"):
        BasicAuthentication(
            username=cast("SensitiveValue[BasicUsername]", sensitive(BodyText(value="user"))),
            password=password,
            wire_encoding=BasicWireEncoding.UTF_8,
        )
    with pytest.raises(TypeError, match="wire encoding"):
        BasicAuthentication(
            username=BasicUsername(value="user"),
            password=password,
            wire_encoding=cast("BasicWireEncoding", "utf-8"),
        )
    with pytest.raises(TypeError, match="header placement"):
        HeaderApiKeyPlacement(name=cast("HeaderName", "x-api-key"))
    with pytest.raises(TypeError, match="query placement"):
        QueryApiKeyPlacement(name=cast("QueryName", "api-key"))
    with pytest.raises(TypeError, match="cookie placement"):
        CookieApiKeyPlacement(name=cast("CookieName", "api-key"))
    with pytest.raises(TypeError, match="supported typed placement"):
        ApiKeyAuthentication(
            credential=api_key_secret(),
            placement=cast("ApiKeyPlacement", object()),
        )


def test_repeatable_byte_source_validates_metadata_and_derives_taint() -> None:
    """Derive sensitivity from the digest and reject untyped descriptor metadata."""
    public = byte_source()
    sealed = byte_source(sealed=True)
    assert public.sensitivity is Sensitivity.PUBLIC
    assert public.taint_origins == ()
    assert "RepeatableByteSource" in repr(public)
    assert sealed.sensitivity is Sensitivity.SENSITIVE
    assert sealed.taint_origins == (TaintOriginId(value="validation-test"),)

    with pytest.raises(TypeError, match="byte-source ID"):
        RepeatableByteSource(
            source_id=cast("BodyId", uuid4()),
            byte_count=public.byte_count,
            digest=public.digest,
            provenance=public.provenance,
        )
    with pytest.raises(TypeError, match="byte count"):
        RepeatableByteSource(
            source_id=public.source_id,
            byte_count=cast("ByteCount", 4),
            digest=public.digest,
            provenance=public.provenance,
        )
    with pytest.raises(TypeError, match="digest must be"):
        RepeatableByteSource(
            source_id=public.source_id,
            byte_count=public.byte_count,
            digest=cast("BodyDigest", object()),
            provenance=public.provenance,
        )
    with pytest.raises(TypeError, match="digest payload"):
        RepeatableByteSource(
            source_id=public.source_id,
            byte_count=public.byte_count,
            digest=cast("BodyDigest", sensitive(HeaderText(value="wrong"))),
            provenance=public.provenance,
        )
    with pytest.raises(TypeError, match="provenance"):
        RepeatableByteSource(
            source_id=public.source_id,
            byte_count=public.byte_count,
            digest=public.digest,
            provenance=cast("ValueProvenance", object()),
        )


def test_body_value_variants_reject_wrong_payloads_and_media() -> None:
    """Exercise the complete sensitive-payload and optional-media validation surface."""
    media = MediaType(type="application", subtype="json")
    with pytest.raises(TypeError, match="body text"):
        BodyText(value=cast("str", 1))
    with pytest.raises(ValueError, match="must not be empty"):
        FormName(value="")

    with pytest.raises(TypeError, match="typed JsonValue"):
        JsonBody(value=cast("SensitiveValue[JsonValue]", sensitive(BodyText(value="wrong"))))
    with pytest.raises(TypeError, match="serialization"):
        JsonBody(
            value=JsonString(value="value"),
            serialization=cast("JsonSerialization", "json"),
        )
    with pytest.raises(TypeError, match="JSON body media"):
        JsonBody(value=JsonString(value="value"), media_type=cast("MediaType", "json"))

    with pytest.raises(TypeError, match="text body value"):
        TextBody(value=cast("BodyText", "text"))
    with pytest.raises(TypeError, match="text body payload"):
        TextBody(value=cast("SensitiveValue[BodyText]", sensitive(JsonString(value="wrong"))))
    with pytest.raises(TypeError, match="text body media"):
        TextBody(value=BodyText(value="text"), media_type=cast("MediaType", "text/plain"))

    with pytest.raises(TypeError, match="bytes body payload"):
        BytesBody(value=cast("ByteContent", sensitive(BodyText(value="wrong"))))
    with pytest.raises(TypeError, match="bytes body media"):
        BytesBody(value=BytesValue(value=b"bytes"), media_type=cast("MediaType", "bytes"))
    body = BytesBody(value=byte_source(), media_type=media)
    assert type(body.value) is RepeatableByteSource
    assert body.value.byte_count.value == 4


def test_form_and_multipart_parts_validate_every_component() -> None:
    """Reject untyped names, payloads, media, filenames, and part collections."""
    text = BodyText(value="value")
    name = MultipartDispositionName(value="field")
    filename = MultipartDispositionFilename(value="file.txt")
    media = MediaType(type="text", subtype="plain")
    with pytest.raises(TypeError, match="form field name"):
        FormField(name=cast("FormName", "field"), value=text)
    with pytest.raises(TypeError, match="form field value"):
        FormField(name=FormName(value="field"), value=cast("BodyText", "value"))
    with pytest.raises(TypeError, match="form field payload"):
        FormField(
            name=FormName(value="field"),
            value=cast("SensitiveValue[BodyText]", sensitive(JsonString(value="wrong"))),
        )
    with pytest.raises(ValueError, match="disposition name"):
        MultipartDispositionName(value="bad name")

    with pytest.raises(TypeError, match="text-part name"):
        MultipartTextPart(name=cast("MultipartDispositionName", "field"), value=text)
    with pytest.raises(TypeError, match="text-part value"):
        MultipartTextPart(name=name, value=cast("BodyText", "value"))
    with pytest.raises(TypeError, match="multipart text payload"):
        MultipartTextPart(
            name=name,
            value=cast("SensitiveValue[BodyText]", sensitive(JsonString(value="wrong"))),
        )
    with pytest.raises(TypeError, match="text-part media"):
        MultipartTextPart(name=name, value=text, media_type=cast("MediaType", "text/plain"))

    with pytest.raises(TypeError, match="file-part name"):
        MultipartFilePart(
            name=cast("MultipartDispositionName", "file"),
            filename=filename,
            value=BytesValue(value=b"x"),
            media_type=media,
        )
    with pytest.raises(TypeError, match="safe disposition filename"):
        MultipartFilePart(
            name=name,
            filename=cast("MultipartFilename", "file.txt"),
            value=BytesValue(value=b"x"),
            media_type=media,
        )
    with pytest.raises(TypeError, match="filename payload"):
        MultipartFilePart(
            name=name,
            filename=cast(
                "SensitiveValue[MultipartDispositionFilename]",
                sensitive(BodyText(value="wrong")),
            ),
            value=BytesValue(value=b"x"),
            media_type=media,
        )
    with pytest.raises(TypeError, match="file content"):
        MultipartFilePart(
            name=name,
            filename=filename,
            value=cast("ByteContent", object()),
            media_type=media,
        )
    with pytest.raises(TypeError, match="file payload"):
        MultipartFilePart(
            name=name,
            filename=filename,
            value=cast("ByteContent", sensitive(BodyText(value="wrong"))),
            media_type=media,
        )
    with pytest.raises(TypeError, match="file-part media"):
        MultipartFilePart(
            name=name,
            filename=filename,
            value=BytesValue(value=b"x"),
            media_type=cast("MediaType", "text/plain"),
        )
    with pytest.raises(TypeError, match="multipart body boundary"):
        MultipartBody(
            boundary=cast("MultipartBoundary", "boundary"),
            parts=cast("tuple[MultipartPart, ...]", ()),
        )


def test_multipart_content_type_and_validation_bind_exact_snapshot() -> None:
    """Cover token and quoted boundary rendering plus all validation bindings."""
    with pytest.raises(TypeError, match="requires MultipartBoundary"):
        multipart_content_type(cast("MultipartBoundary", "boundary"))
    quoted = MultipartBoundary(value="boundary/with/slash")
    assert multipart_content_type(quoted).value.endswith('boundary="boundary/with/slash"')

    source = byte_source()
    boundary = MultipartBoundary(value="boundary")
    content_type = multipart_content_type(boundary)
    validation = MultipartBoundaryValidation(
        boundary=boundary,
        source_id=source.source_id,
        digest=source.digest,
        encoded_content_type=content_type,
    )
    with pytest.raises(TypeError, match="validation boundary"):
        MultipartBoundaryValidation(
            boundary=cast("MultipartBoundary", "boundary"),
            source_id=source.source_id,
            digest=source.digest,
            encoded_content_type=content_type,
        )
    with pytest.raises(TypeError, match="validation source"):
        MultipartBoundaryValidation(
            boundary=boundary,
            source_id=cast("BodyId", uuid4()),
            digest=source.digest,
            encoded_content_type=content_type,
        )
    with pytest.raises(TypeError, match="preserve sensitivity"):
        MultipartBoundaryValidation(
            boundary=boundary,
            source_id=source.source_id,
            digest=cast("BodyDigest", object()),
            encoded_content_type=content_type,
        )
    with pytest.raises(TypeError, match="contain Sha256Digest"):
        MultipartBoundaryValidation(
            boundary=boundary,
            source_id=source.source_id,
            digest=cast("BodyDigest", sensitive(HeaderText(value="wrong"))),
            encoded_content_type=content_type,
        )
    with pytest.raises(TypeError, match="Content-Type must be HeaderText"):
        MultipartBoundaryValidation(
            boundary=boundary,
            source_id=source.source_id,
            digest=source.digest,
            encoded_content_type=cast("HeaderText", "multipart/form-data"),
        )
    with pytest.raises(ValueError, match="does not match"):
        MultipartBoundaryValidation(
            boundary=boundary,
            source_id=source.source_id,
            digest=source.digest,
            encoded_content_type=HeaderText(value="multipart/form-data; boundary=other"),
        )
    assert MaterializedBody(source=source, multipart=validation).multipart is validation
    with pytest.raises(TypeError, match="must be a RepeatableByteSource"):
        MaterializedBody(source=cast("RepeatableByteSource", object()))
    with pytest.raises(TypeError, match="MultipartBoundaryValidation"):
        MaterializedBody(source=source, multipart=cast("MultipartBoundaryValidation", object()))


def test_media_type_direct_construction_rejects_every_invalid_component() -> None:
    """Exercise canonical token, quoted, parameter, collection, and renderer boundaries."""
    with pytest.raises(ValueError, match="token form"):
        MediaTypeTokenValue(value="has space")
    with pytest.raises(ValueError, match="forbidden character"):
        MediaTypeQuotedValue(value="line\nfeed")
    with pytest.raises(ValueError, match="parameter name"):
        MediaTypeParameter(name="Charset", value=MediaTypeTokenValue(value="utf-8"))
    with pytest.raises(TypeError, match="immutable tuple"):
        MediaType(
            type="text",
            subtype="plain",
            parameters=cast("tuple[MediaTypeParameter, ...]", []),
        )
    with pytest.raises(TypeError, match="rendering requires"):
        render_media_type(cast("MediaType", "text/plain"))
    assert type(digest()) is Sha256Digest
