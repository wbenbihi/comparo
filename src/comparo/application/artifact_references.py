"""Pure immutable coordinates for authenticated artifact message access.

These contracts deliberately contain no storage, filesystem, reporting, or runtime behavior.
Manifest-only projections can therefore expose stable body coordinates without depending on the
effectful artifact service that later authenticates and reads them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec


class ArtifactBodyMessage(StrEnum):
    """The sanitized HTTP message body selected for inspection."""

    REQUEST = "request"
    RESPONSE = "response"


@final
class ArtifactBodyReference(msgspec.Struct, frozen=True, kw_only=True):
    """Stable artifact HTTP coordinates for one request or response body."""

    execution_id: str
    attempt_id: str
    exchange_id: str
    message: ArtifactBodyMessage

    def __post_init__(self) -> None:
        """Require bounded structural identifiers and one typed message side."""
        if any(
            type(value) is not str or not value or len(value) > 64
            for value in (self.execution_id, self.attempt_id, self.exchange_id)
        ):
            raise ValueError("artifact body coordinates must be bounded non-empty text")
        if type(self.message) is not ArtifactBodyMessage:
            raise TypeError("artifact body message must be typed")


__all__ = ("ArtifactBodyMessage", "ArtifactBodyReference")
