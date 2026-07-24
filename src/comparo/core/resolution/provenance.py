"""Value-free provenance construction for the resolution engine."""

from __future__ import annotations

from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.provenance import ResolutionSource
from comparo.core.domain.provenance import ResolutionStep
from comparo.core.domain.provenance import ResolutionStepOutcome
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import Error
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import UnavailableReason

LITERAL_PROVIDER = ValueProviderIdentity(namespace="comparo", name="literal", version="1")
SENSITIVE_LITERAL_PROVIDER = ValueProviderIdentity(
    namespace="comparo", name="sensitive-literal", version="1"
)
ENV_PROVIDER = ValueProviderIdentity(namespace="comparo", name="env-snapshot", version="1")
MATRIX_PROVIDER = ValueProviderIdentity(namespace="comparo", name="matrix", version="1")
NAMESPACE_PROVIDER = ValueProviderIdentity(namespace="comparo", name="namespace", version="1")
FALLBACK_PROVIDER = ValueProviderIdentity(namespace="comparo", name="fallback", version="1")
OPTIONAL_PROVIDER = ValueProviderIdentity(namespace="comparo", name="optional", version="1")
CONVERSION_PROVIDER = ValueProviderIdentity(namespace="comparo", name="conversion", version="1")
COMPOSITE_PROVIDER = ValueProviderIdentity(namespace="comparo", name="composite", version="1")
INTERPOLATION_PROVIDER = ValueProviderIdentity(
    namespace="comparo", name="interpolation", version="1"
)
GENERATOR_PROVIDER = ValueProviderIdentity(namespace="comparo", name="generator", version="1")


def resolution_source(
    *,
    symbol: SymbolPath,
    provider: ValueProviderIdentity,
    configuration: ValueProvenance,
    scope: RuntimeScope,
    namespace: ValueNamespace | None,
) -> ResolutionSource:
    """Build one value-free source coordinate."""
    return ResolutionSource(
        symbol=symbol,
        provider=provider,
        configuration=configuration,
        scope=scope,
        namespace=namespace,
    )


def provenance(
    *,
    symbol: SymbolPath,
    scope: RuntimeScope,
    source: ResolutionSource,
    outcome: ResolutionStepOutcome,
    namespace: ValueNamespace | None,
    unavailable_steps: tuple[ResolutionStep, ...] = (),
) -> ResolutionProvenance:
    """Build a terminal result after zero or more unavailable fallback attempts."""
    return ResolutionProvenance(
        symbol=symbol,
        provider=source.provider,
        scope=scope,
        namespace=namespace,
        steps=(*unavailable_steps, ResolutionStep(source=source, outcome=outcome)),
    )


def present(
    value: ResolvedValue | SensitiveValue[ResolvedValue],
    *,
    symbol: SymbolPath,
    scope: RuntimeScope,
    source: ResolutionSource,
    namespace: ValueNamespace | None,
    unavailable_steps: tuple[ResolutionStep, ...] = (),
) -> Present[ResolvedValue]:
    """Build a present result with a matching terminal step."""
    return Present(
        value=value,
        provenance=provenance(
            symbol=symbol,
            scope=scope,
            source=source,
            outcome=ResolutionStepOutcome.PRESENT,
            namespace=namespace,
            unavailable_steps=unavailable_steps,
        ),
    )


def unavailable(
    reason: UnavailableReason,
    *,
    symbol: SymbolPath,
    scope: RuntimeScope,
    source: ResolutionSource,
    namespace: ValueNamespace | None,
    unavailable_steps: tuple[ResolutionStep, ...] = (),
) -> Unavailable:
    """Build an expected absence with a matching terminal step."""
    return Unavailable(
        reason=reason,
        provenance=provenance(
            symbol=symbol,
            scope=scope,
            source=source,
            outcome=ResolutionStepOutcome.UNAVAILABLE,
            namespace=namespace,
            unavailable_steps=unavailable_steps,
        ),
    )


def missing(
    *,
    symbol: SymbolPath,
    scope: RuntimeScope,
    source: ResolutionSource,
    namespace: ValueNamespace | None,
    unavailable_steps: tuple[ResolutionStep, ...] = (),
) -> Missing:
    """Build an intentional omission with a matching terminal step."""
    return Missing(
        provenance=provenance(
            symbol=symbol,
            scope=scope,
            source=source,
            outcome=ResolutionStepOutcome.MISSING,
            namespace=namespace,
            unavailable_steps=unavailable_steps,
        )
    )


def error(
    code: ResolutionErrorCode,
    *,
    symbol: SymbolPath,
    scope: RuntimeScope,
    source: ResolutionSource,
    namespace: ValueNamespace | None,
    unavailable_steps: tuple[ResolutionStep, ...] = (),
) -> Error:
    """Build a value-free failure with a matching terminal step."""
    return Error(
        code=code,
        provenance=provenance(
            symbol=symbol,
            scope=scope,
            source=source,
            outcome=ResolutionStepOutcome.ERROR,
            namespace=namespace,
            unavailable_steps=unavailable_steps,
        ),
    )


def blocked(
    code: ResolutionBlockCode,
    *,
    symbol: SymbolPath,
    scope: RuntimeScope,
    source: ResolutionSource,
    namespace: ValueNamespace | None,
    unavailable_steps: tuple[ResolutionStep, ...] = (),
) -> Blocked:
    """Build a value-free policy denial with a matching terminal step."""
    return Blocked(
        code=code,
        provenance=provenance(
            symbol=symbol,
            scope=scope,
            source=source,
            outcome=ResolutionStepOutcome.BLOCKED,
            namespace=namespace,
            unavailable_steps=unavailable_steps,
        ),
    )
