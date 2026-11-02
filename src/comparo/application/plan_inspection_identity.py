"""Stable revision-bearing identities used by Plan inspection products."""

from __future__ import annotations

from typing import final

import msgspec


@final
class ResourceCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """Stable resource identity plus exact compiled-definition fingerprint."""

    namespace: str
    kind: str
    resource_id: str
    fingerprint: str

    def __post_init__(self) -> None:
        """Require a complete bounded resource identity and exact revision digest."""
        values = (self.namespace, self.kind, self.resource_id)
        if any(type(item) is not str or not item or len(item) > 255 for item in values):
            raise ValueError("resource coordinate identity must be bounded non-empty text")
        if not is_sha256(self.fingerprint):
            raise ValueError("resource coordinate fingerprint must be a SHA-256 hex digest")


def is_sha256(value: object) -> bool:
    """Return whether a value is one canonical lowercase SHA-256 digest."""
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = ("ResourceCoordinate", "is_sha256")
