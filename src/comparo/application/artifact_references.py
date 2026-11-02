"""Pure immutable coordinates for authenticated artifact message access.

These contracts deliberately contain no storage, filesystem, reporting, or runtime behavior.
Manifest-only projections can therefore expose stable body coordinates without depending on the
effectful artifact service that later authenticates and reads them.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import cast
from typing import final

import msgspec

_STABLE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+~-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require_text(value: object, *, label: str, maximum: int) -> None:
    """Require exact bounded text that can cross the JSON boundary."""
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"{label} must be bounded non-empty text")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{label} must contain Unicode scalar values")


def _require_stable_token(value: object, *, label: str, maximum: int) -> None:
    """Require one bounded ASCII token safe for identity and local routing."""
    _require_text(value, label=label, maximum=maximum)
    if _STABLE_TOKEN.fullmatch(cast("str", value)) is None:
        raise ValueError(f"{label} must be a stable ASCII token")


class ArtifactBodyMessage(StrEnum):
    """The sanitized HTTP message body selected for inspection."""

    REQUEST = "request"
    RESPONSE = "response"


@final
class ArtifactBodyReference(msgspec.Struct, frozen=True, kw_only=True):
    """Stable artifact HTTP coordinates for one request or response body."""

    artifact_api_version: str
    artifact_id: str
    artifact_results_sha256: str
    execution_id: str
    attempt_id: str
    exchange_id: str
    message: ArtifactBodyMessage

    def __post_init__(self) -> None:
        """Bind bounded structural coordinates to one artifact identity and revision."""
        _require_text(
            self.artifact_api_version,
            label="artifact body reference API version",
            maximum=127,
        )
        _require_stable_token(
            self.artifact_id,
            label="artifact body reference artifact ID",
            maximum=63,
        )
        if (
            type(self.artifact_results_sha256) is not str
            or _SHA256.fullmatch(self.artifact_results_sha256) is None
        ):
            raise ValueError(
                "artifact body reference results SHA-256 must be lowercase hexadecimal"
            )
        for label, value in (
            ("artifact body reference execution ID", self.execution_id),
            ("artifact body reference attempt ID", self.attempt_id),
            ("artifact body reference exchange ID", self.exchange_id),
        ):
            _require_text(value, label=label, maximum=64)
        if type(self.message) is not ArtifactBodyMessage:
            raise TypeError("artifact body message must be typed")


__all__ = ("ArtifactBodyMessage", "ArtifactBodyReference")
