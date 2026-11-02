"""Canonical decoded HTTP media types and deterministic field rendering."""

from __future__ import annotations

import re
from typing import final

import msgspec

from comparo.core.domain.http.fields import HeaderText

_MEDIA_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9a-z-]+")
_PARAMETER_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


@final
class MediaTypeTokenValue(msgspec.Struct, frozen=True, kw_only=True):
    """A decoded media-type parameter value representable in canonical token form."""

    value: str

    def __post_init__(self) -> None:
        """Require the exact token repertoire."""
        if type(self.value) is not str or _PARAMETER_TOKEN.fullmatch(self.value) is None:
            raise ValueError("media-type token parameter value must use token form")


@final
class MediaTypeQuotedValue(msgspec.Struct, frozen=True, kw_only=True):
    """A decoded quoted parameter value; rendering escapes quotes and backslashes."""

    value: str

    def __post_init__(self) -> None:
        """Allow HTTP quoted text while rejecting controls and non-Latin-1 wire values."""
        if type(self.value) is not str or any(
            ord(character) > 255
            or (ord(character) < 32 and character != "\t")
            or ord(character) == 127
            for character in self.value
        ):
            raise ValueError("media-type quoted parameter contains a forbidden character")
        if _PARAMETER_TOKEN.fullmatch(self.value) is not None:
            raise ValueError("token-compatible media-type values must use canonical token form")


type MediaTypeParameterValue = MediaTypeTokenValue | MediaTypeQuotedValue


@final
class MediaTypeParameter(msgspec.Struct, frozen=True, kw_only=True):
    """One canonical decoded media-type parameter with explicit wire representation."""

    name: str
    value: MediaTypeParameterValue

    def __post_init__(self) -> None:
        """Require a canonical name and explicit token or decoded quoted value."""
        if (
            type(self.name) is not str
            or self.name.lower() != self.name
            or _MEDIA_TOKEN.fullmatch(self.name) is None
        ):
            raise ValueError("media-type parameter name must be a lowercase token")
        if type(self.value) not in {MediaTypeTokenValue, MediaTypeQuotedValue}:
            raise TypeError("media-type parameter value must declare token or quoted form")


@final
class MediaType(msgspec.Struct, frozen=True, kw_only=True):
    """A structured canonical media type."""

    type: str
    subtype: str
    parameters: tuple[MediaTypeParameter, ...] = ()

    def __post_init__(self) -> None:
        """Require canonical components and unique immutable parameters."""
        for label, value in (("type", self.type), ("subtype", self.subtype)):
            if (
                type(value) is not str
                or value.lower() != value
                or _MEDIA_TOKEN.fullmatch(value) is None
            ):
                raise ValueError(f"media {label} must be a lowercase token")
        if type(self.parameters) is not tuple or any(
            type(parameter) is not MediaTypeParameter for parameter in self.parameters
        ):
            raise TypeError("media-type parameters must be an immutable tuple")
        names = tuple(parameter.name for parameter in self.parameters)
        if len(set(names)) != len(names):
            raise ValueError("media-type parameter names must be unique")


def render_media_type(value: MediaType) -> HeaderText:
    """Render the one canonical HTTP field value for a structured media type."""
    if type(value) is not MediaType:
        raise TypeError("media-type rendering requires MediaType")
    rendered = f"{value.type}/{value.subtype}"
    for parameter in value.parameters:
        if type(parameter.value) is MediaTypeTokenValue:
            parameter_value = parameter.value.value
        else:
            escaped = parameter.value.value.replace("\\", "\\\\").replace('"', '\\"')
            parameter_value = f'"{escaped}"'
        rendered += f"; {parameter.name}={parameter_value}"
    return HeaderText(value=rendered)


def parameter_token_compatible(value: str) -> bool:
    """Return whether decoded text can use canonical unquoted parameter form."""
    return type(value) is str and _PARAMETER_TOKEN.fullmatch(value) is not None
