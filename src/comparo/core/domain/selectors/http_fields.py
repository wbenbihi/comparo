"""Typed paths for duplicate-preserving HTTP field selection."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import Count


class HttpFieldProjection(DomainStrEnum):
    """Explicit raw or parsed projection of one HTTP field occurrence."""

    RAW_VALUE = "raw-value"
    ITEMS = "items"


@final
class HttpFieldPath(msgspec.Struct, frozen=True, kw_only=True):
    """Select a field name and optionally one duplicate occurrence."""

    name: str
    occurrence: Count | None = None
    projection: HttpFieldProjection = HttpFieldProjection.RAW_VALUE

    def __post_init__(self) -> None:
        """Require bounded valid Unicode and typed occurrence/projection fields."""
        if type(self.name) is not str or not self.name or len(self.name) > 8_192:
            raise ValueError("HTTP selector field name must be non-empty bounded text")
        try:
            self.name.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError(
                "HTTP selector field name must contain Unicode scalar values"
            ) from error
        if self.occurrence is not None and type(self.occurrence) is not Count:
            raise TypeError("HTTP selector field occurrence must be a Count")
        if type(self.projection) is not HttpFieldProjection:
            raise TypeError("HTTP selector field projection must be HttpFieldProjection")
