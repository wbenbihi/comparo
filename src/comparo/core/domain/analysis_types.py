"""Exact structural identities used by authorized external analysis."""

from __future__ import annotations

import re
from typing import final

import msgspec

_LOWER_IDENTITY_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")
_MODEL_IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")


@final
class AnalysisProviderId(msgspec.Struct, frozen=True, kw_only=True):
    """Stable external-analysis provider identity."""

    value: str

    def __post_init__(self) -> None:
        """Reject payload, prose, controls, and oversized provider identities."""
        if (
            type(self.value) is not str
            or len(self.value) > 127
            or _LOWER_IDENTITY_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError("analysis provider ID must be a bounded lowercase namespaced token")


@final
class AnalysisSchemaId(msgspec.Struct, frozen=True, kw_only=True):
    """Stable external-analysis result-schema identity."""

    value: str

    def __post_init__(self) -> None:
        """Reject payload, prose, controls, and oversized schema identities."""
        if (
            type(self.value) is not str
            or len(self.value) > 127
            or _LOWER_IDENTITY_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError("analysis schema ID must be a bounded lowercase namespaced token")


@final
class AnalysisModelId(msgspec.Struct, frozen=True, kw_only=True):
    """Stable provider-owned model identity without control or payload text."""

    value: str

    def __post_init__(self) -> None:
        """Reject payload, prose, controls, and oversized model identities."""
        if type(self.value) is not str or _MODEL_IDENTITY_PATTERN.fullmatch(self.value) is None:
            raise ValueError("analysis model ID must be a bounded structural token")


@final
class AnalysisContractVersion(msgspec.Struct, frozen=True, kw_only=True):
    """Canonical provider, model, or result-schema contract version."""

    value: str

    def __post_init__(self) -> None:
        """Reject payload, prose, controls, and oversized version identities."""
        if type(self.value) is not str or _VERSION_PATTERN.fullmatch(self.value) is None:
            raise ValueError("analysis contract version must be a bounded structural token")
