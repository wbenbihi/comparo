"""Shared execution boundary for injected value providers."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from collections.abc import Coroutine
from typing import Any
from typing import cast

from comparo.core.domain.provenance import ResolutionSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import Error
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import ResolutionResult
from comparo.core.domain.values import ResolvedValue
from comparo.core.resolution.cache import SingleFlightCache
from comparo.core.resolution.control import RunCancellation
from comparo.core.resolution.models import ProviderBlocked
from comparo.core.resolution.models import ProviderError
from comparo.core.resolution.models import ProviderOutcome
from comparo.core.resolution.models import ProviderPresent
from comparo.core.resolution.models import ProviderUnavailable
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.provenance import blocked
from comparo.core.resolution.provenance import error
from comparo.core.resolution.provenance import present
from comparo.core.resolution.provenance import unavailable
from comparo.core.resolution.registry import SensitiveValueRegistry

_ACCESS = SensitiveValueAccess(purpose="value-resolution")
type ProviderResult = ResolutionResult[ResolvedValue]


class ProviderRuntime:
    """Normalize provider calls and outcomes without owning provider-specific policy."""

    __slots__ = ("_cancellation", "_registry")

    def __init__(
        self,
        cancellation: RunCancellation,
        registry: SensitiveValueRegistry,
    ) -> None:
        """Bind the provider boundary to one run's cancellation and secret registry."""
        self._cancellation = cancellation
        self._registry = registry

    async def call[KeyT](
        self,
        cache: SingleFlightCache[KeyT, ProviderOutcome],
        key: KeyT,
        operation: Callable[[], Coroutine[Any, Any, ProviderOutcome]],
        sensitive_origin: TaintOriginId | None = None,
    ) -> ProviderOutcome:
        """Execute one cached call and contain cancellation, exceptions, and invalid output."""
        if self._cancellation.cancelled:
            return ProviderError(code=ResolutionErrorCode.CANCELLED)

        async def guarded() -> ProviderOutcome:
            try:
                outcome = await operation()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - provider boundary becomes a value-free failure
                return ProviderError(code=ResolutionErrorCode.PROVIDER_FAILED)
            if type(outcome) not in {
                ProviderPresent,
                ProviderUnavailable,
                ProviderError,
                ProviderBlocked,
            }:
                return ProviderError(code=ResolutionErrorCode.PROVIDER_FAILED)
            if sensitive_origin is not None and type(outcome) is ProviderPresent:
                return ProviderPresent(value=seal(outcome.value, sensitive_origin))
            return outcome

        try:
            return await cache.get(key, guarded)
        except RuntimeError:
            return ProviderError(code=ResolutionErrorCode.CANCELLED)

    async def result(
        self,
        outcome: ProviderOutcome,
        *,
        source: ResolutionSource,
        target: ResolutionTarget,
    ) -> ProviderResult:
        """Map a value-free provider outcome to canonical resolution provenance."""
        if type(outcome) is ProviderPresent:
            resolved = present(
                outcome.value,
                symbol=target.symbol,
                scope=target.scope,
                source=source,
                namespace=target.namespace,
            )
            await self._registry.register(resolved, symbol=target.symbol, scope=target.scope)
            return resolved
        if type(outcome) is ProviderUnavailable:
            return unavailable(
                outcome.reason,
                symbol=target.symbol,
                scope=target.scope,
                source=source,
                namespace=target.namespace,
            )
        if type(outcome) is ProviderBlocked:
            return blocked(
                outcome.code,
                symbol=target.symbol,
                scope=target.scope,
                source=source,
                namespace=target.namespace,
            )
        return error(
            outcome.code,
            symbol=target.symbol,
            scope=target.scope,
            source=source,
            namespace=target.namespace,
        )

    @staticmethod
    def error(
        *,
        source: ResolutionSource,
        target: ResolutionTarget,
        code: ResolutionErrorCode,
    ) -> Error:
        """Build one canonical error at a provider-owned source boundary."""
        return error(
            code,
            symbol=target.symbol,
            scope=target.scope,
            source=source,
            namespace=target.namespace,
        )


def seal(
    value: ResolvedValue | SensitiveValue[ResolvedValue],
    origin: TaintOriginId,
) -> SensitiveValue[ResolvedValue]:
    """Add one stable taint origin while preserving existing lineage."""
    if is_sensitive_value(value):
        payload = cast("ResolvedValue", value.reveal(access=_ACCESS))
        if origin in value.origins:
            return cast("SensitiveValue[ResolvedValue]", value)
        origins = stable_taint_origin_union(value.origins, (origin,))
        return SensitiveValue(payload, origins=origins)
    return SensitiveValue(cast("ResolvedValue", value), origins=(origin,))


def reveal(value: SensitiveValue[ResolvedValue]) -> ResolvedValue:
    """Reveal only inside the resolution boundary for type validation or conversion."""
    return value.reveal(access=_ACCESS)


def taint_origin(prefix: str, symbol: SymbolPath) -> TaintOriginId:
    """Derive a stable, value-free taint identifier from a source coordinate."""
    digest = hashlib.sha256(".".join(symbol.segments).encode()).hexdigest()[:24]
    return TaintOriginId(value=f"{prefix}/{digest}")
