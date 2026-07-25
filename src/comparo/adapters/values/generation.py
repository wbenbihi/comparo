"""Cryptographically secure value generation behind the resolution port."""

from __future__ import annotations

import secrets
from uuid import UUID

from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.resolution.models import GenerationOperation
from comparo.core.resolution.models import GenerationRequest
from comparo.core.resolution.models import ProviderBlocked
from comparo.core.resolution.models import ProviderOutcome
from comparo.core.resolution.models import ProviderPresent

_IDENTITY = ValueProviderIdentity(namespace="comparo", name="secure-generator", version="1")


class SecureGenerator:
    """Produce explicitly sensitive non-deterministic values from the system CSPRNG."""

    __slots__ = ()

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return the stable secure-generator identity."""
        return _IDENTITY

    async def generate(self, request: GenerationRequest) -> ProviderOutcome:
        """Generate only specs that explicitly request secure sensitive output."""
        if type(request) is not GenerationRequest:
            raise TypeError("secure generation requires a GenerationRequest")
        if not request.spec.secure or not request.spec.sensitive:
            return ProviderBlocked(code=ResolutionBlockCode.CAPABILITY_DENIED)
        if request.spec.operation is GenerationOperation.UUID:
            generated = str(UUID(bytes=secrets.token_bytes(16), version=4))
        else:
            length = request.spec.length.value
            generated = secrets.token_hex((length + 1) // 2)[:length]
        return ProviderPresent(value=JsonString(value=generated))


__all__ = ("SecureGenerator",)
