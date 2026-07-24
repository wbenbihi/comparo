"""Narrow effect ports injected into run-scoped value resolution."""

from __future__ import annotations

from typing import Protocol

from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.resolution.models import ExternalValueRequest
from comparo.core.resolution.models import FileReadRequest
from comparo.core.resolution.models import GenerationRequest
from comparo.core.resolution.models import ProviderOutcome


class ExternalValueProvider(Protocol):
    """Read one operator-registered logical source such as a secret manager."""

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return the exact provider identity used in provenance and dispatch."""
        ...

    async def read(self, request: ExternalValueRequest) -> ProviderOutcome:
        """Read one already policy-bound source without raising payload-bearing errors."""
        ...


class FileValueProvider(Protocol):
    """Confine, authorize, and read one Project-requested file path."""

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return the exact file-provider implementation identity."""
        ...

    async def read(self, request: FileReadRequest) -> ProviderOutcome:
        """Return bytes or a typed value-free absence, denial, or failure."""
        ...


class GeneratedValueProvider(Protocol):
    """Produce deterministic or secure values for exact runtime scopes."""

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return the exact generator identity."""
        ...

    async def generate(self, request: GenerationRequest) -> ProviderOutcome:
        """Generate one value without depending on scheduler order."""
        ...


class RunClock(Protocol):
    """Supply explicit time without ambient clock reads in resolution services."""

    def now(self) -> UtcInstant:
        """Return one exact UTC instant."""
        ...
