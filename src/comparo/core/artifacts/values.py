"""Tagged exact JSON values owned by the independently versioned artifact API."""

from __future__ import annotations

from decimal import Decimal
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.artifacts.common import require_text


@final
class ArtifactNull(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
    tag="null",
    tag_field="type",
):
    """The exact JSON null value."""


@final
class ArtifactBoolean(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
    tag="boolean",
    tag_field="type",
):
    """An exact JSON Boolean."""

    value: bool

    def __post_init__(self) -> None:
        """Reject truthy non-Boolean values."""
        if type(self.value) is not bool:
            raise TypeError("artifact Boolean value must be bool")


@final
class ArtifactInteger(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
    tag="integer",
    tag_field="type",
):
    """An arbitrary-precision exact JSON integer."""

    value: int

    def __post_init__(self) -> None:
        """Reject bool and non-integer values."""
        if type(self.value) is not int:
            raise TypeError("artifact integer value must be int")


@final
class ArtifactDecimal(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
    tag="decimal",
    tag_field="type",
):
    """An exact finite JSON decimal retained as canonical text."""

    value: str

    def __post_init__(self) -> None:
        """Require a finite canonical Decimal representation."""
        require_text(self.value, label="artifact decimal value", maximum=1024)
        try:
            parsed = Decimal(self.value)
        except Exception as error:
            raise ValueError("artifact decimal value must be canonical Decimal text") from error
        if not parsed.is_finite() or str(parsed) != self.value:
            raise ValueError("artifact decimal value must be finite canonical Decimal text")


@final
class ArtifactString(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
    tag="string",
    tag_field="type",
):
    """An exact JSON string after sanitization."""

    value: str

    def __post_init__(self) -> None:
        """Require valid bounded Unicode while allowing the empty string."""
        if type(self.value) is not str or len(self.value) > 16_777_216:
            raise ValueError("artifact string value must be bounded text")
        try:
            self.value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("artifact string value must contain Unicode scalar values") from error


@final
class ArtifactArray(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
    tag="array",
    tag_field="type",
):
    """An ordered exact JSON array."""

    items: tuple[ArtifactValue, ...]

    def __post_init__(self) -> None:
        """Require immutable exact artifact values."""
        if type(self.items) is not tuple or any(not is_artifact_value(item) for item in self.items):
            raise TypeError("artifact array items must be immutable exact values")


@final
class ArtifactObjectMember(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
):
    """One ordered exact JSON object member."""

    name: str
    value: ArtifactValue

    def __post_init__(self) -> None:
        """Require a bounded name and exact value."""
        if type(self.name) is not str or len(self.name) > 1_048_576:
            raise ValueError("artifact object member name must be bounded text")
        if not is_artifact_value(self.value):
            raise TypeError("artifact object member must contain an exact value")


@final
class ArtifactObject(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
    tag="object",
    tag_field="type",
):
    """An ordered exact JSON object with unique member names."""

    members: tuple[ArtifactObjectMember, ...]

    def __post_init__(self) -> None:
        """Require immutable members and canonical JSON object uniqueness."""
        if type(self.members) is not tuple or any(
            type(item) is not ArtifactObjectMember for item in self.members
        ):
            raise TypeError("artifact object members must be immutable and typed")
        names = tuple(item.name for item in self.members)
        if len(set(names)) != len(names):
            raise ValueError("artifact object member names must be unique")


type ArtifactValue = (
    ArtifactNull
    | ArtifactBoolean
    | ArtifactInteger
    | ArtifactDecimal
    | ArtifactString
    | ArtifactArray
    | ArtifactObject
)


def is_artifact_value(value: object) -> TypeGuard[ArtifactValue]:
    """Narrow arbitrary input to the exact safe artifact value union."""
    return type(value) in {
        ArtifactNull,
        ArtifactBoolean,
        ArtifactInteger,
        ArtifactDecimal,
        ArtifactString,
        ArtifactArray,
        ArtifactObject,
    }
