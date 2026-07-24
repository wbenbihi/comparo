"""Human-facing metadata kept separate from stable resource identity."""

from __future__ import annotations

from typing import final

import msgspec


def _require_optional_text(value: str | None, *, label: str) -> None:
    if value is not None and (type(value) is not str or not value.strip()):
        raise ValueError(f"{label} must be absent or non-blank text")


@final
class Metadata(msgspec.Struct, frozen=True, kw_only=True):
    """Optional human name, description, and ordered unique tags."""

    name: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject ambiguous blank metadata and duplicate tags."""
        _require_optional_text(self.name, label="metadata name")
        _require_optional_text(self.description, label="metadata description")
        if type(self.tags) is not tuple:
            raise TypeError("metadata tags must be an immutable tuple")
        if any(type(tag) is not str or not tag.strip() for tag in self.tags):
            raise ValueError("metadata tags must be non-blank text")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("metadata tags must be unique")
