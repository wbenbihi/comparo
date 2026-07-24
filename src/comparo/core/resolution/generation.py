"""Deterministic scoped generation behind the runtime provider port."""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.resolution.models import GenerationOperation
from comparo.core.resolution.models import GenerationRequest
from comparo.core.resolution.models import ProviderBlocked
from comparo.core.resolution.models import ProviderOutcome
from comparo.core.resolution.models import ProviderPresent
from comparo.core.resolution.provenance import GENERATOR_PROVIDER

_ACCESS = SensitiveValueAccess(purpose="deterministic-generation")


class DeterministicGenerator:
    """Derive stable values from a sealed per-run seed and exact scope coordinates."""

    __slots__ = ("_seed",)

    def __init__(self, seed: SensitiveValue[BytesValue]) -> None:
        """Retain one non-empty sealed seed for the owning run context."""
        if not is_sensitive_value(seed) or seed.payload_type is not BytesValue:
            raise TypeError("deterministic generator seed must be sealed bytes")
        if not seed.reveal(access=_ACCESS).value:
            raise ValueError("deterministic generator seed must not be empty")
        self._seed: SensitiveValue[BytesValue] | None = seed

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return the stable built-in generator identity."""
        return GENERATOR_PROVIDER

    async def generate(self, request: GenerationRequest) -> ProviderOutcome:
        """Generate independently of scheduling order, sharing only declared coordinates."""
        if type(request) is not GenerationRequest:
            raise TypeError("deterministic generation requires a GenerationRequest")
        if request.spec.secure:
            return ProviderBlocked(code=ResolutionBlockCode.CAPABILITY_DENIED)
        seed = self._seed
        if seed is None:
            raise RuntimeError("deterministic generator is closed")
        material = "|".join(
            (
                request.spec.operation.value,
                ".".join(request.spec.symbol.segments),
                str(request.spec.length.value),
                _scope_coordinate(request.scope),
            )
        ).encode()
        generated = _hex_stream(
            seed.reveal(access=_ACCESS).value,
            material,
            request.spec.length.value,
        )
        if request.spec.operation is GenerationOperation.UUID:
            generated = str(UUID(hex=generated))
        return ProviderPresent(value=JsonString(value=generated))

    def close(self) -> None:
        """Drop the sealed seed reference when the run context closes."""
        self._seed = None


def _hex_stream(seed: bytes, material: bytes, length: int) -> str:
    chunks: list[str] = []
    counter = 0
    while sum(map(len, chunks)) < length:
        counter_bytes = counter.to_bytes(8, "big")
        chunks.append(hmac.new(seed, material + counter_bytes, hashlib.sha256).hexdigest())
        counter += 1
    return "".join(chunks)[:length]


def _scope_coordinate(scope: RuntimeScope) -> str:
    if type(scope) is RunScope:
        return f"run:{scope.run_id.value}"
    if type(scope) is CellScope:
        return f"cell:{scope.run.run_id.value}:{scope.cell_id.value}"
    if type(scope) is TrialScope:
        return f"trial:{_scope_coordinate(scope.cell)}:{scope.trial_id.value}"
    if type(scope) is EnvironmentScope:
        identity = scope.environment
        return f"env:{scope.run.run_id.value}:{identity.resource_id.value}"
    if type(scope) is ExecutionScope:
        return (
            f"execution:{_scope_coordinate(scope.trial)}:"
            f"{scope.environment.environment.resource_id.value}:{scope.execution_id.value}"
        )
    if type(scope) is StepScope:
        return f"step:{_scope_coordinate(scope.execution)}:{scope.step_execution_id.value}"
    if type(scope) is AttemptScope:
        step = "none" if scope.step_execution_id is None else str(scope.step_execution_id.value)
        return f"attempt:{_scope_coordinate(scope.execution)}:{step}:{scope.attempt_id.value}"
    raise TypeError("unsupported generation scope")
