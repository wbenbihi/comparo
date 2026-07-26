"""Explicit live and masked egress views over one shared resolution engine."""

from __future__ import annotations

from typing import Protocol
from typing import cast
from typing import final
from typing import runtime_checkable

import msgspec

from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import is_resolved_value
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.resolver import ResolutionResultT

MASKED_VALUE_MARKER = "<masked>"


@final
class MaskedSensitiveValue(msgspec.Struct, frozen=True, kw_only=True):
    """A serializable value-free replacement for one sealed resolution result."""

    kind: ValueKind
    origins: tuple[TaintOriginId, ...]
    marker: str = MASKED_VALUE_MARKER

    def __post_init__(self) -> None:
        """Retain only a closed type category and safe sensitivity lineage."""
        if type(self.kind) is not ValueKind or self.kind is ValueKind.UNKNOWN:
            raise ValueError("masked sensitive value requires one exact resolved value kind")
        if (
            type(self.origins) is not tuple
            or not self.origins
            or any(type(item) is not TaintOriginId for item in self.origins)
        ):
            raise ValueError("masked sensitive value requires immutable typed origins")
        if len(self.origins) != len(set(self.origins)):
            raise ValueError("masked sensitive value origins must be unique")
        if self.marker != MASKED_VALUE_MARKER:
            raise ValueError("masked sensitive value marker is version-owned")


type MaskedResolvedValue = ResolvedValue | MaskedSensitiveValue


@final
class MaskedPresent(msgspec.Struct, frozen=True, kw_only=True):
    """A present resolution result whose sensitive payload cannot be recovered."""

    value: MaskedResolvedValue
    provenance: ResolutionProvenance

    def __post_init__(self) -> None:
        """Require either one public value or one irreversible masked placeholder."""
        if type(self.value) is not MaskedSensitiveValue and not is_resolved_value(self.value):
            raise TypeError("masked present value must be resolved or explicitly masked")
        if type(self.provenance) is not ResolutionProvenance:
            raise TypeError("masked present provenance must be ResolutionProvenance")

    @property
    def sensitivity(self) -> Sensitivity:
        """Report sensitivity without examining or retaining secret data."""
        if type(self.value) is MaskedSensitiveValue:
            return Sensitivity.SENSITIVE
        return Sensitivity.PUBLIC

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Return safe lineage identifiers for a masked result."""
        if type(self.value) is MaskedSensitiveValue:
            return self.value.origins
        return ()


type MaskedResolutionResult = MaskedPresent | Unavailable | Missing | Error | Blocked


@runtime_checkable
class ResolutionEngine(Protocol):
    """The single shared engine contract consumed by both egress views."""

    async def resolve(
        self,
        expression: ValueExpression,
        target: ResolutionTarget,
    ) -> ResolutionResultT:
        """Resolve one expression against an exact runtime target."""
        ...


@final
class LiveValueResolver:
    """Authorized execution view retaining sealed live values for runtime consumers."""

    __slots__ = ("_engine",)

    def __init__(self, engine: ResolutionEngine) -> None:
        """Bind to the shared run-owned resolution engine."""
        if not isinstance(engine, ResolutionEngine):
            raise TypeError("live resolver requires a ResolutionEngine")
        self._engine = engine

    async def resolve(
        self,
        expression: ValueExpression,
        target: ResolutionTarget,
    ) -> ResolutionResultT:
        """Return the engine result, including sealed sensitive values."""
        return await self._engine.resolve(expression, target)


@final
class MaskedValueResolver:
    """Inspection view irreversibly replacing every sensitive result at egress."""

    __slots__ = ("_engine",)

    def __init__(self, engine: ResolutionEngine) -> None:
        """Bind to the same run-owned engine used by live execution."""
        if not isinstance(engine, ResolutionEngine):
            raise TypeError("masked resolver requires a ResolutionEngine")
        self._engine = engine

    async def resolve(
        self,
        expression: ValueExpression,
        target: ResolutionTarget,
    ) -> MaskedResolutionResult:
        """Resolve normally, then remove sealed payloads before returning to the caller."""
        return mask_resolution(await self._engine.resolve(expression, target))


def mask_resolution(result: ResolutionResultT) -> MaskedResolutionResult:
    """Project one live result to a serializable value-free sensitive view."""
    if type(result) is not Present:
        return result
    value = result.value
    if not is_sensitive_value(value):
        return MaskedPresent(value=cast("ResolvedValue", value), provenance=result.provenance)
    return MaskedPresent(
        value=MaskedSensitiveValue(
            kind=_resolved_value_kind(value.payload_type),
            origins=value.origins,
        ),
        provenance=result.provenance,
    )


def _resolved_value_kind(value_type: type[object]) -> ValueKind:
    kinds: dict[type[object], ValueKind] = {
        JsonNull: ValueKind.NULL,
        JsonBoolean: ValueKind.BOOLEAN,
        JsonInteger: ValueKind.INTEGER,
        JsonDecimal: ValueKind.NUMBER,
        JsonString: ValueKind.STRING,
        JsonArray: ValueKind.ARRAY,
        JsonObject: ValueKind.OBJECT,
        BytesValue: ValueKind.BYTES,
        Duration: ValueKind.DURATION,
        ByteCount: ValueKind.SIZE,
    }
    try:
        return kinds[value_type]
    except KeyError as error:  # pragma: no cover - SensitiveValue validates the closed union
        raise TypeError("unsupported masked resolved value type") from error


__all__ = (
    "MASKED_VALUE_MARKER",
    "LiveValueResolver",
    "MaskedPresent",
    "MaskedResolutionResult",
    "MaskedSensitiveValue",
    "MaskedValueResolver",
    "mask_resolution",
)
