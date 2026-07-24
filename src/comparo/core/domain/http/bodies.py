"""Immutable typed request bodies and repeatable byte-source descriptors."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TypeGuard
from typing import final
from typing import override

import msgspec

from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.media_types import MediaType as MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter as MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeParameterValue as MediaTypeParameterValue
from comparo.core.domain.http.media_types import MediaTypeQuotedValue as MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue as MediaTypeTokenValue
from comparo.core.domain.http.media_types import parameter_token_compatible
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value

_PARAMETER_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_BOUNDARY_PATTERN = re.compile(r"[0-9A-Za-z'()+_,./:=?-]{1,70}")
_JSON_VALUE_TYPES = {
    JsonNull,
    JsonBoolean,
    JsonInteger,
    JsonDecimal,
    JsonString,
    JsonArray,
    JsonObject,
}


def _require_body_text(value: str, *, label: str, allow_empty: bool = True) -> None:
    if type(value) is not str:
        raise TypeError(f"{label} must be text")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must contain valid Unicode scalar values") from error


@final
class BodyText(msgspec.Struct, frozen=True, kw_only=True):
    """Exact UTF-8 text held by a text, form, or multipart body."""

    value: str

    def __post_init__(self) -> None:
        """Require exact text with a valid UTF-8 encoding."""
        _require_body_text(self.value, label="body text")


type TextContent = BodyText | SensitiveValue[BodyText]
type ByteContent = BytesValue | SensitiveValue[BytesValue] | RepeatableByteSource
type BodyDigest = Sha256Digest | SensitiveValue[Sha256Digest]


@final
class RepeatableByteSource(msgspec.Struct, frozen=True, kw_only=True):
    """A stable handle to complete snapshotted bytes owned by a runtime byte-source port."""

    source_id: BodyId
    byte_count: ByteCount
    digest: BodyDigest
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require stable content identity, size, taint, and provenance."""
        if type(self.source_id) is not BodyId:
            raise TypeError("byte-source ID must be an opaque BodyId")
        if type(self.byte_count) is not ByteCount:
            raise TypeError("byte-source byte count must be ByteCount")
        if type(self.digest) is not Sha256Digest and not isinstance(self.digest, SensitiveValue):
            raise TypeError("byte-source digest must be public or sensitivity-contained")
        if isinstance(self.digest, SensitiveValue) and self.digest.payload_type is not Sha256Digest:
            raise TypeError("sensitive byte-source digest payload must be Sha256Digest")
        if type(self.provenance) is not ValueProvenance:
            raise TypeError("byte-source provenance must be ValueProvenance")

    @property
    def sensitivity(self) -> Sensitivity:
        """Derive sensitivity from containment instead of trusting a parallel flag."""
        if isinstance(self.digest, SensitiveValue):
            return Sensitivity.SENSITIVE
        return Sensitivity.PUBLIC

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Expose non-secret lineage for sensitive snapshots."""
        if isinstance(self.digest, SensitiveValue):
            return self.digest.origins
        return ()

    @override
    def __repr__(self) -> str:
        """Refuse generic rendering when even the content digest is sensitivity-contained."""
        if isinstance(self.digest, SensitiveValue):
            raise TypeError("sensitive byte-source descriptors cannot be rendered")
        return (
            f"{type(self).__name__}(source_id={self.source_id!r}, "
            f"byte_count={self.byte_count!r}, digest={self.digest!r}, "
            f"provenance={self.provenance!r})"
        )


class JsonSerialization(StrEnum):
    """Named deterministic JSON wire representations."""

    COMPARO_V1 = "comparo-v1"


@final
class NoBody(msgspec.Struct, frozen=True, kw_only=True):
    """An explicit absence of request entity bytes."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


@final
class JsonBody(msgspec.Struct, frozen=True, kw_only=True):
    """An exact JSON value with an explicit deterministic serialization contract."""

    value: JsonValue | SensitiveValue[JsonValue]
    serialization: JsonSerialization = JsonSerialization.COMPARO_V1
    media_type: MediaType | None = None

    def __post_init__(self) -> None:
        """Require an exact immutable JSON payload and named serialization."""
        if not is_json_value(self.value) and not isinstance(self.value, SensitiveValue):
            raise TypeError("JSON body value must be JsonValue or SensitiveValue")
        if (
            isinstance(self.value, SensitiveValue)
            and self.value.payload_type not in _JSON_VALUE_TYPES
        ):
            raise TypeError("sensitive JSON body payload must be a typed JsonValue")
        if type(self.serialization) is not JsonSerialization:
            raise TypeError("JSON body serialization must be JsonSerialization")
        if self.media_type is not None and type(self.media_type) is not MediaType:
            raise TypeError("JSON body media type must be MediaType")


@final
class TextBody(msgspec.Struct, frozen=True, kw_only=True):
    """A UTF-8 text request body."""

    value: TextContent
    media_type: MediaType | None = None

    def __post_init__(self) -> None:
        """Require immutable UTF-8 text and an optional typed media type."""
        if type(self.value) is not BodyText and not isinstance(self.value, SensitiveValue):
            raise TypeError("text body value must be BodyText or SensitiveValue")
        if isinstance(self.value, SensitiveValue) and self.value.payload_type is not BodyText:
            raise TypeError("sensitive text body payload must be BodyText")
        if self.media_type is not None and type(self.media_type) is not MediaType:
            raise TypeError("text body media type must be MediaType")


@final
class BytesBody(msgspec.Struct, frozen=True, kw_only=True):
    """Opaque inline or repeatable externally stored request bytes."""

    value: ByteContent
    media_type: MediaType | None = None

    def __post_init__(self) -> None:
        """Require immutable inline bytes or a repeatable byte source."""
        if type(self.value) not in {BytesValue, RepeatableByteSource} and not isinstance(
            self.value, SensitiveValue
        ):
            raise TypeError("bytes body value must be BytesValue, SensitiveValue, or byte source")
        if isinstance(self.value, SensitiveValue) and self.value.payload_type is not BytesValue:
            raise TypeError("sensitive bytes body payload must be BytesValue")
        if self.media_type is not None and type(self.media_type) is not MediaType:
            raise TypeError("bytes body media type must be MediaType")


@final
class FormName(msgspec.Struct, frozen=True, kw_only=True):
    """An exact non-empty UTF-8 form field name."""

    value: str

    def __post_init__(self) -> None:
        """Require a non-empty UTF-8 field name."""
        _require_body_text(self.value, label="form field name", allow_empty=False)


@final
class FormField(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered form occurrence; repeated names remain distinct."""

    name: FormName
    value: TextContent

    def __post_init__(self) -> None:
        """Require typed immutable field components."""
        if type(self.name) is not FormName:
            raise TypeError("form field name must be FormName")
        if type(self.value) is not BodyText and not isinstance(self.value, SensitiveValue):
            raise TypeError("form field value must be BodyText or SensitiveValue")
        if isinstance(self.value, SensitiveValue) and self.value.payload_type is not BodyText:
            raise TypeError("sensitive form field payload must be BodyText")


@final
class FormBody(msgspec.Struct, frozen=True, kw_only=True):
    """An ordered repeated-string application/x-www-form-urlencoded body."""

    fields: tuple[FormField, ...]

    def __post_init__(self) -> None:
        """Require an immutable source-ordered form collection."""
        if type(self.fields) is not tuple or any(
            type(field) is not FormField for field in self.fields
        ):
            raise TypeError("form fields must be an immutable tuple of FormField values")


@final
class MultipartBoundary(msgspec.Struct, frozen=True, kw_only=True):
    """A cell-shared multipart boundary already chosen by the planner."""

    value: str

    def __post_init__(self) -> None:
        """Require bounded interoperable multipart boundary syntax."""
        if type(self.value) is not str or _BOUNDARY_PATTERN.fullmatch(self.value) is None:
            raise ValueError("multipart boundary must be a 1-70 character canonical token")


@final
class MultipartDispositionName(msgspec.Struct, frozen=True, kw_only=True):
    """A multipart disposition name safe for exact quoted-string materialization."""

    value: str

    def __post_init__(self) -> None:
        """Reject ambiguous, non-ASCII, or injection-capable disposition names."""
        if type(self.value) is not str or _PARAMETER_TOKEN.fullmatch(self.value) is None:
            raise ValueError("multipart disposition name must be a non-empty ASCII token")


@final
class MultipartDispositionFilename(msgspec.Struct, frozen=True, kw_only=True):
    """A decoded filename emitted only as RFC 8187 UTF-8 ``filename*`` data.

    Comparo emits no legacy ``filename`` fallback. Percent encoding is adapter-owned; path
    separators and controls remain invalid before encoding.
    """

    value: str

    def __post_init__(self) -> None:
        """Require a non-empty scalar string with no controls or path separators."""
        _require_body_text(self.value, label="multipart filename", allow_empty=False)
        if any(
            ord(character) < 32 or ord(character) == 127 or character in {"\\", "/"}
            for character in self.value
        ):
            raise ValueError("multipart filename contains an unsafe disposition character")


type MultipartFilename = MultipartDispositionFilename | SensitiveValue[MultipartDispositionFilename]


@final
class MultipartTextPart(msgspec.Struct, frozen=True, kw_only=True):
    """One typed multipart text field."""

    name: MultipartDispositionName
    value: TextContent
    media_type: MediaType | None = None

    def __post_init__(self) -> None:
        """Require immutable typed text-part components."""
        if type(self.name) is not MultipartDispositionName:
            raise TypeError("multipart text-part name must be MultipartDispositionName")
        if type(self.value) is not BodyText and not isinstance(self.value, SensitiveValue):
            raise TypeError("multipart text-part value must be BodyText or SensitiveValue")
        if isinstance(self.value, SensitiveValue) and self.value.payload_type is not BodyText:
            raise TypeError("sensitive multipart text payload must be BodyText")
        if self.media_type is not None and type(self.media_type) is not MediaType:
            raise TypeError("multipart text-part media type must be MediaType")


@final
class MultipartFilePart(msgspec.Struct, frozen=True, kw_only=True):
    """One typed multipart byte source with explicit filename and media type."""

    name: MultipartDispositionName
    filename: MultipartFilename
    value: ByteContent
    media_type: MediaType

    def __post_init__(self) -> None:
        """Require typed filename, byte content, and media metadata."""
        if type(self.name) is not MultipartDispositionName:
            raise TypeError("multipart file-part name must be MultipartDispositionName")
        if type(self.filename) is not MultipartDispositionFilename and not isinstance(
            self.filename, SensitiveValue
        ):
            raise TypeError("multipart filename must use a safe disposition filename type")
        if (
            isinstance(self.filename, SensitiveValue)
            and self.filename.payload_type is not MultipartDispositionFilename
        ):
            raise TypeError("sensitive multipart filename payload has the wrong type")
        if type(self.value) not in {BytesValue, RepeatableByteSource} and not isinstance(
            self.value, SensitiveValue
        ):
            raise TypeError("multipart file content must be immutable bytes or byte source")
        if isinstance(self.value, SensitiveValue) and self.value.payload_type is not BytesValue:
            raise TypeError("sensitive multipart file payload must be BytesValue")
        if type(self.media_type) is not MediaType:
            raise TypeError("multipart file-part media type must be MediaType")


type MultipartPart = MultipartTextPart | MultipartFilePart


@final
class MultipartBody(msgspec.Struct, frozen=True, kw_only=True):
    """An ordered multipart body whose boundary is shared across Environment fanout."""

    boundary: MultipartBoundary
    parts: tuple[MultipartPart, ...]

    def __post_init__(self) -> None:
        """Require a planned boundary and immutable typed parts."""
        if type(self.boundary) is not MultipartBoundary:
            raise TypeError("multipart body boundary must be MultipartBoundary")
        if type(self.parts) is not tuple or any(
            type(part) not in {MultipartTextPart, MultipartFilePart} for part in self.parts
        ):
            raise TypeError("multipart parts must be an immutable tuple of typed parts")


type RequestBody = NoBody | JsonBody | TextBody | BytesBody | FormBody | MultipartBody


def is_request_body(value: object) -> TypeGuard[RequestBody]:
    """Narrow an arbitrary value to the closed initial request-body union."""
    return type(value) in {NoBody, JsonBody, TextBody, BytesBody, FormBody, MultipartBody}


@final
class NoMaterializedBody(msgspec.Struct, frozen=True, kw_only=True):
    """Proof that the finalized request emits no entity bytes."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


def multipart_content_type(boundary: MultipartBoundary) -> HeaderText:
    """Return the canonical Content-Type field value for a chosen multipart boundary."""
    if type(boundary) is not MultipartBoundary:
        raise TypeError("multipart Content-Type requires MultipartBoundary")
    if parameter_token_compatible(boundary.value):
        parameter_value: MediaTypeParameterValue = MediaTypeTokenValue(value=boundary.value)
    else:
        parameter_value = MediaTypeQuotedValue(value=boundary.value)
    return render_media_type(
        MediaType(
            type="multipart",
            subtype="form-data",
            parameters=(MediaTypeParameter(name="boundary", value=parameter_value),),
        )
    )


@final
class MultipartBoundaryValidation(msgspec.Struct, frozen=True, kw_only=True):
    """Runtime record binding collision validation to exact materialized multipart bytes.

    The materializer issues this only after verifying the boundary delimiter is absent from every
    encoded part before assembly. Domain construction validates its snapshot and Content-Type
    bindings; the byte-level collision scan remains a runtime responsibility.
    """

    boundary: MultipartBoundary
    source_id: BodyId
    digest: BodyDigest
    encoded_content_type: HeaderText

    def __post_init__(self) -> None:
        """Require typed snapshot identity, digest containment, and canonical Content-Type."""
        if type(self.boundary) is not MultipartBoundary:
            raise TypeError("multipart validation boundary must be MultipartBoundary")
        if type(self.source_id) is not BodyId:
            raise TypeError("multipart validation source must be BodyId")
        if type(self.digest) is not Sha256Digest and not isinstance(self.digest, SensitiveValue):
            raise TypeError("multipart validation digest must preserve sensitivity")
        if isinstance(self.digest, SensitiveValue) and self.digest.payload_type is not Sha256Digest:
            raise TypeError("sensitive multipart validation digest must contain Sha256Digest")
        if type(self.encoded_content_type) is not HeaderText:
            raise TypeError("multipart validation Content-Type must be HeaderText")
        if self.encoded_content_type != multipart_content_type(self.boundary):
            raise ValueError("multipart validation Content-Type does not match its boundary")


@final
class MaterializedBody(msgspec.Struct, frozen=True, kw_only=True):
    """A complete repeatable byte snapshot produced before outbound authorization."""

    source: RepeatableByteSource
    multipart: MultipartBoundaryValidation | None = None

    def __post_init__(self) -> None:
        """Require an opaque snapshot containing byte count and sensitivity-aware digest."""
        if type(self.source) is not RepeatableByteSource:
            raise TypeError("materialized request body must be a RepeatableByteSource")
        if self.multipart is not None and type(self.multipart) is not MultipartBoundaryValidation:
            raise TypeError("multipart materialization must use MultipartBoundaryValidation")
        if self.multipart is not None and (
            self.multipart.source_id != self.source.source_id
            or self.multipart.digest != self.source.digest
        ):
            raise ValueError("multipart validation is not bound to the materialized snapshot")


type FinalizedBody = NoMaterializedBody | MaterializedBody
