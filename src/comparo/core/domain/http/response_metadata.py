"""Deterministic response metadata derivation from duplicate-preserving HTTP fields."""

from __future__ import annotations

from typing import Protocol

from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HeaderValue
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.http.media_types import parameter_token_compatible
from comparo.core.domain.http.response_bodies import ContentCoding
from comparo.core.domain.http.response_bodies import InvalidContentEncodingReason
from comparo.core.domain.http.response_bodies import InvalidMediaType
from comparo.core.domain.http.response_bodies import InvalidMediaTypeReason
from comparo.core.domain.http.response_bodies import MediaTypeMetadata
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.http.response_bodies import ParsedMediaType


class _ResponseFieldLike(Protocol):
    """Structural leaf contract needed for metadata derivation."""

    @property
    def name(self) -> HeaderName: ...

    @property
    def value(self) -> HeaderValue: ...


class ResponseFieldsLike(Protocol):
    """A duplicate-preserving field collection without importing response aggregates."""

    @property
    def fields(self) -> tuple[_ResponseFieldLike, ...]:
        """Return ordered duplicate-preserving response fields."""
        ...


def _public_values(fields: ResponseFieldsLike, name: str) -> tuple[str | None, ...]:
    return tuple(
        field.value.value if type(field.value) is HeaderText else None
        for field in fields.fields
        if field.name.value == name
    )


def _split_parameters(value: str) -> tuple[str, ...] | None:
    parts: list[str] = []
    start = 0
    quoted = False
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and character == "\\":
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == ";" and not quoted:
            parts.append(value[start:index].strip())
            start = index + 1
    if quoted or escaped:
        return None
    parts.append(value[start:].strip())
    return tuple(parts)


def _decode_quoted(value: str) -> str | None:
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        return None
    output: list[str] = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            output.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return None
        else:
            output.append(character)
    if escaped:
        return None
    return "".join(output)


def _parse_media_type(value: str) -> ParsedMediaType | None:
    pieces = _split_parameters(value)
    if pieces is None or not pieces or pieces[0].count("/") != 1:
        return None
    media_type, subtype = (part.strip().lower() for part in pieces[0].split("/", 1))
    parameters: list[MediaTypeParameter] = []
    try:
        for piece in pieces[1:]:
            if piece.count("=") != 1:
                return None
            name, raw = (part.strip() for part in piece.split("=", 1))
            if raw.startswith('"'):
                decoded = _decode_quoted(raw)
                if decoded is None:
                    return None
                parameter_value = (
                    MediaTypeTokenValue(value=decoded)
                    if parameter_token_compatible(decoded)
                    else MediaTypeQuotedValue(value=decoded)
                )
            else:
                parameter_value = MediaTypeTokenValue(value=raw)
            parameters.append(MediaTypeParameter(name=name.lower(), value=parameter_value))
        return ParsedMediaType(
            value=MediaType(
                type=media_type,
                subtype=subtype,
                parameters=tuple(parameters),
            )
        )
    except (TypeError, ValueError):
        return None


def derive_media_type(fields: ResponseFieldsLike) -> MediaTypeMetadata:
    """Derive the only media state consistent with ordered Content-Type occurrences."""
    values = _public_values(fields, "content-type")
    if not values:
        return MissingMediaType()
    if len(values) > 1:
        reason = (
            InvalidMediaTypeReason.MULTIPLE_VALUES
            if len(set(values)) == 1
            else InvalidMediaTypeReason.CONFLICTING_VALUES
        )
        return InvalidMediaType(reason=reason)
    if values[0] is None:
        return InvalidMediaType(reason=InvalidMediaTypeReason.MALFORMED)
    parsed = _parse_media_type(values[0])
    return parsed or InvalidMediaType(reason=InvalidMediaTypeReason.MALFORMED)


def derive_content_encodings(
    fields: ResponseFieldsLike,
) -> tuple[tuple[ContentCoding, ...], InvalidContentEncodingReason | None]:
    """Combine Content-Encoding occurrences in wire order or return one typed error."""
    values = _public_values(fields, "content-encoding")
    if any(value is None for value in values):
        return (), InvalidContentEncodingReason.MALFORMED
    public_values = tuple(value for value in values if value is not None)
    tokens = tuple(token.strip().lower() for value in public_values for token in value.split(","))
    if any(not token for token in tokens):
        return (), InvalidContentEncodingReason.MALFORMED
    try:
        return tuple(ContentCoding(value=token) for token in tokens), None
    except ValueError:
        return (), InvalidContentEncodingReason.MALFORMED
